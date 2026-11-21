"""
integrations/sheets_sync.py

Syncs jobs to Google Sheets.
  - Auto-creates frozen bold header row if sheet is empty
  - Appends only: synced_to_sheet=False AND is_repost=False
  - Status updates: finds row by Job Hash column, updates Status cell only
  - Local-only fallback: returns False if not authenticated
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Optional

log = logging.getLogger(__name__)

# Google Sheets column spec (1-indexed)
HEADERS = [
    "Date Found", "Hours Old", "Source", "Company", "Role Title",
    "Location", "Apply Type", "Apply Link", "Contact Info",
    "Status", "Resume Mode", "Resume Link", "Job Hash",
]
STATUS_COL = 10        # "Status" is column J (1-indexed)
JOB_HASH_COL = 13      # "Job Hash" is column M (1-indexed)
STATUS_OPTIONS = ["To Apply", "Applied", "Interview", "Rejected", "Skipped"]


def _a1(col: int, row: int) -> str:
    """Convert 1-indexed col/row to A1 notation."""
    col_letter = ""
    while col > 0:
        col -= 1
        col_letter = chr(65 + col % 26) + col_letter
        col //= 26
    return f"{col_letter}{row}"


def _build_row(job: dict) -> list:
    """Build a Sheets row list from a job dict (from db.get_all_jobs)."""
    posted_str = job.get("first_seen_utc") or job.get("posted_at_utc") or ""
    # Compute hours old
    try:
        posted_dt = datetime.fromisoformat(posted_str)
        if posted_dt.tzinfo is None:
            posted_dt = posted_dt.replace(tzinfo=timezone.utc)
        hours_old = round((datetime.now(timezone.utc) - posted_dt).total_seconds() / 3600, 1)
    except Exception:
        hours_old = ""

    return [
        posted_str[:10],                    # Date Found (YYYY-MM-DD)
        hours_old,                           # Hours Old
        job.get("source", ""),
        job.get("company", ""),
        job.get("title", ""),
        job.get("location", ""),
        job.get("apply_type", ""),
        job.get("apply_url", ""),
        job.get("contact_info", ""),
        job.get("status", "To Apply"),
        job.get("resume_mode_used", "single_base"),
        job.get("resume_drive_url", ""),
        job.get("job_hash", ""),
    ]


def sync_jobs_to_sheet(sheet_id: str) -> tuple[int, str]:
    """
    Append unsynced jobs to Google Sheet.
    Returns (rows_appended, error_message).
    """
    from integrations.google_auth import get_credentials
    creds = get_credentials()
    if creds is None:
        return 0, "Not authenticated (Local-Only mode)"

    from core import db as jobee_db
    unsynced = jobee_db.get_unsynced_jobs()
    if not unsynced:
        return 0, ""

    try:
        from googleapiclient.discovery import build
        service = build("sheets", "v4", credentials=creds)
        sheet = service.spreadsheets()

        # --- Ensure header row exists ---
        result = sheet.values().get(
            spreadsheetId=sheet_id, range="A1:M1"
        ).execute()
        existing = result.get("values", [])
        if not existing or existing[0] != HEADERS:
            sheet.values().update(
                spreadsheetId=sheet_id,
                range="A1",
                valueInputOption="RAW",
                body={"values": [HEADERS]},
            ).execute()
            # Freeze row 1 + bold
            _apply_header_formatting(sheet, sheet_id)

        # --- Append rows ---
        rows = [_build_row(j) for j in unsynced]
        sheet.values().append(
            spreadsheetId=sheet_id,
            range="A1",
            valueInputOption="USER_ENTERED",
            insertDataOption="INSERT_ROWS",
            body={"values": rows},
        ).execute()

        # --- Add Status dropdown validation ---
        _ensure_status_validation(sheet, sheet_id)

        # --- Mark synced in DB ---
        jobee_db.mark_synced_to_sheet([j["job_hash"] for j in unsynced])

        log.info("Sheets: appended %d rows to %s", len(rows), sheet_id)
        return len(rows), ""

    except Exception as exc:
        log.error("Sheets sync failed: %s", exc)
        return 0, str(exc)


def update_status_in_sheet(sheet_id: str, job_hash: str, new_status: str) -> bool:
    """
    Find the row with matching Job Hash and update only the Status cell.
    Never overwrites other columns (preserves manual edits).
    """
    from integrations.google_auth import get_credentials
    creds = get_credentials()
    if creds is None:
        return False

    try:
        from googleapiclient.discovery import build
        service = build("sheets", "v4", credentials=creds)
        sheet = service.spreadsheets()

        # Read Job Hash column to find the row
        result = sheet.values().get(
            spreadsheetId=sheet_id, range=f"M:M"
        ).execute()
        hash_col = result.get("values", [])

        target_row = None
        for idx, cell in enumerate(hash_col):
            if cell and cell[0] == job_hash:
                target_row = idx + 1  # 1-indexed
                break

        if target_row is None:
            log.warning("Sheets: job_hash %s not found in sheet", job_hash)
            return False

        cell_range = _a1(STATUS_COL, target_row)
        sheet.values().update(
            spreadsheetId=sheet_id,
            range=cell_range,
            valueInputOption="RAW",
            body={"values": [[new_status]]},
        ).execute()
        log.info("Sheets: updated status for hash %s → %s (row %d)", job_hash, new_status, target_row)
        return True

    except Exception as exc:
        log.error("Sheets status update failed: %s", exc)
        return False


def _apply_header_formatting(sheet, sheet_id: str) -> None:
    """Freeze row 1 and bold the header."""
    try:
        requests_body = [
            {
                "updateSheetProperties": {
                    "properties": {"sheetId": 0, "gridProperties": {"frozenRowCount": 1}},
                    "fields": "gridProperties.frozenRowCount",
                }
            },
            {
                "repeatCell": {
                    "range": {"sheetId": 0, "startRowIndex": 0, "endRowIndex": 1},
                    "cell": {"userEnteredFormat": {"textFormat": {"bold": True}}},
                    "fields": "userEnteredFormat.textFormat.bold",
                }
            },
        ]
        sheet.batchUpdate(
            spreadsheetId=sheet_id,
            body={"requests": requests_body},
        ).execute()
    except Exception as exc:
        log.warning("Sheets: header formatting failed (non-critical): %s", exc)


def _ensure_status_validation(sheet, sheet_id: str) -> None:
    """Add Data Validation dropdown to Status column (J)."""
    try:
        condition_values = [{"userEnteredValue": s} for s in STATUS_OPTIONS]
        sheet.batchUpdate(
            spreadsheetId=sheet_id,
            body={
                "requests": [
                    {
                        "setDataValidation": {
                            "range": {
                                "sheetId": 0,
                                "startRowIndex": 1,
                                "startColumnIndex": STATUS_COL - 1,
                                "endColumnIndex": STATUS_COL,
                            },
                            "rule": {
                                "condition": {
                                    "type": "ONE_OF_LIST",
                                    "values": condition_values,
                                },
                                "showCustomUi": True,
                                "strict": True,
                            },
                        }
                    }
                ]
            },
        ).execute()
    except Exception as exc:
        log.warning("Sheets: status validation setup failed (non-critical): %s", exc)
