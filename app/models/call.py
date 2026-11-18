"""Call lifecycle, API response, webhook, and transcript Pydantic models."""

from datetime import datetime, timezone
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field, field_validator

from app.utils.validators import normalize_optional_text, normalize_phone_number


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


class CallStatus(str, Enum):
    """Tracked outbound call lifecycle states."""

    INITIATED = "initiated"
    RINGING = "ringing"
    ANSWERED = "answered"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    FAILED = "failed"
    BUSY = "busy"
    UNANSWERED = "unanswered"
    CANCELED = "canceled"


class CallInitiateRequest(BaseModel):
    """Validated request payload to initiate an outbound call."""

    phone_number: str = Field(..., description="Target PG owner phone number")
    owner_name: str | None = Field(default=None, max_length=120)
    pg_name: str | None = Field(default=None, max_length=160)
    campaign_id: str | None = Field(default=None, max_length=80)
    notes: str | None = Field(default=None, max_length=500)

    @field_validator("phone_number")
    @classmethod
    def validate_phone(cls, value: str) -> str:
        return normalize_phone_number(value)

    @field_validator("owner_name", "pg_name", "campaign_id", "notes", mode="before")
    @classmethod
    def clean_optional_strings(cls, value: str | None) -> str | None:
        return normalize_optional_text(value)


class CallEventEntry(BaseModel):
    """Audit entry for a provider or workflow status event."""

    event_key: str = Field(..., description="Unique key for duplicate webhook protection")
    source: str = Field(..., description="Event source, e.g., backend, vobiz, dograh")
    status: CallStatus
    raw_status: str | None = Field(default=None)
    timestamp: datetime = Field(default_factory=_utc_now)
    details: dict[str, Any] = Field(default_factory=dict)


class CallRecord(BaseModel):
    """Persisted record representing an outbound call attempt and its lifecycle."""

    call_id: str = Field(..., description="Unique internal call identifier, e.g., CALL_0001")
    provider_call_id: str | None = Field(default=None)
    workflow_session_id: str | None = Field(default=None)
    campaign_id: str | None = Field(default=None)
    phone_number: str
    owner_name: str | None = Field(default=None)
    pg_name: str | None = Field(default=None)
    notes: str | None = Field(default=None)
    status: CallStatus = Field(default=CallStatus.INITIATED)
    duration_seconds: int | None = Field(default=None, ge=0)
    failure_reason: str | None = Field(default=None)
    events: list[CallEventEntry] = Field(default_factory=list)
    processed_event_keys: list[str] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=_utc_now)
    updated_at: datetime = Field(default_factory=_utc_now)

    @field_validator("phone_number")
    @classmethod
    def validate_phone(cls, value: str) -> str:
        return normalize_phone_number(value)


class CallInitiateResponse(BaseModel):
    """Response returned when an outbound call is successfully dispatched."""

    call_id: str
    status: CallStatus
    phone_number: str
    workflow_run_id: str | None = None
    provider_call_id: str | None = None
    mocked: bool = False
    message: str


class CallWebhookPayload(BaseModel):
    """Incoming call lifecycle or workflow completion webhook payload."""

    call_id: str | None = Field(default=None, description="Internal CALL_XXXX identifier")
    workflow_run_id: str | None = Field(default=None, description="Dograh workflow_run_id")
    provider_call_id: str | None = Field(default=None, description="Telephony provider call ID")
    event_id: str | None = Field(default=None, description="Unique webhook event ID")
    source: str = Field(default="dograh", max_length=40)
    status: str = Field(..., description="Raw lifecycle status from Dograh or telephony")
    duration_seconds: int | None = Field(default=None, ge=0)
    failure_reason: str | None = Field(default=None, max_length=500)
    timestamp: datetime | None = Field(default=None)
    transcript: str | None = Field(default=None)
    transcript_url: str | None = Field(default=None)
    recording_url: str | None = Field(default=None)
    initial_context: dict[str, Any] = Field(default_factory=dict)
    gathered_context: dict[str, Any] = Field(default_factory=dict)


class CallWebhookResponse(BaseModel):
    """Response returned after processing a call status webhook."""

    received: bool = True
    duplicate: bool = False
    call_id: str
    status: CallStatus


class TranscriptTurn(BaseModel):
    """Single conversational utterance between agent and callee."""

    speaker: str = Field(..., description="e.g., agent or owner")
    text: str
    timestamp: datetime | None = Field(default=None)


class TranscriptRecord(BaseModel):
    """Conversation transcript stored separately from structured lead data."""

    call_id: str
    phone_number: str | None = Field(default=None)
    language: str | None = Field(default=None)
    turns: list[TranscriptTurn] = Field(default_factory=list)
    raw_transcript: str | None = Field(default=None)
    created_at: datetime = Field(default_factory=_utc_now)
    updated_at: datetime = Field(default_factory=_utc_now)