"""Structured lead record model matching extracted call information."""

from datetime import datetime, timezone
from enum import Enum

from pydantic import BaseModel, Field, field_validator

from app.utils.validators import normalize_optional_text, normalize_phone_number


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


class LeadOutcome(str, Enum):
    """Explicit classification of the call's lead collection outcome."""

    INTERESTED = "interested"
    NOT_INTERESTED = "not_interested"
    CALLBACK_REQUESTED = "callback_requested"
    OPT_OUT = "opt_out"
    UNSURE = "unsure"
    WRONG_NUMBER = "wrong_number"
    CALL_NOT_CONNECTED = "call_not_connected"
    UNKNOWN = "unknown"


class LeadRecord(BaseModel):
    """Structured lead record saved locally in JSON and CSV.

    Fields not collected during a call remain None (null in JSON).
    """

    call_id: str
    campaign_id: str | None = Field(default=None)
    owner_name: str | None = Field(default=None)
    owner_role: str | None = Field(default=None)
    pg_name: str | None = Field(default=None)
    phone_number: str | None = Field(default=None)
    contact_number_confirmed: bool = Field(default=False)
    city: str | None = Field(default=None)
    locality: str | None = Field(default=None)
    total_rooms: int | None = Field(default=None, ge=0)
    available_rooms: int | None = Field(default=None, ge=0)
    interested: bool | None = Field(default=None)
    listing_free_explained: bool = Field(default=False)
    follow_up_required: bool = Field(default=False)
    follow_up_day: str | None = Field(default=None)
    follow_up_time: str | None = Field(default=None)
    communication_consent: bool | None = Field(default=None)
    do_not_call: bool = Field(default=False)
    objections: list[str] = Field(default_factory=list)
    additional_questions: list[str] = Field(default_factory=list)
    additional_notes: str | None = Field(default=None)
    outcome: LeadOutcome = Field(default=LeadOutcome.UNKNOWN)
    call_status: str | None = Field(default=None)
    call_duration_seconds: int | None = Field(default=None, ge=0)
    created_at: datetime = Field(default_factory=_utc_now)
    updated_at: datetime = Field(default_factory=_utc_now)

    @field_validator("phone_number")
    @classmethod
    def validate_optional_phone(cls, value: str | None) -> str | None:
        if value is None:
            return None
        stripped = value.strip()
        if not stripped:
            return None
        return normalize_phone_number(stripped)

    @field_validator(
        "campaign_id",
        "owner_name",
        "owner_role",
        "pg_name",
        "city",
        "locality",
        "follow_up_day",
        "follow_up_time",
        "additional_notes",
        "call_status",
        mode="before",
    )
    @classmethod
    def clean_optional_strings(cls, value: str | None) -> str | None:
        return normalize_optional_text(value)