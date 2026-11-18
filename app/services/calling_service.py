"""Core outbound calling orchestration, lifecycle tracking, and lead persistence."""

from datetime import datetime, timezone
import hashlib
import json
from typing import Any

from app.config import Settings
from app.models.call import (
    CallEventEntry,
    CallInitiateRequest,
    CallRecord,
    CallStatus,
)
from app.models.lead import LeadOutcome, LeadRecord
from app.services.dograh_service import DograhError, DograhService
from app.services.extraction_service import ExtractionService
from app.services.vobiz_service import VobizService
from app.storage.csv_storage import LeadCsvStorage
from app.storage.json_storage import (
    CallJsonStorage,
    LeadJsonStorage,
    TranscriptJsonStorage,
)
from app.utils.logger import get_logger

logger = get_logger(__name__)

_TERMINAL_CALL_STATUSES = {
    CallStatus.COMPLETED,
    CallStatus.FAILED,
    CallStatus.BUSY,
    CallStatus.UNANSWERED,
    CallStatus.CANCELED,
}


class OptOutBlockedError(Exception):
    """Raised when attempting to call a phone number that previously opted out."""


class CallNotFoundError(Exception):
    """Raised when a requested call record cannot be found."""


class CallingService:
    """Coordinates call initiation, Dograh/Vobiz lifecycle events, and lead extraction."""

    def __init__(
        self,
        settings: Settings,
        call_storage: CallJsonStorage,
        lead_storage: LeadJsonStorage,
        dograh_service: DograhService,
        vobiz_service: VobizService,
        lead_csv_storage: LeadCsvStorage | None = None,
        transcript_storage: TranscriptJsonStorage | None = None,
        extraction_service: ExtractionService | None = None,
    ) -> None:
        self.settings = settings
        self.call_storage = call_storage
        self.lead_storage = lead_storage
        self.dograh_service = dograh_service
        self.vobiz_service = vobiz_service
        self.lead_csv_storage = lead_csv_storage or LeadCsvStorage(
            settings.leads_csv_file
        )
        self.transcript_storage = transcript_storage or TranscriptJsonStorage(
            settings.transcripts_json_file
        )
        self.extraction_service = extraction_service or ExtractionService()

    @staticmethod
    def _has_real_gathered_data(gathered_ctx: Any) -> bool:
        if not isinstance(gathered_ctx, dict):
            return False
        for value in gathered_ctx.values():
            if value is None:
                continue
            text = str(value).strip()
            if not text:
                continue
            if text.startswith("{{") and text.endswith("}}"):
                continue
            return True
        return False

    def _persist_lead_and_transcript(
        self,
        call_record: CallRecord,
        payload: dict[str, Any],
    ) -> LeadRecord | None:
        """Extract and persist LeadRecord (JSON + CSV) and TranscriptRecord safely."""
        gathered_ctx = payload.get("gathered_context")
        has_gathered = self._has_real_gathered_data(gathered_ctx)
        is_terminal = call_record.status in _TERMINAL_CALL_STATUSES

        if not has_gathered and not is_terminal:
            return None

        existing_lead = self.lead_storage.get_by_call_id(call_record.call_id)

        if existing_lead is not None and not has_gathered:
            existing_lead.call_status = call_record.status.value
            if call_record.duration_seconds is not None:
                existing_lead.call_duration_seconds = call_record.duration_seconds
            if (
                existing_lead.outcome == LeadOutcome.UNKNOWN
                and call_record.status
                in {CallStatus.FAILED, CallStatus.BUSY, CallStatus.UNANSWERED, CallStatus.CANCELED}
            ):
                existing_lead.outcome = LeadOutcome.CALL_NOT_CONNECTED

            saved_lead = self.lead_storage.upsert_lead(existing_lead)
            self.lead_csv_storage.upsert_lead(saved_lead)
            return saved_lead

        extracted_lead = self.extraction_service.extract_lead(
            call_id=call_record.call_id,
            payload=payload,
            call_record=call_record,
        )
        saved_lead = self.lead_storage.upsert_lead(extracted_lead)
        self.lead_csv_storage.upsert_lead(saved_lead)

        transcript = self.extraction_service.extract_transcript(
            call_id=call_record.call_id,
            payload=payload,
            phone_number=call_record.phone_number,
        )
        if transcript is not None:
            self.transcript_storage.upsert_transcript(transcript)

        logger.info(
            "Saved lead record: call_id=%s outcome=%s do_not_call=%s",
            saved_lead.call_id,
            saved_lead.outcome.value,
            saved_lead.do_not_call,
        )
        return saved_lead

    async def initiate_call(self, request: CallInitiateRequest) -> CallRecord:
        """Validate opt-out status, persist call attempt, and trigger Dograh + Vobiz."""
        if self.lead_storage.is_phone_opted_out(request.phone_number):
            raise OptOutBlockedError(
                f"Phone number {request.phone_number} is marked do_not_call and cannot be dialed."
            )

        call_record = self.call_storage.create_call(request)
        initial_context: dict[str, Any] = {
            "call_id": call_record.call_id,
            "phone_number": request.phone_number,
            "owner_name": request.owner_name,
            "pg_name": request.pg_name,
            "campaign_id": request.campaign_id,
            "notes": request.notes,
        }

        if self.settings.mock_providers:
            mock_run_id = f"mock-run-{call_record.call_id}"
            event = CallEventEntry(
                event_key=f"{call_record.call_id}:mock:provider_accepted",
                source="mock_dograh",
                status=CallStatus.RINGING,
                raw_status="ringing",
                details={"workflow_run_id": mock_run_id, "vobiz_mode": self.settings.vobiz_mode},
            )
            updated, _ = self.call_storage.record_event(
                call_id=call_record.call_id,
                event=event,
                workflow_session_id=mock_run_id,
                provider_call_id=f"mock-vobiz-{call_record.call_id}",
            )
            logger.info(
                "Mock outbound call initiated: call_id=%s workflow_run_id=%s",
                updated.call_id,
                mock_run_id,
            )
            return updated

        try:
            trigger_result = await self.dograh_service.trigger_outbound_call(
                phone_number=request.phone_number,
                initial_context=initial_context,
            )
        except DograhError as exc:
            failure_event = CallEventEntry(
                event_key=f"{call_record.call_id}:dograh:initiate_failed",
                source="dograh",
                status=CallStatus.FAILED,
                raw_status="failed",
                details={"error": str(exc)},
            )
            failed_record, _ = self.call_storage.record_event(
                call_id=call_record.call_id,
                event=failure_event,
                failure_reason=str(exc),
            )
            self._persist_lead_and_transcript(
                call_record=failed_record,
                payload={
                    "status": "failed",
                    "initial_context": initial_context,
                    "gathered_context": {
                        "additional_notes": f"Call initiation failed: {exc}"
                    },
                },
            )
            logger.error(
                "Outbound call initiation failed for call_id=%s: %s",
                call_record.call_id,
                exc,
            )
            raise

        accepted_event = CallEventEntry(
            event_key=f"{call_record.call_id}:dograh:triggered:{trigger_result.workflow_run_id}",
            source="dograh",
            status=self.vobiz_service.map_status(trigger_result.status),
            raw_status=trigger_result.status,
            details={
                "workflow_run_id": trigger_result.workflow_run_id,
                "workflow_run_name": trigger_result.workflow_run_name,
            },
        )
        updated, _ = self.call_storage.record_event(
            call_id=call_record.call_id,
            event=accepted_event,
            workflow_session_id=trigger_result.workflow_run_id,
        )
        logger.info(
            "Outbound call triggered in Dograh: call_id=%s workflow_run_id=%s",
            updated.call_id,
            trigger_result.workflow_run_id,
        )
        return updated

    def _find_call_for_webhook(
        self,
        call_id: str | None,
        workflow_run_id: str | None,
        provider_call_id: str | None,
    ) -> CallRecord | None:
        if call_id:
            found = self.call_storage.get_by_call_id(call_id)
            if found:
                return found

        if provider_call_id:
            found = self.call_storage.get_by_provider_call_id(provider_call_id)
            if found:
                return found

        if workflow_run_id:
            for item in self.call_storage.list_calls():
                if item.workflow_session_id == str(workflow_run_id):
                    return item

        return None

    def process_status_webhook(
        self,
        payload: dict[str, Any],
        delivery_id: str | None = None,
    ) -> tuple[CallRecord, bool]:
        """Process a Dograh/Vobiz webhook idempotently and persist leads/transcripts."""
        initial_ctx = payload.get("initial_context")
        call_id = payload.get("call_id") or (
            initial_ctx.get("call_id") if isinstance(initial_ctx, dict) else None
        )
        if call_id and str(call_id).startswith("{{"):
            call_id = None

        workflow_run_id = (
            str(payload["workflow_run_id"])
            if payload.get("workflow_run_id") is not None
            and not str(payload["workflow_run_id"]).startswith("{{")
            else None
        )
        provider_call_id = (
            payload.get("provider_call_id")
            or payload.get("call_uuid")
            or payload.get("CallUUID")
        )

        record = self._find_call_for_webhook(
            call_id=call_id,
            workflow_run_id=workflow_run_id,
            provider_call_id=provider_call_id,
        )
        if record is None:
            raise CallNotFoundError(
                f"No matching call found for call_id={call_id}, workflow_run_id={workflow_run_id}."
            )

        raw_status = (
            payload.get("status")
            or payload.get("call_status")
            or payload.get("disposition")
            or payload.get("CallStatus")
        )
        mapped_status = self.vobiz_service.map_status(
            str(raw_status) if raw_status is not None else None
        )

        cost_info = payload.get("cost_info")
        cost_duration = (
            cost_info.get("call_duration_seconds")
            if isinstance(cost_info, dict)
            else None
        )
        raw_duration = (
            payload.get("duration_seconds")
            or payload.get("call_duration_seconds")
            or payload.get("duration")
            or cost_duration
        )
        duration_seconds: int | None = None
        if (
            raw_duration is not None
            and str(raw_duration).strip() != ""
            and not str(raw_duration).strip().startswith("{{")
        ):
            try:
                duration_seconds = max(0, int(float(raw_duration)))
            except (ValueError, TypeError):
                duration_seconds = None

        failure_reason = payload.get("failure_reason") or payload.get("error")

        if delivery_id:
            event_key = f"delivery:{delivery_id}"
        elif payload.get("event_id"):
            event_key = f"event:{payload['event_id']}"
        else:
            canonical = json.dumps(
                {
                    "call_id": record.call_id,
                    "status": mapped_status.value,
                    "raw_status": raw_status,
                    "duration_seconds": duration_seconds,
                    "workflow_run_id": workflow_run_id,
                    "provider_call_id": provider_call_id,
                    "gathered_context": payload.get("gathered_context"),
                },
                sort_keys=True,
                default=str,
            )
            digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:16]
            event_key = f"hash:{digest}"

        event = CallEventEntry(
            event_key=event_key,
            source=str(payload.get("source", "dograh")),
            status=mapped_status,
            raw_status=str(raw_status) if raw_status is not None else None,
            timestamp=datetime.now(timezone.utc),
            details={
                k: v
                for k, v in payload.items()
                if k not in {"transcript", "raw_transcript"}
            },
        )

        updated, applied = self.call_storage.record_event(
            call_id=record.call_id,
            event=event,
            duration_seconds=duration_seconds,
            failure_reason=str(failure_reason) if failure_reason else None,
            provider_call_id=str(provider_call_id) if provider_call_id else None,
            workflow_session_id=workflow_run_id,
        )

        if applied:
            self._persist_lead_and_transcript(call_record=updated, payload=payload)

        return updated, applied

    async def sync_call_from_dograh(self, call_id: str) -> CallRecord:
        """Pull the latest run state and gathered context for a call from Dograh API."""
        record = self.call_storage.get_by_call_id(call_id)
        if record is None:
            raise CallNotFoundError(f"Call '{call_id}' not found.")

        if self.settings.mock_providers:
            return record

        if not record.workflow_session_id:
            raise DograhError("Call does not have a Dograh workflow_run_id yet.", status_code=400)
        if self.settings.dograh_workflow_id is None:
            raise DograhError(
                "DOGRAH_WORKFLOW_ID must be set in .env to fetch run status from Dograh.",
                status_code=400,
            )

        run_data = await self.dograh_service.get_workflow_run(
            workflow_id=self.settings.dograh_workflow_id,
            run_id=record.workflow_session_id,
        )
        status_value = run_data.get("status") or run_data.get("call_status")
        if not status_value:
            status_value = "completed" if run_data.get("is_completed") else "in_progress"

        cost_info = run_data.get("cost_info")
        duration_value = (
            run_data.get("duration_seconds")
            or run_data.get("call_duration_seconds")
            or (cost_info.get("call_duration_seconds") if isinstance(cost_info, dict) else None)
        )
        gathered_ctx = run_data.get("gathered_context")

        updated, _ = self.process_status_webhook(
            payload={
                "call_id": record.call_id,
                "workflow_run_id": record.workflow_session_id,
                "status": status_value,
                "duration_seconds": duration_value,
                "initial_context": run_data.get("initial_context"),
                "gathered_context": gathered_ctx,
                "transcript_url": run_data.get("transcript_url"),
                "transcript": run_data.get("transcript"),
                "turns": run_data.get("logs") or run_data.get("turns"),
                "source": "dograh_sync",
                "event_id": (
                    f"sync:{record.workflow_session_id}:{status_value}:"
                    f"{duration_value}:{bool(gathered_ctx)}"
                ),
            }
        )
        return updated

    def get_call(self, call_id: str) -> CallRecord:
        record = self.call_storage.get_by_call_id(call_id)
        if record is None:
            raise CallNotFoundError(f"Call '{call_id}' not found.")
        return record

    def list_calls(self) -> list[CallRecord]:
        return self.call_storage.list_calls()