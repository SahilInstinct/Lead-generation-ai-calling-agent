"""Vobiz telephony status mapping and configuration verification.

Note: In this deployment, Vobiz is configured inside Dograh's Telephony Settings,
so Dograh places outbound calls via Vobiz directly. This service validates
configuration state and normalizes Vobiz/telephony call lifecycle statuses.
"""

from typing import Any

from app.config import Settings
from app.models.call import CallStatus


_STATUS_MAP: dict[str, CallStatus] = {
    "initiated": CallStatus.INITIATED,
    "queued": CallStatus.INITIATED,
    "created": CallStatus.INITIATED,
    "ringing": CallStatus.RINGING,
    "answered": CallStatus.ANSWERED,
    "in-progress": CallStatus.IN_PROGRESS,
    "in_progress": CallStatus.IN_PROGRESS,
    "ongoing": CallStatus.IN_PROGRESS,
    "completed": CallStatus.COMPLETED,
    "finished": CallStatus.COMPLETED,
    "ended": CallStatus.COMPLETED,
    "hangup": CallStatus.COMPLETED,
    "busy": CallStatus.BUSY,
    "no-answer": CallStatus.UNANSWERED,
    "no_answer": CallStatus.UNANSWERED,
    "unanswered": CallStatus.UNANSWERED,
    "timeout": CallStatus.UNANSWERED,
    "failed": CallStatus.FAILED,
    "error": CallStatus.FAILED,
    "rejected": CallStatus.FAILED,
    "canceled": CallStatus.CANCELED,
    "cancelled": CallStatus.CANCELED,
}


class VobizService:
    """Handles Vobiz integration mode checks and status normalization."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def map_status(self, raw_status: str | None) -> CallStatus:
        """Map a Vobiz or Dograh telephony status string to canonical CallStatus."""
        if not raw_status:
            return CallStatus.FAILED
        normalized = raw_status.strip().lower()
        return _STATUS_MAP.get(normalized, CallStatus.FAILED)

    def get_status_summary(self) -> dict[str, Any]:
        """Return safe, non-secret status of Vobiz configuration."""
        return {
            "mode": self.settings.vobiz_mode,
            "managed_by_dograh": self.settings.vobiz_mode == "dograh_managed",
            "caller_id_configured": bool(self.settings.vobiz_caller_id),
            "auth_id_configured": bool(self.settings.vobiz_auth_id),
        }