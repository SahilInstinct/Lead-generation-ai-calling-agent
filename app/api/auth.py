"""Authentication dependencies and service factories for API routes."""

import hmac

from fastapi import Depends, Header, HTTPException, status

from app.config import Settings, get_settings
from app.services.calling_service import CallingService
from app.services.dograh_service import DograhService
from app.services.export_service import ExportService
from app.services.extraction_service import ExtractionService
from app.services.vobiz_service import VobizService
from app.storage.csv_storage import LeadCsvStorage
from app.storage.json_storage import (
    CallJsonStorage,
    LeadJsonStorage,
    TranscriptJsonStorage,
)


def require_api_key(
    x_api_key: str | None = Header(default=None, alias="X-API-Key"),
    settings: Settings = Depends(get_settings),
) -> None:
    """Validate X-API-Key on sensitive calling endpoints when API_AUTH_KEY is configured."""
    expected = settings.api_auth_key.strip()
    if not expected:
        if settings.app_env == "production":
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Server API_AUTH_KEY is not configured.",
            )
        return

    if not x_api_key or not hmac.compare_digest(x_api_key, expected):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or missing X-API-Key.",
        )


def verify_webhook_secret(
    authorization: str | None = Header(default=None),
    x_webhook_secret: str | None = Header(default=None, alias="X-Webhook-Secret"),
    settings: Settings = Depends(get_settings),
) -> None:
    """Verify Bearer token or X-Webhook-Secret when DOGRAH_WEBHOOK_SECRET is configured."""
    expected = settings.dograh_webhook_secret.strip()
    if not expected:
        return

    bearer_token = ""
    if authorization and authorization.lower().startswith("bearer "):
        bearer_token = authorization[7:].strip()

    candidate = bearer_token or (x_webhook_secret or "").strip()
    if not candidate or not hmac.compare_digest(candidate, expected):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or missing webhook authentication.",
        )


def get_calling_service(
    settings: Settings = Depends(get_settings),
) -> CallingService:
    """Construct CallingService with JSON/CSV storage, Dograh, Vobiz, and extraction."""
    return CallingService(
        settings=settings,
        call_storage=CallJsonStorage(settings.calls_history_file),
        lead_storage=LeadJsonStorage(settings.leads_json_file),
        dograh_service=DograhService(settings=settings),
        vobiz_service=VobizService(settings=settings),
        lead_csv_storage=LeadCsvStorage(settings.leads_csv_file),
        transcript_storage=TranscriptJsonStorage(settings.transcripts_json_file),
        extraction_service=ExtractionService(),
    )


def get_export_service(
    settings: Settings = Depends(get_settings),
) -> ExportService:
    """Construct ExportService backed by local LeadJsonStorage."""
    return ExportService(lead_storage=LeadJsonStorage(settings.leads_json_file))