"""Structured lead and transcript extraction from Dograh workflow results."""

from datetime import datetime, timezone
import re
from typing import Any

from app.models.call import CallRecord, CallStatus, TranscriptRecord, TranscriptTurn
from app.models.lead import LeadOutcome, LeadRecord
from app.utils.validators import normalize_optional_text, normalize_phone_number


_PLACEHOLDER_VALUES = {
    "",
    "null",
    "none",
    "nil",
    "n/a",
    "na",
    "unknown",
    "not provided",
    "not mentioned",
    "not asked",
    "not available",
    "undefined",
}

_NON_CONNECTED_CALL_STATUSES = {
    CallStatus.FAILED.value,
    CallStatus.BUSY.value,
    CallStatus.UNANSWERED.value,
    CallStatus.CANCELED.value,
    "no-answer",
    "no_answer",
    "timeout",
    "rejected",
    "error",
    "cancelled",
}


class ExtractionService:
    """Normalizes Dograh gathered_context & call metadata into strict LeadRecord objects."""

    @staticmethod
    def _clean_text(value: Any) -> str | None:
        if value is None:
            return None
        text = normalize_optional_text(str(value))
        if text is None:
            return None
        if text.lower() in _PLACEHOLDER_VALUES:
            return None
        return text

    @classmethod
    def _parse_optional_bool(cls, value: Any) -> bool | None:
        if isinstance(value, bool):
            return value
        if value is None:
            return None
        cleaned = cls._clean_text(value)
        if cleaned is None:
            return None
        lowered = cleaned.lower()
        if lowered in {"true", "yes", "1", "y", "confirmed", "agreed"}:
            return True
        if lowered in {"false", "no", "0", "n", "declined", "refused"}:
            return False
        return None

    @classmethod
    def _parse_bool_flag(cls, value: Any, default: bool = False) -> bool:
        parsed = cls._parse_optional_bool(value)
        return parsed if parsed is not None else default

    @classmethod
    def _parse_optional_non_negative_int(
        cls,
        value: Any,
        field_label: str,
        notes_accumulator: list[str],
    ) -> int | None:
        if value is None:
            return None
        if isinstance(value, bool):
            return None
        if isinstance(value, int):
            return value if value >= 0 else None
        if isinstance(value, float):
            if value >= 0 and value.is_integer():
                return int(value)
            return None

        cleaned = cls._clean_text(value)
        if cleaned is None:
            return None

        if re.fullmatch(r"\d+(\.0+)?", cleaned):
            return int(float(cleaned))

        notes_accumulator.append(f"{field_label}: {cleaned}")
        return None

    @classmethod
    def _parse_string_list(cls, value: Any) -> list[str]:
        if value is None:
            return []
        if isinstance(value, list):
            items: list[str] = []
            for entry in value:
                cleaned = cls._clean_text(entry)
                if cleaned:
                    items.append(cleaned)
            return items

        cleaned = cls._clean_text(value)
        if cleaned is None:
            return []

        parts = [part.strip() for part in re.split(r"[;\n]+", cleaned)]
        return [part for part in parts if part and part.lower() not in _PLACEHOLDER_VALUES]

    @classmethod
    def _determine_outcome(
        cls,
        disposition: str | None,
        interested: bool | None,
        follow_up_required: bool,
        do_not_call: bool,
        call_status: str | None,
    ) -> LeadOutcome:
        normalized_disp = (disposition or "").strip().lower()

        if do_not_call or normalized_disp in {"opt_out", "opt-out", "do_not_call", "dnc"}:
            return LeadOutcome.OPT_OUT
        if normalized_disp in {"wrong_number", "wrong-number"}:
            return LeadOutcome.WRONG_NUMBER
        if (
            call_status in _NON_CONNECTED_CALL_STATUSES
            and interested is None
            and not follow_up_required
            and not normalized_disp
        ):
            return LeadOutcome.CALL_NOT_CONNECTED
        if interested is True or normalized_disp == "interested":
            return LeadOutcome.INTERESTED
        if interested is False or normalized_disp in {"not_interested", "not-interested"}:
            return LeadOutcome.NOT_INTERESTED
        if follow_up_required or normalized_disp in {"callback", "callback_requested", "busy"}:
            return LeadOutcome.CALLBACK_REQUESTED
        if normalized_disp == "unsure":
            return LeadOutcome.UNSURE
        if call_status in _NON_CONNECTED_CALL_STATUSES:
            return LeadOutcome.CALL_NOT_CONNECTED
        return LeadOutcome.UNKNOWN

    def extract_lead(
        self,
        call_id: str,
        payload: dict[str, Any],
        call_record: CallRecord | None = None,
    ) -> LeadRecord:
        """Build a validated LeadRecord from Dograh webhook/run data without inventing fields."""
        initial_ctx = payload.get("initial_context")
        if not isinstance(initial_ctx, dict):
            initial_ctx = {}

        gathered_ctx = payload.get("gathered_context")
        if not isinstance(gathered_ctx, dict):
            gathered_ctx = {}

        notes_parts: list[str] = []

        owner_name = (
            self._clean_text(gathered_ctx.get("owner_name"))
            or self._clean_text(initial_ctx.get("owner_name"))
            or (call_record.owner_name if call_record else None)
        )
        owner_role = self._clean_text(gathered_ctx.get("owner_role"))
        pg_name = (
            self._clean_text(gathered_ctx.get("pg_name"))
            or self._clean_text(initial_ctx.get("pg_name"))
            or (call_record.pg_name if call_record else None)
        )
        campaign_id = (
            self._clean_text(payload.get("campaign_id"))
            or self._clean_text(initial_ctx.get("campaign_id"))
            or (call_record.campaign_id if call_record else None)
        )

        raw_phone_candidates = [
            gathered_ctx.get("confirmed_phone_number"),
            gathered_ctx.get("phone_number"),
            initial_ctx.get("phone_number"),
            payload.get("phone_number"),
            call_record.phone_number if call_record else None,
        ]
        resolved_phone: str | None = None
        for candidate in raw_phone_candidates:
            cleaned = self._clean_text(candidate)
            if not cleaned:
                continue
            try:
                resolved_phone = normalize_phone_number(cleaned)
                break
            except ValueError:
                if candidate is gathered_ctx.get("confirmed_phone_number"):
                    notes_parts.append(f"Spoken contact note: {cleaned}")

        total_rooms = self._parse_optional_non_negative_int(
            gathered_ctx.get("total_rooms"),
            "Total rooms",
            notes_parts,
        )
        available_rooms = self._parse_optional_non_negative_int(
            gathered_ctx.get("available_rooms"),
            "Available rooms",
            notes_parts,
        )

        interested = self._parse_optional_bool(gathered_ctx.get("interested"))
        do_not_call = self._parse_bool_flag(gathered_ctx.get("do_not_call"), default=False)
        follow_up_required = self._parse_bool_flag(
            gathered_ctx.get("follow_up_required"), default=False
        )
        communication_consent = self._parse_optional_bool(
            gathered_ctx.get("communication_consent")
        )
        contact_number_confirmed = self._parse_bool_flag(
            gathered_ctx.get("contact_number_confirmed"), default=False
        )
        listing_free_explained = self._parse_bool_flag(
            gathered_ctx.get("listing_free_explained"), default=False
        )

        disposition = self._clean_text(gathered_ctx.get("call_disposition"))
        if disposition and disposition.lower() in {"opt_out", "opt-out", "do_not_call"}:
            do_not_call = True
            if interested is None:
                interested = False
            if communication_consent is None:
                communication_consent = False

        if do_not_call:
            follow_up_required = False

        call_status = (
            (call_record.status.value if call_record else None)
            or self._clean_text(payload.get("status"))
            or self._clean_text(payload.get("call_status"))
        )
        if call_status:
            call_status = call_status.lower()

        duration_seconds = self._parse_optional_non_negative_int(
            payload.get("duration_seconds")
            or payload.get("call_duration_seconds")
            or (call_record.duration_seconds if call_record else None),
            "Call duration",
            notes_parts,
        )

        extracted_notes = self._clean_text(gathered_ctx.get("additional_notes"))
        if extracted_notes:
            notes_parts.insert(0, extracted_notes)
        combined_notes = " | ".join(notes_parts) if notes_parts else None

        outcome = self._determine_outcome(
            disposition=disposition,
            interested=interested,
            follow_up_required=follow_up_required,
            do_not_call=do_not_call,
            call_status=call_status,
        )

        return LeadRecord(
            call_id=call_id,
            campaign_id=campaign_id,
            owner_name=owner_name,
            owner_role=owner_role,
            pg_name=pg_name,
            phone_number=resolved_phone,
            contact_number_confirmed=contact_number_confirmed,
            city=self._clean_text(gathered_ctx.get("city")),
            locality=self._clean_text(gathered_ctx.get("locality")),
            total_rooms=total_rooms,
            available_rooms=available_rooms,
            interested=interested,
            listing_free_explained=listing_free_explained,
            follow_up_required=follow_up_required,
            follow_up_day=self._clean_text(gathered_ctx.get("follow_up_day")),
            follow_up_time=self._clean_text(gathered_ctx.get("follow_up_time")),
            communication_consent=communication_consent,
            do_not_call=do_not_call,
            objections=self._parse_string_list(gathered_ctx.get("objections")),
            additional_questions=self._parse_string_list(
                gathered_ctx.get("additional_questions")
            ),
            additional_notes=combined_notes,
            outcome=outcome,
            call_status=call_status,
            call_duration_seconds=duration_seconds,
        )

    def extract_transcript(
        self,
        call_id: str,
        payload: dict[str, Any],
        phone_number: str | None = None,
    ) -> TranscriptRecord | None:
        """Extract transcript turns or raw transcript text if supplied in payload."""
        raw_turns = payload.get("turns") or payload.get("transcript_turns")
        turns: list[TranscriptTurn] = []

        if isinstance(raw_turns, list):
            for entry in raw_turns:
                if not isinstance(entry, dict):
                    continue
                speaker = self._clean_text(entry.get("speaker") or entry.get("role"))
                text = self._clean_text(entry.get("text") or entry.get("content"))
                if speaker and text:
                    turns.append(TranscriptTurn(speaker=speaker, text=text))

        raw_transcript = self._clean_text(
            payload.get("raw_transcript") or payload.get("transcript")
        )
        transcript_url = self._clean_text(payload.get("transcript_url"))
        if not raw_transcript and transcript_url:
            raw_transcript = f"Transcript URL: {transcript_url}"

        if not turns and not raw_transcript:
            return None

        return TranscriptRecord(
            call_id=call_id,
            phone_number=phone_number,
            language=self._clean_text(payload.get("language")),
            turns=turns,
            raw_transcript=raw_transcript,
            updated_at=datetime.now(timezone.utc),
        )