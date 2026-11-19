"""Tests for application startup, configuration, and health endpoint."""

from fastapi.testclient import TestClient

from app.config import Settings, get_settings
from app.main import create_app


def test_health_endpoint_returns_healthy(tmp_path, monkeypatch):
    """Verify /health reports healthy when storage directories are writable."""
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("APP_ENV", "test")
    get_settings.cache_clear()

    app = create_app()
    with TestClient(app) as client:
        response = client.get("/health")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "healthy"
    assert body["environment"] == "test"
    assert body["storage"] == {
        "leads_dir": True,
        "calls_dir": True,
        "transcripts_dir": True,
        "batches_dir": True,
    }
    assert "timestamp" in body

    get_settings.cache_clear()


def test_secret_masking_in_settings():
    """Verify configured secrets are collected for log masking."""
    settings = Settings(
        api_auth_key="super-secret-auth-key",
        groq_api_key="gsk_test_secret_1234",
    )
    secrets = settings.get_secret_values()
    assert "super-secret-auth-key" in secrets
    assert "gsk_test_secret_1234" in secrets