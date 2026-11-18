"""Tests for the built-in operator dashboard UI and localhost-only backend routes."""

from fastapi import HTTPException
from fastapi.testclient import TestClient
import pytest
from starlette.requests import Request

from app.api.ui import require_local_dashboard_origin
from app.config import get_settings
from app.main import create_app


def test_dashboard_root_and_no_key_initiate_sync(tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("MOCK_PROVIDERS", "true")
    monkeypatch.setenv("API_AUTH_KEY", "strict-secret-key")
    get_settings.cache_clear()

    app = create_app()
    with TestClient(app) as client:
        # HTML dashboard loads with no secrets embedded
        home = client.get("/")
        assert home.status_code == 200
        assert "ApkaPG AI Outbound Calling Console" in home.text
        assert "strict-secret-key" not in home.text
        assert "/ui/calls/initiate" in home.text

        # External API still enforces X-API-Key
        api_unauth = client.post(
            "/api/calls/initiate",
            json={"phone_number": "9876543210"},
        )
        assert api_unauth.status_code == 401

        # Local dashboard route works without X-API-Key
        ui_init = client.post(
            "/ui/calls/initiate",
            json={"phone_number": "9876543210"},
        )
        assert ui_init.status_code == 201
        call_id = ui_init.json()["call"]["call_id"]

        ui_sync = client.post(f"/ui/calls/{call_id}/sync")
        assert ui_sync.status_code == 200
        assert ui_sync.json()["call_id"] == call_id

    get_settings.cache_clear()


def test_dashboard_actions_block_non_local_clients():
    remote_request = Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/ui/calls/initiate",
            "headers": [],
            "client": ("192.168.1.50", 54321),
        }
    )
    with pytest.raises(HTTPException) as exc_info:
        require_local_dashboard_origin(remote_request)
    assert exc_info.value.status_code == 403