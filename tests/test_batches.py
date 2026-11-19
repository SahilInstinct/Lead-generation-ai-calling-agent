"""Tests for sequential CSV/list parsing and one-by-one automated batch calling."""

import time

from fastapi.testclient import TestClient
import pytest

from app.config import get_settings
from app.main import create_app
from app.models.batch import BatchItemStatus, BatchStatus
from app.models.lead import LeadOutcome, LeadRecord
from app.services.batch_service import (
    BatchValidationError,
    get_batch_manager,
    parse_contacts_from_text,
)
from app.storage.json_storage import LeadJsonStorage


def test_parse_contacts_csv_and_plain_lines():
    csv_text = (
        "phone_number,owner_name,pg_name\n"
        "9876543210,Ramesh,Sunrise PG\n"
        "9876543210,Ramesh Dup,Sunrise PG\n"
        "bad-phone,Unknown,Test\n"
        "9876543211,,\n"
    )
    items = parse_contacts_from_text(csv_text)
    assert len(items) == 4
    assert items[0].phone_number == "+919876543210"
    assert items[0].status == BatchItemStatus.PENDING
    assert items[1].status == BatchItemStatus.SKIPPED_DUPLICATE
    assert items[2].status == BatchItemStatus.INVALID
    assert items[3].phone_number == "+919876543211"

    plain_lines = "9876500001\n9876500002,Suresh,Green PG\n"
    plain_items = parse_contacts_from_text(plain_lines)
    assert len(plain_items) == 2
    assert plain_items[1].owner_name == "Suresh"
    assert plain_items[1].pg_name == "Green PG"


def test_parse_contacts_rejects_empty_or_all_invalid():
    with pytest.raises(BatchValidationError):
        parse_contacts_from_text("   ")
    with pytest.raises(BatchValidationError):
        parse_contacts_from_text("phone_number\nabc\n123\n")


def test_sequential_mock_batch_runs_one_by_one_and_skips_opt_out(tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("MOCK_PROVIDERS", "true")
    monkeypatch.setenv("API_AUTH_KEY", "batch-api-key")
    get_settings.cache_clear()

    # Pre-seed an opt-out number so the sequential runner must skip it
    lead_store = LeadJsonStorage(tmp_path / "data" / "leads" / "leads.json")
    lead_store.upsert_lead(
        LeadRecord(
            call_id="CALL_0000",
            phone_number="+919876543299",
            do_not_call=True,
            outcome=LeadOutcome.OPT_OUT,
        )
    )

    manager = get_batch_manager()
    old_mock_dur = manager.mock_call_duration_seconds
    manager.mock_call_duration_seconds = 0.05

    try:
        app = create_app()
        with TestClient(app) as client:
            # External API requires X-API-Key
            unauth = client.post(
                "/api/batches",
                json={"csv_content": "9876543210"},
            )
            assert unauth.status_code == 401

            # Local UI starts batch without X-API-Key
            resp = client.post(
                "/ui/batches",
                json={
                    "campaign_id": "auto-batch-1",
                    "delay_seconds": 0,
                    "csv_content": (
                        "phone_number,owner_name,pg_name\n"
                        "9876543210,Kiran,Star PG\n"
                        "9876543299,Opted Out,Skip PG\n"
                        "9876543211,Mohan,Lake PG\n"
                    ),
                },
            )
            assert resp.status_code == 201
            batch_id = resp.json()["batch"]["batch_id"]

            # Poll briefly until background sequential runner finishes
            final_data = None
            for _ in range(40):
                check = client.get(f"/api/batches/{batch_id}")
                assert check.status_code == 200
                final_data = check.json()
                if final_data["batch"]["status"] == BatchStatus.COMPLETED.value:
                    break
                time.sleep(0.05)

            assert final_data is not None
            assert final_data["batch"]["status"] == BatchStatus.COMPLETED.value
            items = final_data["batch"]["items"]
            assert items[0]["status"] == BatchItemStatus.COMPLETED.value
            assert items[0]["call_id"] == "CALL_0001"
            assert items[1]["status"] == BatchItemStatus.SKIPPED_OPT_OUT.value
            assert items[1]["call_id"] is None
            assert items[2]["status"] == BatchItemStatus.COMPLETED.value
            assert items[2]["call_id"] == "CALL_0002"
    finally:
        manager.mock_call_duration_seconds = old_mock_dur
        get_settings.cache_clear()