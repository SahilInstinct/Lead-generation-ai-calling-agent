"""Tests for phone validators and Call/Lead Pydantic models."""

import pytest
from pydantic import ValidationError

from app.models.call import CallInitiateRequest
from app.models.lead import LeadOutcome, LeadRecord
from app.utils.validators import normalize_phone_number


def test_phone_normalization_indian_formats():
    assert normalize_phone_number("9876543210") == "+919876543210"
    assert normalize_phone_number("09876543210") == "+919876543210"
    assert normalize_phone_number("919876543210") == "+919876543210"
    assert normalize_phone_number("+91 98765-43210") == "+919876543210"
    assert normalize_phone_number("+14155552671") == "+14155552671"


@pytest.mark.parametrize("bad_number", ["", "12345", "abcdef", "+9198765", "5123456789"])
def test_phone_normalization_rejects_invalid(bad_number: str):
    with pytest.raises(ValueError):
        normalize_phone_number(bad_number)


def test_call_initiate_request_validates_and_trims():
    req = CallInitiateRequest(
        phone_number="98765 43210",
        owner_name="  Ramesh Kumar ",
        pg_name="   ",
        campaign_id="camp-01",
    )
    assert req.phone_number == "+919876543210"
    assert req.owner_name == "Ramesh Kumar"
    assert req.pg_name is None
    assert req.campaign_id == "camp-01"


def test_call_initiate_request_rejects_bad_phone():
    with pytest.raises(ValidationError):
        CallInitiateRequest(phone_number="not-a-phone")


def test_lead_record_keeps_uncollected_fields_null():
    lead = LeadRecord(call_id="CALL_0001")
    assert lead.owner_name is None
    assert lead.interested is None
    assert lead.communication_consent is None
    assert lead.do_not_call is False
    assert lead.contact_number_confirmed is False
    assert lead.objections == []
    assert lead.additional_questions == []
    assert lead.outcome == LeadOutcome.UNKNOWN