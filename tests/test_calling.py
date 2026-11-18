"""Tests for Phase 3 outbound call initiation, Dograh integration, and webhooks."""

import json

from fastapi.testclient import TestClient
import httpx
import pytest

from app.config import Settings, get_settings
from app.main import create_app
from app.models.lead import LeadOutcome, LeadRecord
from app.services.dograh_service import DograhService
from app.storage.json_storage import LeadJsonStorage


@pytest.fixture
def configured_client(tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("MOCK_PROVIDERS", "true")
    monkeypatch.setenv("API_AUTH_KEY", "test-api-key")
    monkeypatch.setenv("DOGRAH_WEBHOOK_SECRET", "test-webhook-secret")
    get_settings.cache_clear()

    app = create_app()
    with TestClient(app) as client:
        yield client, tmp_path

    get_settings.cache_clear()


def test_initiate_call_requires_api_key(configured_client):
    client, _ = configured_client
    resp = client.post("/api/calls/initiate", json={"phone_number": "9876543210"})
    assert resp.status_code == 401


def test_mock_call_initiation_and_lifecycle_webhooks(configured_client):
    client, tmp_path = configured_client
    headers = {"X-API-Key": "test-api-key"}

    init_resp = client.post(
        "/api/calls/initiate",
        headers=headers,
        json={
            "phone_number": "9876543210",
            "owner_name": "Ramesh",
            "pg_name": "Green View PG",
            "campaign_id": "blr-nov",
        },
    )
    assert init_resp.status_code == 201
    call = init_resp.json()["call"]
    assert call["call_id"] == "CALL_0001"
    assert call["phone_number"] == "+919876543210"
    assert call["status"] == "ringing"

    # Webhook: answered
    wh_headers = {"Authorization": "Bearer test-webhook-secret"}
    wh_1 = client.post(
        "/api/calls/webhook",
        headers={**wh_headers, "X-Dograh-Delivery-Id": "deliv-1"},
        json={"call_id": "CALL_0001", "status": "answered"},
    )
    assert wh_1.status_code == 200
    assert wh_1.json()["status"] == "answered"
    assert wh_1.json()["duplicate"] is False

    # Webhook: completed + duplicate replay
    completed_payload = {
        "call_id": "CALL_0001",
        "status": "completed",
        "duration_seconds": 84,
    }
    wh_2 = client.post(
        "/api/calls/webhook",
        headers={**wh_headers, "X-Dograh-Delivery-Id": "deliv-2"},
        json=completed_payload,
    )
    wh_2_dup = client.post(
        "/api/calls/webhook",
        headers={**wh_headers, "X-Dograh-Delivery-Id": "deliv-2"},
        json=completed_payload,
    )
    assert wh_2.status_code == 200
    assert wh_2.json()["duplicate"] is False
    assert wh_2_dup.status_code == 200
    assert wh_2_dup.json()["duplicate"] is True

    # Verify call detail & persistence
    detail_resp = client.get("/api/calls/CALL_0001")
    assert detail_resp.status_code == 200
    detail = detail_resp.json()
    assert detail["status"] == "completed"
    assert detail["duration_seconds"] == 84
    assert len(detail["events"]) == 4  # initiated, ringing, answered, completed


def test_opt_out_phone_is_blocked_before_dialing(configured_client):
    client, tmp_path = configured_client
    lead_store = LeadJsonStorage(tmp_path / "data" / "leads" / "leads.json")
    lead_store.upsert_lead(
        LeadRecord(
            call_id="CALL_0000",
            phone_number="+919876543299",
            do_not_call=True,
            outcome=LeadOutcome.OPT_OUT,
        )
    )

    resp = client.post(
        "/api/calls/initiate",
        headers={"X-API-Key": "test-api-key"},
        json={"phone_number": "9876543299"},
    )
    assert resp.status_code == 409


def test_webhook_rejects_bad_secret(configured_client):
    client, _ = configured_client
    resp = client.post(
        "/api/calls/webhook",
        headers={"Authorization": "Bearer wrong-secret"},
        json={"call_id": "CALL_0001", "status": "completed"},
    )
    assert resp.status_code == 401


@pytest.mark.anyio
async def test_dograh_service_triggers_expected_endpoint_and_records_run_id():
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["method"] = request.method
        captured["url"] = str(request.url)
        captured["api_key"] = request.headers.get("X-API-Key")
        captured["body"] = json.loads(request.content.decode("utf-8"))
        return httpx.Response(
            200,
            json={
                "status": "initiated",
                "workflow_run_id": 9876,
                "workflow_run_name": "WR-API-9876",
            },
        )

    settings = Settings(
        mock_providers=False,
        dograh_api_base_url="http://127.0.0.1:8000",
        dograh_api_key="dgr_test_key_123",
        dograh_workflow_uuid="11111111-1111-4111-8111-111111111111",
    )
    service = DograhService(settings=settings, transport=httpx.MockTransport(handler))
    result = await service.trigger_outbound_call(
        phone_number="+919876543210",
        initial_context={"call_id": "CALL_0001", "owner_name": "Ramesh"},
    )

    assert captured["method"] == "POST"
    assert (
        captured["url"]
        == "http://127.0.0.1:8000/api/v1/public/agent/workflow/11111111-1111-4111-8111-111111111111"
    )
    assert captured["api_key"] == "dgr_test_key_123"
    assert captured["body"]["phone_number"] == "+919876543210"
    assert result.workflow_run_id == "9876"


def test_system_status_endpoint(configured_client):
    client, _ = configured_client
    resp = client.get("/api/system/status")
    assert resp.status_code == 200
    body = resp.json()
    assert body["mock_providers"] is True
    assert body["vobiz"]["mode"] == "dograh_managed"