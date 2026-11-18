"""Webhook endpoint for receiving Dograh/Vobiz call lifecycle and extraction events."""

from typing import Any

from fastapi import APIRouter, Depends, Header, HTTPException, status
from pydantic import BaseModel

from app.api.auth import get_calling_service, verify_webhook_secret
from app.config import Settings, get_settings
from app.services.calling_service import CallNotFoundError, CallingService
from app.storage.json_storage import LeadJsonStorage

router = APIRouter(prefix="/api/calls", tags=["Webhooks"])


class WebhookAckResponse(BaseModel):
    """Acknowledgement returned to Dograh/telephony webhooks."""

    received: bool
    call_id: str
    status: str
    duplicate: bool
    lead_saved: bool
    outcome: str | None = None


@router.get("/webhook", include_in_schema=False)
async def webhook_method_not_allowed() -> None:
    """Return 405 if /api/calls/webhook is requested with GET (e.g. from a browser)."""
    raise HTTPException(
        status_code=status.HTTP_405_METHOD_NOT_ALLOWED,
        detail="Method Not Allowed. Send POST requests to /api/calls/webhook.",
        headers={"Allow": "POST"},
    )


@router.post(
    "/webhook",
    response_model=WebhookAckResponse,
    dependencies=[Depends(verify_webhook_secret)],
)
async def receive_call_webhook(
    payload: dict[str, Any],
    x_dograh_delivery_id: str | None = Header(default=None, alias="X-Dograh-Delivery-Id"),
    service: CallingService = Depends(get_calling_service),
    settings: Settings = Depends(get_settings),
) -> WebhookAckResponse:
    """Receive call status and extraction events idempotently."""
    try:
        record, applied = service.process_status_webhook(
            payload=payload,
            delivery_id=x_dograh_delivery_id,
        )
    except CallNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc

    saved_lead = LeadJsonStorage(settings.leads_json_file).get_by_call_id(record.call_id)
    return WebhookAckResponse(
        received=True,
        call_id=record.call_id,
        status=record.status.value,
        duplicate=not applied,
        lead_saved=applied and saved_lead is not None,
        outcome=saved_lead.outcome.value if saved_lead else None,
    )