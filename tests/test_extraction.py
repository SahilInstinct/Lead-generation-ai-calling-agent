"""Tests for Phase 4 structured lead & transcript extraction."""

from app.models.call import CallRecord, CallStatus
from app.models.lead import LeadOutcome
from app.services.extraction_service import ExtractionService


def test_extract_interested_lead_merges_initial_and_gathered_context():
    extractor = ExtractionService()
    call_record = CallRecord(
        call_id="CALL_0001",
        phone_number="+919876543210",
        owner_name="Mahesh",
        campaign_id="blr-q4",
        status=CallStatus.COMPLETED,
        duration_seconds=110,
    )

    payload = {
        "status": "completed",
        "duration_seconds": "110",
        "initial_context": {
            "call_id": "CALL_0001",
            "phone_number": "+919876543210",
            "owner_name": "Mahesh",
            "pg_name": "",
            "campaign_id": "blr-q4",
        },
        "gathered_context": {
            "call_disposition": "interested",
            "owner_name": "",
            "owner_role": "Owner",
            "pg_name": "Comfort Stay PG",
            "confirmed_phone_number": "",
            "contact_number_confirmed": "true",
            "city": "Bengaluru",
            "locality": "Koramangala",
            "total_rooms": "30",
            "available_rooms": 5,
            "interested": "true",
            "listing_free_explained": True,
            "follow_up_required": True,
            "follow_up_day": "Friday",
            "follow_up_time": "3:00 PM",
            "communication_consent": True,
            "do_not_call": False,
            "objections": "Asked about verification process; Asked about commission",
            "additional_questions": "How long does listing go live?",
            "additional_notes": "Prefers afternoon calls",
        },
    }

    lead = extractor.extract_lead(
        call_id="CALL_0001",
        payload=payload,
        call_record=call_record,
    )

    assert lead.call_id == "CALL_0001"
    assert lead.owner_name == "Mahesh"
    assert lead.owner_role == "Owner"
    assert lead.pg_name == "Comfort Stay PG"
    assert lead.phone_number == "+919876543210"
    assert lead.contact_number_confirmed is True
    assert lead.city == "Bengaluru"
    assert lead.locality == "Koramangala"
    assert lead.total_rooms == 30
    assert lead.available_rooms == 5
    assert lead.interested is True
    assert lead.listing_free_explained is True
    assert lead.follow_up_required is True
    assert lead.follow_up_day == "Friday"
    assert lead.follow_up_time == "3:00 PM"
    assert lead.communication_consent is True
    assert lead.do_not_call is False
    assert lead.objections == [
        "Asked about verification process",
        "Asked about commission",
    ]
    assert lead.additional_questions == ["How long does listing go live?"]
    assert lead.outcome == LeadOutcome.INTERESTED
    assert lead.call_duration_seconds == 110


def test_extract_does_not_invent_missing_values_and_normalizes_placeholders():
    extractor = ExtractionService()
    payload = {
        "status": "completed",
        "gathered_context": {
            "call_disposition": "not_interested",
            "owner_name": "N/A",
            "pg_name": "unknown",
            "city": "not mentioned",
            "total_rooms": "null",
            "available_rooms": "around 3 or 4",
            "interested": False,
            "communication_consent": "",
            "do_not_call": False,
        },
    }

    lead = extractor.extract_lead(call_id="CALL_0002", payload=payload)

    assert lead.owner_name is None
    assert lead.pg_name is None
    assert lead.city is None
    assert lead.total_rooms is None
    assert lead.available_rooms is None
    assert "Available rooms: around 3 or 4" in (lead.additional_notes or "")
    assert lead.interested is False
    assert lead.communication_consent is None
    assert lead.outcome == LeadOutcome.NOT_INTERESTED


def test_extract_opt_out_and_callback_and_unanswered():
    extractor = ExtractionService()

    opt_out = extractor.extract_lead(
        call_id="CALL_0003",
        payload={
            "status": "completed",
            "gathered_context": {
                "call_disposition": "opt_out",
                "do_not_call": True,
                "follow_up_required": True,
            },
        },
    )
    assert opt_out.do_not_call is True
    assert opt_out.follow_up_required is False
    assert opt_out.outcome == LeadOutcome.OPT_OUT

    callback = extractor.extract_lead(
        call_id="CALL_0004",
        payload={
            "status": "completed",
            "gathered_context": {
                "call_disposition": "callback_requested",
                "follow_up_required": True,
                "follow_up_day": "Tomorrow",
                "follow_up_time": "5 PM",
            },
        },
    )
    assert callback.outcome == LeadOutcome.CALLBACK_REQUESTED
    assert callback.follow_up_day == "Tomorrow"
    assert callback.interested is None

    unanswered = extractor.extract_lead(
        call_id="CALL_0005",
        payload={"status": "unanswered"},
    )
    assert unanswered.outcome == LeadOutcome.CALL_NOT_CONNECTED
    assert unanswered.interested is None
    assert unanswered.communication_consent is None


def test_extract_transcript_from_turns_and_url():
    extractor = ExtractionService()

    t1 = extractor.extract_transcript(
        call_id="CALL_0001",
        phone_number="+919876543210",
        payload={
            "language": "en-IN",
            "turns": [
                {"speaker": "agent", "text": "Hello, calling from ApkaPG."},
                {"speaker": "owner", "text": "Yes, tell me."},
            ],
        },
    )
    assert t1 is not None
    assert len(t1.turns) == 2

    t2 = extractor.extract_transcript(
        call_id="CALL_0002",
        payload={"transcript_url": "http://127.0.0.1:8000/transcripts/2.txt"},
    )
    assert t2 is not None
    assert "http://127.0.0.1:8000/transcripts/2.txt" in (t2.raw_transcript or "")