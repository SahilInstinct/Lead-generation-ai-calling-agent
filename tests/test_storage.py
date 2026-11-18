"""Tests for thread/process-safe JSON & CSV storage, duplicates, and opt-outs."""

from concurrent.futures import ThreadPoolExecutor
import csv
import json

from app.models.call import (
    CallEventEntry,
    CallInitiateRequest,
    CallStatus,
    TranscriptRecord,
    TranscriptTurn,
)
from app.models.lead import LeadOutcome, LeadRecord
from app.storage.csv_storage import LEAD_CSV_COLUMNS, LeadCsvStorage
from app.storage.json_storage import (
    CallJsonStorage,
    LeadJsonStorage,
    TranscriptJsonStorage,
)


def test_call_storage_unique_ids_and_concurrent_writes(tmp_path):
    store = CallJsonStorage(tmp_path / "calls" / "call_history.json")

    def _create(idx: int) -> str:
        req = CallInitiateRequest(phone_number=f"98765432{idx:02d}")
        record = store.create_call(req)
        return record.call_id

    with ThreadPoolExecutor(max_workers=6) as pool:
        call_ids = list(pool.map(_create, range(12)))

    assert len(set(call_ids)) == 12
    assert sorted(call_ids)[0] == "CALL_0001"
    assert sorted(call_ids)[-1] == "CALL_0012"

    # Verify JSON remains valid on disk
    raw = json.loads(store.file_path.read_text(encoding="utf-8"))
    assert len(raw) == 12


def test_duplicate_webhook_event_is_ignored(tmp_path):
    store = CallJsonStorage(tmp_path / "calls" / "call_history.json")
    call = store.create_call(CallInitiateRequest(phone_number="9876543210"))

    event = CallEventEntry(
        event_key="vobiz:evt_123",
        source="vobiz",
        status=CallStatus.COMPLETED,
        raw_status="completed",
    )

    updated_1, applied_1 = store.record_event(
        call.call_id, event, duration_seconds=95
    )
    updated_2, applied_2 = store.record_event(
        call.call_id, event, duration_seconds=95
    )

    assert applied_1 is True
    assert applied_2 is False
    assert updated_1.status == CallStatus.COMPLETED
    assert updated_2.duration_seconds == 95
    assert len(updated_2.events) == 2  # initial 'initiated' + single 'completed'


def test_lead_json_and_csv_storage_outcomes_and_opt_out(tmp_path):
    json_store = LeadJsonStorage(tmp_path / "leads" / "leads.json")
    csv_store = LeadCsvStorage(tmp_path / "leads" / "leads.csv")

    interested_lead = LeadRecord(
        call_id="CALL_0001",
        owner_name="Suresh",
        pg_name="Sri Sai PG",
        phone_number="9876543210",
        contact_number_confirmed=True,
        city="Bengaluru",
        locality="BTM Layout",
        total_rooms=24,
        available_rooms=4,
        interested=True,
        listing_free_explained=True,
        follow_up_required=True,
        follow_up_day="Tomorrow",
        follow_up_time="11:00 AM",
        communication_consent=True,
        objections=["Wanted to know if commission applies"],
        additional_questions=["How soon do students see the listing?"],
        outcome=LeadOutcome.INTERESTED,
        call_status="completed",
        call_duration_seconds=120,
    )

    not_interested_lead = LeadRecord(
        call_id="CALL_0002",
        phone_number="9876543211",
        interested=False,
        outcome=LeadOutcome.NOT_INTERESTED,
        call_status="completed",
    )

    opt_out_lead = LeadRecord(
        call_id="CALL_0003",
        phone_number="9876543212",
        interested=False,
        communication_consent=False,
        do_not_call=True,
        outcome=LeadOutcome.OPT_OUT,
        call_status="completed",
    )

    for lead in (interested_lead, not_interested_lead, opt_out_lead):
        json_store.upsert_lead(lead)
        csv_store.upsert_lead(lead)

    # Update CALL_0001 without duplicating rows
    interested_lead.additional_notes = "Prefers WhatsApp before call"
    json_store.upsert_lead(interested_lead)
    csv_store.upsert_lead(interested_lead)

    assert len(json_store.list_leads()) == 3
    assert json_store.is_phone_opted_out("9876543212") is True
    assert json_store.is_phone_opted_out("9876543210") is False

    # Verify CSV header & row consistency
    with csv_store.file_path.open("r", encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        assert tuple(reader.fieldnames or []) == LEAD_CSV_COLUMNS
        rows = list(reader)

    assert len(rows) == 3
    row_1 = next(r for r in rows if r["call_id"] == "CALL_0001")
    assert row_1["pg_name"] == "Sri Sai PG"
    assert row_1["additional_notes"] == "Prefers WhatsApp before call"


def test_transcript_storage_upsert(tmp_path):
    store = TranscriptJsonStorage(tmp_path / "transcripts" / "call_transcripts.json")
    t1 = TranscriptRecord(
        call_id="CALL_0001",
        phone_number="+919876543210",
        language="en-IN",
        turns=[TranscriptTurn(speaker="agent", text="Hello, calling from ApkaPG.")],
    )
    store.upsert_transcript(t1)

    t1.turns.append(TranscriptTurn(speaker="owner", text="Yes, tell me."))
    store.upsert_transcript(t1)

    saved = store.get_by_call_id("CALL_0001")
    assert saved is not None
    assert len(saved.turns) == 2