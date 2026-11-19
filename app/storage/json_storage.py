"""Safe JSON persistence for call history, leads, transcripts, and batches."""

from datetime import datetime, timezone
import json
from pathlib import Path
import re
from typing import Any

from app.models.batch import BatchItemRecord, BatchRecord, BatchStatus
from app.models.call import (
    CallEventEntry,
    CallInitiateRequest,
    CallRecord,
    CallStatus,
    TranscriptRecord,
    TranscriptTurn,
)
from app.models.lead import LeadRecord
from app.storage.file_lock import StorageError, atomic_write_text, file_lock
from app.utils.validators import normalize_phone_number


_CALL_ID_PATTERN = re.compile(r"^CALL_(\d+)$")
_BATCH_ID_PATTERN = re.compile(r"^BATCH_(\d+)$")


def _read_json_list_unlocked(file_path: Path) -> list[dict[str, Any]]:
    """Read a JSON array from disk; returns [] if missing or empty."""
    if not file_path.exists():
        return []

    raw = file_path.read_text(encoding="utf-8").strip()
    if not raw:
        return []

    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise StorageError(
            f"Corrupted JSON detected in '{file_path}': {exc}"
        ) from exc

    if not isinstance(data, list):
        raise StorageError(
            f"Expected JSON array in '{file_path}', found {type(data).__name__}."
        )
    return data


def _write_json_list_unlocked(file_path: Path, items: list[dict[str, Any]]) -> None:
    """Serialize list as formatted JSON and write atomically."""
    serialized = json.dumps(items, indent=2, ensure_ascii=False, default=str) + "\n"
    atomic_write_text(file_path, serialized, encoding="utf-8")


class CallJsonStorage:
    """JSON repository for outbound call records (data/calls/call_history.json)."""

    def __init__(self, file_path: Path) -> None:
        self.file_path = file_path

    def _next_call_id(self, records: list[dict[str, Any]]) -> str:
        max_num = 0
        for item in records:
            call_id = str(item.get("call_id", ""))
            match = _CALL_ID_PATTERN.match(call_id)
            if match:
                max_num = max(max_num, int(match.group(1)))
        return f"CALL_{max_num + 1:04d}"

    def create_call(
        self,
        request: CallInitiateRequest,
        provider_call_id: str | None = None,
        workflow_session_id: str | None = None,
    ) -> CallRecord:
        """Create and persist a new call record with a unique sequential call_id."""
        with file_lock(self.file_path):
            raw_items = _read_json_list_unlocked(self.file_path)
            call_id = self._next_call_id(raw_items)
            now = datetime.now(timezone.utc)

            initial_event = CallEventEntry(
                event_key=f"{call_id}:backend:initiated",
                source="backend",
                status=CallStatus.INITIATED,
                raw_status="initiated",
                timestamp=now,
            )

            record = CallRecord(
                call_id=call_id,
                provider_call_id=provider_call_id,
                workflow_session_id=workflow_session_id,
                campaign_id=request.campaign_id,
                phone_number=request.phone_number,
                owner_name=request.owner_name,
                pg_name=request.pg_name,
                notes=request.notes,
                status=CallStatus.INITIATED,
                events=[initial_event],
                processed_event_keys=[initial_event.event_key],
                created_at=now,
                updated_at=now,
            )

            raw_items.append(record.model_dump(mode="json"))
            _write_json_list_unlocked(self.file_path, raw_items)
            return record

    def update_call(self, record: CallRecord) -> CallRecord:
        """Update an existing call record by call_id."""
        with file_lock(self.file_path):
            raw_items = _read_json_list_unlocked(self.file_path)
            for idx, item in enumerate(raw_items):
                if item.get("call_id") == record.call_id:
                    record.updated_at = datetime.now(timezone.utc)
                    raw_items[idx] = record.model_dump(mode="json")
                    _write_json_list_unlocked(self.file_path, raw_items)
                    return record

        raise StorageError(f"Call record '{record.call_id}' not found.")

    def record_event(
        self,
        call_id: str,
        event: CallEventEntry,
        duration_seconds: int | None = None,
        failure_reason: str | None = None,
        provider_call_id: str | None = None,
        workflow_session_id: str | None = None,
    ) -> tuple[CallRecord, bool]:
        """Apply a call lifecycle event idempotently."""
        with file_lock(self.file_path):
            raw_items = _read_json_list_unlocked(self.file_path)
            for idx, item in enumerate(raw_items):
                if item.get("call_id") != call_id:
                    continue

                record = CallRecord.model_validate(item)
                if event.event_key in record.processed_event_keys:
                    return record, False

                record.events.append(event)
                record.processed_event_keys.append(event.event_key)
                record.status = event.status
                if duration_seconds is not None:
                    record.duration_seconds = duration_seconds
                if failure_reason is not None:
                    record.failure_reason = failure_reason
                if provider_call_id is not None:
                    record.provider_call_id = provider_call_id
                if workflow_session_id is not None:
                    record.workflow_session_id = workflow_session_id
                record.updated_at = datetime.now(timezone.utc)

                raw_items[idx] = record.model_dump(mode="json")
                _write_json_list_unlocked(self.file_path, raw_items)
                return record, True

        raise StorageError(f"Call record '{call_id}' not found.")

    def get_by_call_id(self, call_id: str) -> CallRecord | None:
        """Fetch a single call record by call_id."""
        with file_lock(self.file_path):
            raw_items = _read_json_list_unlocked(self.file_path)
            for item in raw_items:
                if item.get("call_id") == call_id:
                    return CallRecord.model_validate(item)
        return None

    def get_by_provider_call_id(self, provider_call_id: str) -> CallRecord | None:
        """Fetch a single call record by external provider_call_id."""
        with file_lock(self.file_path):
            raw_items = _read_json_list_unlocked(self.file_path)
            for item in raw_items:
                if item.get("provider_call_id") == provider_call_id:
                    return CallRecord.model_validate(item)
        return None

    def list_calls(self) -> list[CallRecord]:
        """Return all persisted call records ordered by creation time descending."""
        with file_lock(self.file_path):
            raw_items = _read_json_list_unlocked(self.file_path)
        records = [CallRecord.model_validate(item) for item in raw_items]
        records.sort(key=lambda r: r.created_at, reverse=True)
        return records


class LeadJsonStorage:
    """JSON repository for structured lead records (data/leads/leads.json)."""

    def __init__(self, file_path: Path) -> None:
        self.file_path = file_path

    def upsert_lead(self, lead: LeadRecord) -> LeadRecord:
        """Insert a new lead or update an existing lead matching call_id."""
        with file_lock(self.file_path):
            raw_items = _read_json_list_unlocked(self.file_path)
            now = datetime.now(timezone.utc)

            for idx, item in enumerate(raw_items):
                if item.get("call_id") == lead.call_id:
                    existing = LeadRecord.model_validate(item)
                    lead.created_at = existing.created_at
                    lead.updated_at = now
                    raw_items[idx] = lead.model_dump(mode="json")
                    _write_json_list_unlocked(self.file_path, raw_items)
                    return lead

            lead.updated_at = now
            raw_items.append(lead.model_dump(mode="json"))
            _write_json_list_unlocked(self.file_path, raw_items)
            return lead

    def get_by_call_id(self, call_id: str) -> LeadRecord | None:
        """Fetch a single lead record by call_id."""
        with file_lock(self.file_path):
            raw_items = _read_json_list_unlocked(self.file_path)
            for item in raw_items:
                if item.get("call_id") == call_id:
                    return LeadRecord.model_validate(item)
        return None

    def list_leads(self) -> list[LeadRecord]:
        """Return all persisted lead records ordered by creation time descending."""
        with file_lock(self.file_path):
            raw_items = _read_json_list_unlocked(self.file_path)
        records = [LeadRecord.model_validate(item) for item in raw_items]
        records.sort(key=lambda r: r.created_at, reverse=True)
        return records

    def is_phone_opted_out(self, phone_number: str) -> bool:
        """Check whether a phone number has an existing do_not_call opt-out record."""
        normalized = normalize_phone_number(phone_number)
        with file_lock(self.file_path):
            raw_items = _read_json_list_unlocked(self.file_path)
            for item in raw_items:
                if item.get("phone_number") == normalized and bool(item.get("do_not_call")):
                    return True
        return False


class TranscriptJsonStorage:
    """JSON repository for conversation transcripts (data/transcripts/call_transcripts.json)."""

    def __init__(self, file_path: Path) -> None:
        self.file_path = file_path

    def upsert_transcript(self, transcript: TranscriptRecord) -> TranscriptRecord:
        """Insert or update transcript for a call_id."""
        with file_lock(self.file_path):
            raw_items = _read_json_list_unlocked(self.file_path)
            now = datetime.now(timezone.utc)

            for idx, item in enumerate(raw_items):
                if item.get("call_id") == transcript.call_id:
                    existing = TranscriptRecord.model_validate(item)
                    transcript.created_at = existing.created_at
                    transcript.updated_at = now
                    raw_items[idx] = transcript.model_dump(mode="json")
                    _write_json_list_unlocked(self.file_path, raw_items)
                    return transcript

            transcript.updated_at = now
            raw_items.append(transcript.model_dump(mode="json"))
            _write_json_list_unlocked(self.file_path, raw_items)
            return transcript

    def get_by_call_id(self, call_id: str) -> TranscriptRecord | None:
        """Fetch transcript for a given call_id."""
        with file_lock(self.file_path):
            raw_items = _read_json_list_unlocked(self.file_path)
            for item in raw_items:
                if item.get("call_id") == call_id:
                    return TranscriptRecord.model_validate(item)
        return None


class BatchJsonStorage:
    """JSON repository for sequential calling batches (data/batches/batches.json)."""

    def __init__(self, file_path: Path) -> None:
        self.file_path = file_path

    def _next_batch_id(self, records: list[dict[str, Any]]) -> str:
        max_num = 0
        for item in records:
            batch_id = str(item.get("batch_id", ""))
            match = _BATCH_ID_PATTERN.match(batch_id)
            if match:
                max_num = max(max_num, int(match.group(1)))
        return f"BATCH_{max_num + 1:04d}"

    def create_batch(
        self,
        items: list[BatchItemRecord],
        campaign_id: str | None = None,
        delay_seconds: int = 3,
    ) -> BatchRecord:
        """Create and persist a new batch record."""
        with file_lock(self.file_path):
            raw_items = _read_json_list_unlocked(self.file_path)
            batch_id = self._next_batch_id(raw_items)
            now = datetime.now(timezone.utc)

            record = BatchRecord(
                batch_id=batch_id,
                campaign_id=campaign_id,
                status=BatchStatus.PENDING,
                delay_seconds=delay_seconds,
                items=items,
                created_at=now,
                updated_at=now,
            )
            raw_items.append(record.model_dump(mode="json"))
            _write_json_list_unlocked(self.file_path, raw_items)
            return record

    def save_batch(self, batch: BatchRecord) -> BatchRecord:
        """Persist updates to an existing batch record."""
        with file_lock(self.file_path):
            raw_items = _read_json_list_unlocked(self.file_path)
            for idx, item in enumerate(raw_items):
                if item.get("batch_id") == batch.batch_id:
                    batch.updated_at = datetime.now(timezone.utc)
                    raw_items[idx] = batch.model_dump(mode="json")
                    _write_json_list_unlocked(self.file_path, raw_items)
                    return batch

        raise StorageError(f"Batch '{batch.batch_id}' not found.")

    def get_by_batch_id(self, batch_id: str) -> BatchRecord | None:
        """Fetch a single batch by batch_id."""
        with file_lock(self.file_path):
            raw_items = _read_json_list_unlocked(self.file_path)
            for item in raw_items:
                if item.get("batch_id") == batch_id:
                    return BatchRecord.model_validate(item)
        return None

    def list_batches(self) -> list[BatchRecord]:
        """Return all persisted batches ordered newest first."""
        with file_lock(self.file_path):
            raw_items = _read_json_list_unlocked(self.file_path)
        records = [BatchRecord.model_validate(item) for item in raw_items]
        records.sort(key=lambda r: r.created_at, reverse=True)
        return records