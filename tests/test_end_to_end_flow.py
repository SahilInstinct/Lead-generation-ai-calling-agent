
##### File 2 of 2 → `apkapg-ai-calling\tests\test_end_to_end_flow.py`
"""Phase 6 end-to-end mocked verification across all calling, outcome, and export paths."""

import csv
import io
import json

from fastapi.testclient import TestClient
import httpx
import pytest

from app.api.auth import get_calling_service
from app.config import get_settings
from app.main import create_app
from app.services.calling_service import CallingService
from app.services.dograh_service import DograhService
from app.services.extraction_service import ExtractionService
from app.services.vobiz_service import VobizService
from app.storage.csv_storage import LEAD_CSV_COLUMNS, LeadCsvStorage
from app.storage.json_storage import (
    CallJsonStorage,
    LeadJsonStorage,
    TranscriptJsonStorage,
)


@pytest.fixture
def e2e_env(tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("MOCK_PROVIDERS", "true")
    monkeypatch.setenv("API_AUTH_KEY", "e2e-api-key")
    monkeypatch.setenv("DOGRAH_WEBHOOK_SECRET", "e2e-webhook-secret")
    get_settings.cache_clear()
    yield tmp_path
    get_settings.cache_clear()


def test_full_mocked_flow_all_outcomes_and_exports(e2e_env):
    tmp_path = e2e_env
    app = create_app()
    api_headers = {"X-API-Key": "e2e-api-key"}
    wh_headers = {"Authorization": "Bearer e2e-webhook-secret"}

    with TestClient(app) as client:
        # 1. Health & system status
        assert client.get("/health").status_code == 200
        assert client.get("/api/system/status").status_code == 200

        # 2. Interested owner lifecycle: initiate -> answered -> completed + duplicate replay
        c1 = client.post(
            "/api/calls/initiate",
            headers=api_headers,
            json={
                "phone_number": "9876500001",
                "owner_name": "Arun Sharma",
                "pg_name": "Royal Stay PG",
                "campaign_id": "phase6-test",
            },
        )
        assert c1.status_code == 201
        call_1 = c1.json()["call"]["call_id"]

        assert (
            client.post(
                "/api/calls/webhook",
                headers={**wh_headers, "X-Dograh-Delivery-Id": "e2e-1a"},
                json={"call_id": call_1, "status": "answered"},
            ).status_code
            == 200
        )

        interested_payload = {
            "call_id": call_1,
            "status": "completed",
            "duration_seconds": 135,
            "gathered_context": {
                "call_disposition": "interested",
                "owner_role": "Owner",
                "contact_number_confirmed": True,
                "city": "Bengaluru",
                "locality": "HSR Layout",
                "total_rooms": 20,
                "available_rooms": 4,
                "interested": True,
                "listing_free_explained": True,
                "follow_up_required": True,
                "follow_up_day": "Wednesday",
                "follow_up_time": "4:00 PM",
                "communication_consent": True,
                "do_not_call": False,
                "objections": "Asked about verification",
                "additional_questions": "When will tenants see my PG?",
            },
            "turns": [
                {"speaker": "agent", "text": "Hello Arun Sharma ji, calling from ApkaPG."},
                {"speaker": "owner", "text": "Yes, interested in free listing."},
            ],
        }
        w1 = client.post(
            "/api/calls/webhook",
            headers={**wh_headers, "X-Dograh-Delivery-Id": "e2e-1b"},
            json=interested_payload,
        )
        w1_dup = client.post(
            "/api/calls/webhook",
            headers={**wh_headers, "X-Dograh-Delivery-Id": "e2e-1b"},
            json=interested_payload,
        )
        assert w1.status_code == 200
        assert w1.json()["duplicate"] is False
        assert w1_dup.status_code == 200
        assert w1_dup.json()["duplicate"] is True

        # 3. Not-interested owner
        c2 = client.post(
            "/api/calls/initiate",
            headers=api_headers,
            json={"phone_number": "9876500002", "campaign_id": "phase6-test"},
        )
        call_2 = c2.json()["call"]["call_id"]
        w2 = client.post(
            "/api/calls/webhook",
            headers={**wh_headers, "X-Dograh-Delivery-Id": "e2e-2"},
            json={
                "call_id": call_2,
                "status": "completed",
                "duration_seconds": 30,
                "gathered_context": {
                    "call_disposition": "not_interested",
                    "interested": False,
                    "listing_free_explained": True,
                },
            },
        )
        assert w2.json()["outcome"] == "not_interested"

        # 4. Callback requested
        c3 = client.post(
            "/api/calls/initiate",
            headers=api_headers,
            json={"phone_number": "9876500003", "campaign_id": "phase6-test"},
        )
        call_3 = c3.json()["call"]["call_id"]
        w3 = client.post(
            "/api/calls/webhook",
            headers={**wh_headers, "X-Dograh-Delivery-Id": "e2e-3"},
            json={
                "call_id": call_3,
                "status": "completed",
                "duration_seconds": 25,
                "gathered_context": {
                    "call_disposition": "callback_requested",
                    "follow_up_required": True,
                    "follow_up_day": "Thursday",
                    "follow_up_time": "11:00 AM",
                },
            },
        )
        assert w3.json()["outcome"] == "callback_requested"

        # 5. Opt-out & subsequent dial blocked
        c4 = client.post(
            "/api/calls/initiate",
            headers=api_headers,
            json={"phone_number": "9876500004", "campaign_id": "phase6-test"},
        )
        call_4 = c4.json()["call"]["call_id"]
        w4 = client.post(
            "/api/calls/webhook",
            headers={**wh_headers, "X-Dograh-Delivery-Id": "e2e-4"},
            json={
                "call_id": call_4,
                "status": "completed",
                "duration_seconds": 15,
                "gathered_context": {
                    "call_disposition": "opt_out",
                    "do_not_call": True,
                },
            },
        )
        assert w4.json()["outcome"] == "opt_out"

        blocked = client.post(
            "/api/calls/initiate",
            headers=api_headers,
            json={"phone_number": "9876500004"},
        )
        assert blocked.status_code == 409

        # 6. Unanswered call
        c5 = client.post(
            "/api/calls/initiate",
            headers=api_headers,
            json={"phone_number": "9876500005", "campaign_id": "phase6-test"},
        )
        call_5 = c5.json()["call"]["call_id"]
        w5 = client.post(
            "/api/calls/webhook",
            headers={**wh_headers, "X-Dograh-Delivery-Id": "e2e-5"},
            json={"call_id": call_5, "status": "no-answer", "duration_seconds": 0},
        )
        assert w5.json()["status"] == "unanswered"
        assert w5.json()["outcome"] == "call_not_connected"

        # Verify Exports
        json_export = client.get("/api/leads/export/json")
        assert json_export.status_code == 200
        exported_json = json.loads(json_export.text)
        assert len(exported_json) == 5

        csv_export = client.get("/api/leads/export/csv")
        assert csv_export.status_code == 200
        reader = csv.DictReader(io.StringIO(csv_export.text))
        assert tuple(reader.fieldnames or []) == LEAD_CSV_COLUMNS
        csv_rows = list(reader)
        assert len(csv_rows) == 5

    # Verify raw storage files on disk are all valid and consistent
    calls_on_disk = json.loads(
        (tmp_path / "data" / "calls" / "call_history.json").read_text(encoding="utf-8")
    )
    leads_on_disk = json.loads(
        (tmp_path / "data" / "leads" / "leads.json").read_text(encoding="utf-8")
    )
    transcripts_on_disk = json.loads(
        (tmp_path / "data" / "transcripts" / "call_transcripts.json").read_text(
            encoding="utf-8"
        )
    )
    assert len(calls_on_disk) == 5
    assert len(leads_on_disk) == 5
    assert len(transcripts_on_disk) == 1


def test_failed_provider_call_is_recorded_honestly(tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("MOCK_PROVIDERS", "false")
    monkeypatch.setenv("API_AUTH_KEY", "e2e-api-key")
    monkeypatch.setenv("DOGRAH_API_BASE_URL", "http://127.0.0.1:8000")
    monkeypatch.setenv("DOGRAH_API_KEY", "dgr_test")
    monkeypatch.setenv("DOGRAH_WORKFLOW_UUID", "11111111-1111-4111-8111-111111111111")
    get_settings.cache_clear()

    def failing_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(502, json={"detail": "Vobiz upstream failure"})

    app = create_app()
    settings = get_settings()

    def override_calling_service() -> CallingService:
        return CallingService(
            settings=settings,
            call_storage=CallJsonStorage(settings.calls_history_file),
            lead_storage=LeadJsonStorage(settings.leads_json_file),
            dograh_service=DograhService(
                settings=settings,
                transport=httpx.MockTransport(failing_handler),
            ),
            vobiz_service=VobizService(settings=settings),
            lead_csv_storage=LeadCsvStorage(settings.leads_csv_file),
            transcript_storage=TranscriptJsonStorage(settings.transcripts_json_file),
            extraction_service=ExtractionService(),
        )

    app.dependency_overrides[get_calling_service] = override_calling_service
    try:
        with TestClient(app) as client:
            resp = client.post(
                "/api/calls/initiate",
                headers={"X-API-Key": "e2e-api-key"},
                json={"phone_number": "9876500099"},
            )
            assert resp.status_code == 502
    finally:
        app.dependency_overrides.clear()
        get_settings.cache_clear()

    # Verify failed call was still persisted in call_history.json, leads.json, and leads.csv
    calls = json.loads(
        (tmp_path / "data" / "calls" / "call_history.json").read_text(encoding="utf-8")
    )
    leads = json.loads(
        (tmp_path / "data" / "leads" / "leads.json").read_text(encoding="utf-8")
    )
    assert len(calls) == 1
    assert calls[0]["status"] == "failed"
    assert "Vobiz upstream failure" in (calls[0]["failure_reason"] or "")
    assert len(leads) == 1
    assert leads[0]["outcome"] == "call_not_connected"