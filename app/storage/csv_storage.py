"""Safe CSV persistence for structured lead records (data/leads/leads.csv)."""

import csv
import io
from pathlib import Path
from typing import Any

from app.models.lead import LeadRecord
from app.storage.file_lock import atomic_write_text, file_lock


LEAD_CSV_COLUMNS: tuple[str, ...] = (
    "call_id",
    "campaign_id",
    "owner_name",
    "owner_role",
    "pg_name",
    "phone_number",
    "contact_number_confirmed",
    "city",
    "locality",
    "total_rooms",
    "available_rooms",
    "interested",
    "listing_free_explained",
    "follow_up_required",
    "follow_up_day",
    "follow_up_time",
    "communication_consent",
    "do_not_call",
    "objections",
    "additional_questions",
    "additional_notes",
    "outcome",
    "call_status",
    "call_duration_seconds",
    "created_at",
    "updated_at",
)


def _lead_to_csv_row(lead: LeadRecord) -> dict[str, Any]:
    """Convert a LeadRecord into a flat CSV-compatible dictionary."""
    data = lead.model_dump(mode="json")
    row: dict[str, Any] = {}
    for col in LEAD_CSV_COLUMNS:
        value = data.get(col)
        if isinstance(value, list):
            row[col] = "; ".join(str(item) for item in value if item is not None)
        elif value is None:
            row[col] = ""
        else:
            row[col] = value
    return row


class LeadCsvStorage:
    """CSV repository keeping consistent columns and safe atomic writes."""

    def __init__(self, file_path: Path) -> None:
        self.file_path = file_path

    def write_all(self, leads: list[LeadRecord]) -> None:
        """Rewrite the entire CSV file atomically from canonical LeadRecord objects."""
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

        with file_lock(self.file_path):
            atomic_write_text(self.file_path, buffer.getvalue(), encoding="utf-8")

    def upsert_lead(self, lead: LeadRecord) -> None:
        """Insert or update a single lead row by call_id while keeping existing rows intact."""
        with file_lock(self.file_path):
            existing_rows: list[dict[str, str]] = []
            if self.file_path.exists():
                content = self.file_path.read_text(encoding="utf-8")
                if content.strip():
                    reader = csv.DictReader(io.StringIO(content))
                    existing_rows = list(reader)

            new_row = {col: str(_lead_to_csv_row(lead)[col]) for col in LEAD_CSV_COLUMNS}
            replaced = False
            for idx, row in enumerate(existing_rows):
                if row.get("call_id") == lead.call_id:
                    existing_rows[idx] = new_row
                    replaced = True
                    break

            if not replaced:
                existing_rows.append(new_row)

            buffer = io.StringIO()
            writer = csv.DictWriter(
                buffer,
                fieldnames=list(LEAD_CSV_COLUMNS),
                extrasaction="ignore",
                lineterminator="\n",
            )
            writer.writeheader()
            for row in existing_rows:
                normalized_row = {col: row.get(col, "") for col in LEAD_CSV_COLUMNS}
                writer.writerow(normalized_row)

            atomic_write_text(self.file_path, buffer.getvalue(), encoding="utf-8")

    def read_all_rows(self) -> list[dict[str, str]]:
        """Read all rows from the CSV file safely."""
        with file_lock(self.file_path):
            if not self.file_path.exists():
                return []
            content = self.file_path.read_text(encoding="utf-8")
            if not content.strip():
                return []
            return list(csv.DictReader(io.StringIO(content)))