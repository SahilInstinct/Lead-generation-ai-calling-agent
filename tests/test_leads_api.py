"""Tests for Phase 5 webhook-to-lead persistence, exports, and edge cases."""

import csv
import io
import json

from fastapi.testclient import TestClient
import pytest

from app.config import get_settings
from app.main import create_app


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("MOCK_PROVIDERS", "true")
    monkeypatch.setenv("API_AUTH_KEY", "test-api-key")
    monkeypatch.setenv("DOGRAH_WEBHOOK_SECRET", "test-webhook-secret")
    get_settings.cache_clear()

    app = create_app()
    with TestClient(app) as test_client:
        yield test_client, tmp_path

    get_settings.cache_clear()


def test_end_to_end_mocked_outcomes_and_exports(client):
    test_client, tmp_path = client
    api_headers = {"X-API-Key": "test-api-key"}
    wh_headers = {"X-Webhook-Secret": "test-webhook-secret"}

    # 1. Initiate call 1 -> Interested lead + transcript
    r1 = test_client.post(
        "/api/calls/initiate",
        headers=api_headers,
        json={"phone_number": "9876543210", "owner_name": "Kiran", "campaign_id": "camp-a"},
    )
    assert r1.status_code == 201
    call_1 = r1.json()["call"]["call_id"]

    wh1 = test_client.post(
        "/api/calls/webhook",
        headers={**wh_headers, "X-Dograh-Delivery-Id": "d-101"},
        json={
            "call_id": call_1,
            "status": "completed",
            "duration_seconds": 95,
            "gathered_context": {
                "call_disposition": "interested",
                "owner_role": "Owner",
                "pg_name": "Metro Nest PG",
                "contact_number_confirmed": True,
                "city": "Pune",
                "locality": "Hinjewadi",
                "total_rooms": 18,
                "available_rooms": 3,
                "interested": True,
                "listing_free_explained": True,
                "follow_up_required": True,
                "follow_up_day": "Monday",
                "follow_up_time": "11 AM",
                "communication_consent": True,
                "do_not_call": False,
            },
            "turns": [
                {"speaker": "agent", "text": "Hello Kiran, calling from ApkaPG."},
                {"speaker": "owner", "text": "Yes, interested."},
            ],
        },
    )
    assert wh1.status_code == 200
    assert wh1.json()["lead_saved"] is True
    assert wh1.json()["outcome"] == "interested"

    # Simulate a bare telephony hangup webhook arriving AFTER extraction
    # It must NOT wipe out the already-extracted PG details.
    wh1_bare = test_client.post(
        "/api/calls/webhook",
        headers={**wh_headers, "X-Dograh-Delivery-Id": "d-102"},
        json={"call_id": call_1, "status": "completed", "duration_seconds": 96},
    )
    assert wh1_bare.status_code == 200

    # 2. Initiate call 2 -> Opt-out
    r2 = test_client.post(
        "/api/calls/initiate",
        headers=api_headers,
        json={"phone_number": "9876543211"},
    )
    call_2 = r2.json()["call"]["call_id"]
    wh2 = test_client.post(
        "/api/calls/webhook",
        headers={**wh_headers, "X-Dograh-Delivery-Id": "d-201"},
        json={
            "call_id": call_2,
            "status": "completed",
            "duration_seconds": 18,
            "gathered_context": {
                "call_disposition": "opt_out",
                "do_not_call": True,
            },
        },
    )
    assert wh2.status_code == 200
    assert wh2.json()["outcome"] == "opt_out"

    # Verify Lead API filtering and detail
    all_leads = test_client.get("/api/leads")
    assert all_leads.status_code == 200
    assert all_leads.json()["count"] == 2

    interested_only = test_client.get("/api/leads?outcome=interested")
    assert interested_only.status_code == 200
    assert interested_only.json()["count"] == 1
    assert interested_only.json()["leads"][0]["pg_name"] == "Metro Nest PG"
    assert interested_only.json()["leads"][0]["call_duration_seconds"] == 96

    lead_detail = test_client.get(f"/api/leads/{call_1}")
    assert lead_detail.status_code == 200
    assert lead_detail.json()["city"] == "Pune"

    # Verify JSON export
    json_export = test_client.get("/api/leads/export/json")
    assert json_export.status_code == 200
    exported_list = json.loads(json_export.text)
    assert len(exported_list) == 2

    # Verify CSV export
    csv_export = test_client.get("/api/leads/export/csv")
    assert csv_export.status_code == 200
    rows = list(csv.DictReader(io.StringIO(csv_export.text)))
    assert len(rows) == 2
    assert any(r["pg_name"] == "Metro Nest PG" for r in rows)

    # Verify transcript file persisted
    transcripts = json.loads(
        (tmp_path / "data" / "transcripts" / "call_transcripts.json").read_text(
            encoding="utf-8"
        )
    )
    assert len(transcripts) == 1
    assert transcripts[0]["call_id"] == call_1


def test_get_webhook_returns_405_not_call_not_found(client):
    test_client, _ = client
    resp = test_client.get("/api/calls/webhook")
    assert resp.status_code == 405