"""CSV/list parsing and sequential one-by-one outbound batch calling runner."""

import asyncio
import csv
from datetime import datetime, timezone
import io

from app.config import Settings
from app.models.batch import (
    BatchCreateRequest,
    BatchItemRecord,
    BatchItemStatus,
    BatchRecord,
    BatchStatus,
)
from app.models.call import CallInitiateRequest, CallStatus
from app.services.calling_service import (
    CallingService,
    OptOutBlockedError,
    _TERMINAL_CALL_STATUSES,
)
from app.services.dograh_service import DograhError, DograhService
from app.services.vobiz_service import VobizService
from app.storage.csv_storage import LeadCsvStorage
from app.storage.json_storage import (
    BatchJsonStorage,
    CallJsonStorage,
    LeadJsonStorage,
    TranscriptJsonStorage,
)
from app.utils.logger import get_logger
from app.utils.validators import normalize_optional_text, normalize_phone_number

logger = get_logger(__name__)

_PHONE_HEADERS = {"phone_number", "phone", "mobile", "number", "contact", "contact_number"}
_OWNER_HEADERS = {"owner_name", "owner", "name", "contact_name"}
_PG_HEADERS = {"pg_name", "pg", "property_name", "property"}
_NOTES_HEADERS = {"notes", "note", "remarks"}


class BatchNotFoundError(Exception):
    """Raised when a batch_id does not exist."""


class BatchValidationError(Exception):
    """Raised when an uploaded batch contains no valid contacts."""


def parse_contacts_from_text(raw_content: str) -> list[BatchItemRecord]:
    """Parse CSV (with or without headers) or newline-separated numbers into BatchItemRecords."""
    text = raw_content.strip()
    if not text:
        raise BatchValidationError("Contact list cannot be empty.")

    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if not lines:
        raise BatchValidationError("Contact list contains no rows.")

    first_row = next(csv.reader([lines[0]]))
    normalized_headers = [col.strip().lower().replace(" ", "_") for col in first_row]
    has_header = any(col in _PHONE_HEADERS for col in normalized_headers)

    items: list[BatchItemRecord] = []
    seen_phones: set[str] = set()

    if has_header:
        reader = csv.DictReader(io.StringIO(text))
        for idx, row in enumerate(reader, start=1):
            lowered = {
                (k or "").strip().lower().replace(" ", "_"): (v or "").strip()
                for k, v in row.items()
            }
            raw_phone = next(
                (lowered[h] for h in _PHONE_HEADERS if lowered.get(h)),
                "",
            )
            owner_name = normalize_optional_text(
                next((lowered[h] for h in _OWNER_HEADERS if lowered.get(h)), None)
            )
            pg_name = normalize_optional_text(
                next((lowered[h] for h in _PG_HEADERS if lowered.get(h)), None)
            )
            notes = normalize_optional_text(
                next((lowered[h] for h in _NOTES_HEADERS if lowered.get(h)), None)
            )
            items.append(
                _build_item(
                    row_number=idx,
                    raw_phone=raw_phone,
                    owner_name=owner_name,
                    pg_name=pg_name,
                    notes=notes,
                    seen_phones=seen_phones,
                )
            )
    else:
        for idx, line in enumerate(lines, start=1):
            cols = [c.strip() for c in next(csv.reader([line]))]
            raw_phone = cols[0] if len(cols) > 0 else ""
            owner_name = normalize_optional_text(cols[1]) if len(cols) > 1 else None
            pg_name = normalize_optional_text(cols[2]) if len(cols) > 2 else None
            notes = normalize_optional_text(cols[3]) if len(cols) > 3 else None
            items.append(
                _build_item(
                    row_number=idx,
                    raw_phone=raw_phone,
                    owner_name=owner_name,
                    pg_name=pg_name,
                    notes=notes,
                    seen_phones=seen_phones,
                )
            )

    if not items:
        raise BatchValidationError("No contact rows found in input.")
    if not any(item.status == BatchItemStatus.PENDING for item in items):
        raise BatchValidationError(
            "No valid phone numbers found. Check the format and try again."
        )
    return items


def _build_item(
    row_number: int,
    raw_phone: str,
    owner_name: str | None,
    pg_name: str | None,
    notes: str | None,
    seen_phones: set[str],
) -> BatchItemRecord:
    try:
        normalized = normalize_phone_number(raw_phone)
    except ValueError as exc:
        return BatchItemRecord(
            row_number=row_number,
            raw_phone=raw_phone,
            owner_name=owner_name,
            pg_name=pg_name,
            notes=notes,
            status=BatchItemStatus.INVALID,
            error=str(exc),
        )

    if normalized in seen_phones:
        return BatchItemRecord(
            row_number=row_number,
            raw_phone=raw_phone,
            phone_number=normalized,
            owner_name=owner_name,
            pg_name=pg_name,
            notes=notes,
            status=BatchItemStatus.SKIPPED_DUPLICATE,
            error="Duplicate phone number in the same batch.",
        )

    seen_phones.add(normalized)
    return BatchItemRecord(
        row_number=row_number,
        raw_phone=raw_phone,
        phone_number=normalized,
        owner_name=owner_name,
        pg_name=pg_name,
        notes=notes,
        status=BatchItemStatus.PENDING,
    )


class BatchRunnerManager:
    """Manages sequential one-by-one background batch execution across the app lifecycle."""

    def __init__(
        self,
        poll_interval_seconds: float = 2.0,
        max_call_wait_seconds: float = 600.0,
        mock_call_duration_seconds: float = 1.0,
    ) -> None:
        self.poll_interval_seconds = poll_interval_seconds
        self.max_call_wait_seconds = max_call_wait_seconds
        self.mock_call_duration_seconds = mock_call_duration_seconds
        self._tasks: dict[str, asyncio.Task] = {}
        self._pause_events: dict[str, asyncio.Event] = {}
        self._stop_flags: set[str] = set()

    def _build_services(
        self, settings: Settings
    ) -> tuple[BatchJsonStorage, CallJsonStorage, LeadJsonStorage, CallingService]:
        call_storage = CallJsonStorage(settings.calls_history_file)
        lead_storage = LeadJsonStorage(settings.leads_json_file)
        batch_storage = BatchJsonStorage(settings.batches_json_file)
        calling_service = CallingService(
            settings=settings,
            call_storage=call_storage,
            lead_storage=lead_storage,
            dograh_service=DograhService(settings=settings),
            vobiz_service=VobizService(settings=settings),
            lead_csv_storage=LeadCsvStorage(settings.leads_csv_file),
            transcript_storage=TranscriptJsonStorage(settings.transcripts_json_file),
        )
        return batch_storage, call_storage, lead_storage, calling_service

    def create_batch(
        self,
        settings: Settings,
        request: BatchCreateRequest,
    ) -> BatchRecord:
        """Parse contacts, persist batch, and optionally start sequential runner."""
        items = parse_contacts_from_text(request.csv_content)
        batch_storage = BatchJsonStorage(settings.batches_json_file)
        batch = batch_storage.create_batch(
            items=items,
            campaign_id=request.campaign_id,
            delay_seconds=request.delay_seconds,
        )
        if request.auto_start:
            self.start_batch(settings=settings, batch_id=batch.batch_id)
            refreshed = batch_storage.get_by_batch_id(batch.batch_id)
            return refreshed or batch
        return batch

    def start_batch(self, settings: Settings, batch_id: str) -> BatchRecord:
        """Start background sequential execution for a pending batch."""
        batch_storage = BatchJsonStorage(settings.batches_json_file)
        batch = batch_storage.get_by_batch_id(batch_id)
        if batch is None:
            raise BatchNotFoundError(f"Batch '{batch_id}' not found.")

        existing_task = self._tasks.get(batch_id)
        if existing_task and not existing_task.done():
            return batch

        self._stop_flags.discard(batch_id)
        pause_event = asyncio.Event()
        pause_event.set()
        self._pause_events[batch_id] = pause_event

        batch.status = BatchStatus.RUNNING
        batch.started_at = batch.started_at or datetime.now(timezone.utc)
        batch_storage.save_batch(batch)

        task = asyncio.create_task(self._run_loop(settings=settings, batch_id=batch_id))
        self._tasks[batch_id] = task
        return batch

    def pause_batch(self, settings: Settings, batch_id: str) -> BatchRecord:
        """Pause a running batch after its current call finishes (or before next dial)."""
        batch_storage = BatchJsonStorage(settings.batches_json_file)
        batch = batch_storage.get_by_batch_id(batch_id)
        if batch is None:
            raise BatchNotFoundError(f"Batch '{batch_id}' not found.")

        if batch.status == BatchStatus.RUNNING:
            event = self._pause_events.get(batch_id)
            if event:
                event.clear()
            batch.status = BatchStatus.PAUSED
            batch_storage.save_batch(batch)
        return batch

    def resume_batch(self, settings: Settings, batch_id: str) -> BatchRecord:
        """Resume a paused batch."""
        batch_storage = BatchJsonStorage(settings.batches_json_file)
        batch = batch_storage.get_by_batch_id(batch_id)
        if batch is None:
            raise BatchNotFoundError(f"Batch '{batch_id}' not found.")

        if batch.status == BatchStatus.PAUSED:
            batch.status = BatchStatus.RUNNING
            batch_storage.save_batch(batch)
            event = self._pause_events.get(batch_id)
            if event:
                event.set()
            else:
                return self.start_batch(settings=settings, batch_id=batch_id)
        return batch

    def stop_batch(self, settings: Settings, batch_id: str) -> BatchRecord:
        """Stop a batch and mark remaining pending contacts as canceled."""
        batch_storage = BatchJsonStorage(settings.batches_json_file)
        batch = batch_storage.get_by_batch_id(batch_id)
        if batch is None:
            raise BatchNotFoundError(f"Batch '{batch_id}' not found.")

        self._stop_flags.add(batch_id)
        event = self._pause_events.get(batch_id)
        if event:
            event.set()

        if batch.status in {BatchStatus.PENDING, BatchStatus.RUNNING, BatchStatus.PAUSED}:
            batch.status = BatchStatus.STOPPED
            batch.finished_at = datetime.now(timezone.utc)
            for item in batch.items:
                if item.status == BatchItemStatus.PENDING:
                    item.status = BatchItemStatus.CANCELED
                    item.updated_at = datetime.now(timezone.utc)
            batch_storage.save_batch(batch)
        return batch

    async def shutdown(self) -> None:
        """Cancel running background batch tasks cleanly on server shutdown."""
        for batch_id in list(self._tasks.keys()):
            self._stop_flags.add(batch_id)
            event = self._pause_events.get(batch_id)
            if event:
                event.set()
            task = self._tasks[batch_id]
            if not task.done():
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass
        self._tasks.clear()

    async def _wait_for_call_completion(
        self,
        settings: Settings,
        call_id: str,
        batch_id: str,
        call_storage: CallJsonStorage,
        calling_service: CallingService,
    ) -> CallStatus:
        """Wait until call_id reaches a terminal CallStatus before dialing next number."""
        if settings.mock_providers:
            await asyncio.sleep(self.mock_call_duration_seconds)
            record = call_storage.get_by_call_id(call_id)
            if record and record.status not in _TERMINAL_CALL_STATUSES:
                updated, _ = calling_service.process_status_webhook(
                    payload={
                        "call_id": call_id,
                        "status": "completed",
                        "duration_seconds": 12,
                        "source": "mock_batch",
                        "event_id": f"mock-batch-complete:{call_id}",
                        "gathered_context": {
                            "owner_role": "owner",
                            "additional_notes": "Mocked batch call completion",
                        },
                    }
                )
                return updated.status
            return record.status if record else CallStatus.COMPLETED

        elapsed = 0.0
        last_sync = 0.0
        while elapsed < self.max_call_wait_seconds:
            if batch_id in self._stop_flags:
                record = call_storage.get_by_call_id(call_id)
                return record.status if record else CallStatus.CANCELED

            record = call_storage.get_by_call_id(call_id)
            if record and record.status in _TERMINAL_CALL_STATUSES:
                return record.status

            # Fallback Dograh sync every 6 seconds in case a webhook is delayed
            if (
                settings.dograh_workflow_id is not None
                and elapsed - last_sync >= 6.0
            ):
                last_sync = elapsed
                try:
                    synced = await calling_service.sync_call_from_dograh(call_id)
                    if synced.status in _TERMINAL_CALL_STATUSES:
                        return synced.status
                except Exception as exc:
                    logger.debug("Batch poll sync skipped for %s: %s", call_id, exc)

            await asyncio.sleep(self.poll_interval_seconds)
            elapsed += self.poll_interval_seconds

        logger.warning("Timed out waiting for %s to complete; moving to next.", call_id)
        record = call_storage.get_by_call_id(call_id)
        return record.status if record else CallStatus.IN_PROGRESS

    async def _run_loop(self, settings: Settings, batch_id: str) -> None:
        batch_storage, call_storage, lead_storage, calling_service = self._build_services(
            settings
        )
        pause_event = self._pause_events[batch_id]

        try:
            batch = batch_storage.get_by_batch_id(batch_id)
            if batch is None:
                return

            total_items = len(batch.items)
            for idx in range(total_items):
                await pause_event.wait()
                if batch_id in self._stop_flags:
                    break

                # Reload latest state so manual stop/pause changes are respected
                batch = batch_storage.get_by_batch_id(batch_id)
                if batch is None or batch.status == BatchStatus.STOPPED:
                    break

                item = batch.items[idx]
                if item.status != BatchItemStatus.PENDING or not item.phone_number:
                    continue

                if lead_storage.is_phone_opted_out(item.phone_number):
                    item.status = BatchItemStatus.SKIPPED_OPT_OUT
                    item.error = "Skipped: phone number previously opted out (do_not_call)."
                    item.updated_at = datetime.now(timezone.utc)
                    batch_storage.save_batch(batch)
                    continue

                item.status = BatchItemStatus.DIALING
                item.updated_at = datetime.now(timezone.utc)
                batch_storage.save_batch(batch)

                try:
                    call_record = await calling_service.initiate_call(
                        CallInitiateRequest(
                            phone_number=item.phone_number,
                            owner_name=item.owner_name,
                            pg_name=item.pg_name,
                            campaign_id=batch.campaign_id,
                            notes=item.notes,
                        )
                    )
                    item.call_id = call_record.call_id
                    batch_storage.save_batch(batch)

                    final_status = await self._wait_for_call_completion(
                        settings=settings,
                        call_id=call_record.call_id,
                        batch_id=batch_id,
                        call_storage=call_storage,
                        calling_service=calling_service,
                    )
                    batch = batch_storage.get_by_batch_id(batch_id) or batch
                    item = batch.items[idx]
                    item.status = (
                        BatchItemStatus.FAILED
                        if final_status == CallStatus.FAILED
                        else BatchItemStatus.COMPLETED
                    )
                    item.updated_at = datetime.now(timezone.utc)
                    batch_storage.save_batch(batch)
                except OptOutBlockedError as exc:
                    item.status = BatchItemStatus.SKIPPED_OPT_OUT
                    item.error = str(exc)
                    item.updated_at = datetime.now(timezone.utc)
                    batch_storage.save_batch(batch)
                except DograhError as exc:
                    item.status = BatchItemStatus.FAILED
                    item.error = str(exc)
                    item.updated_at = datetime.now(timezone.utc)
                    batch_storage.save_batch(batch)
                except Exception as exc:
                    logger.exception("Unexpected error dialing batch item %s", item.phone_number)
                    item.status = BatchItemStatus.FAILED
                    item.error = str(exc)
                    item.updated_at = datetime.now(timezone.utc)
                    batch_storage.save_batch(batch)

                # Pause cleanly between calls if there are remaining pending items
                has_more = any(
                    other.status == BatchItemStatus.PENDING
                    for other in batch.items[idx + 1 :]
                )
                if has_more and batch.delay_seconds > 0 and batch_id not in self._stop_flags:
                    await asyncio.sleep(batch.delay_seconds)

            final_batch = batch_storage.get_by_batch_id(batch_id)
            if final_batch and final_batch.status not in {
                BatchStatus.STOPPED,
                BatchStatus.FAILED,
            }:
                final_batch.status = BatchStatus.COMPLETED
                final_batch.finished_at = datetime.now(timezone.utc)
                batch_storage.save_batch(final_batch)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Batch runner crashed for batch_id=%s", batch_id)
            failed_batch = batch_storage.get_by_batch_id(batch_id)
            if failed_batch:
                failed_batch.status = BatchStatus.FAILED
                failed_batch.finished_at = datetime.now(timezone.utc)
                batch_storage.save_batch(failed_batch)


_batch_manager = BatchRunnerManager()


def get_batch_manager() -> BatchRunnerManager:
    """Return singleton BatchRunnerManager instance."""
    return _batch_manager