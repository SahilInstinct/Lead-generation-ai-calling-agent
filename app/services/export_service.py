"""Service for querying, filtering, and exporting collected lead records."""

import csv
import io
import json
from typing import Any

from app.models.lead import LeadOutcome, LeadRecord
from app.storage.csv_storage import LEAD_CSV_COLUMNS, _lead_to_csv_row
from app.storage.json_storage import LeadJsonStorage


class LeadNotFoundError(Exception):
    """Raised when a requested lead record cannot be found."""


class ExportService:
    """Provides filtered lead queries and JSON/CSV export generation."""

    def __init__(self, lead_storage: LeadJsonStorage) -> None:
        self.lead_storage = lead_storage

    def list_leads(
        self,
        outcome: LeadOutcome | None = None,
        interested: bool | None = None,
        do_not_call: bool | None = None,
        campaign_id: str | None = None,
    ) -> list[LeadRecord]:
        """Return leads optionally filtered by outcome, interest, DNC, or campaign."""
        leads = self.lead_storage.list_leads()
        if outcome is not None:
            leads = [lead for lead in leads if lead.outcome == outcome]
        if interested is not None:
            leads = [lead for lead in leads if lead.interested is interested]
        if do_not_call is not None:
            leads = [lead for lead in leads if lead.do_not_call is do_not_call]
        if campaign_id is not None:
            leads = [lead for lead in leads if lead.campaign_id == campaign_id]
        return leads

    def get_lead(self, call_id: str) -> LeadRecord:
        """Retrieve a single lead by call_id or raise LeadNotFoundError."""
        lead = self.lead_storage.get_by_call_id(call_id)
        if lead is None:
            raise LeadNotFoundError(f"Lead for call '{call_id}' not found.")
        return lead

    def export_json_string(
        self,
        outcome: LeadOutcome | None = None,
        interested: bool | None = None,
        do_not_call: bool | None = None,
        campaign_id: str | None = None,
    ) -> str:
        """Serialize filtered leads to formatted JSON."""
        leads = self.list_leads(
            outcome=outcome,
            interested=interested,
            do_not_call=do_not_call,
            campaign_id=campaign_id,
        )
        payload: list[dict[str, Any]] = [lead.model_dump(mode="json") for lead in leads]
        return json.dumps(payload, indent=2, ensure_ascii=False) + "\n"

    def export_csv_string(
        self,
        outcome: LeadOutcome | None = None,
        interested: bool | None = None,
        do_not_call: bool | None = None,
        campaign_id: str | None = None,
    ) -> str:
        """Serialize filtered leads to CSV with consistent headers."""
        leads = self.list_leads(
            outcome=outcome,
            interested=interested,
            do_not_call=do_not_call,
            campaign_id=campaign_id,
        )
        buffer = io.StringIO()
        writer = csv.DictWriter(
            buffer,
            fieldnames=list(LEAD_CSV_COLUMNS),
            extrasaction="ignore",
            lineterminator="\n",
        )
        writer.writeheader()
        for lead in leads:
            writer.writerow(_lead_to_csv_row(lead))
        return buffer.getvalue()