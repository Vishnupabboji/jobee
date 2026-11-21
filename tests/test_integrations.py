"""
tests/test_integrations.py

Verifies Google Drive & Sheets integration components and local-only fallbacks.
Runs without requiring real Google credentials.
"""

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from integrations import drive_sync, google_auth, sheets_sync
from core import db as jobee_db
from core.models import JobPosting, ApplyType
from datetime import datetime, timezone


def test_google_auth_local_fallback():
    """When credentials.json is missing, get_credentials returns None gracefully."""
    with patch("integrations.google_auth.CREDENTIALS_PATH", Path("non_existent_creds_9999.json")):
        creds = google_auth.get_credentials()
        assert creds is None
        assert not google_auth.is_authenticated()


def test_drive_sync_local_fallback():
    """Drive upload returns None when unauthenticated."""
    with patch("integrations.google_auth.get_credentials", return_value=None):
        url = drive_sync.upload_base_resume()
        assert url is None


def test_sheets_sync_local_fallback():
    """Sheets sync returns 0 and error string when unauthenticated."""
    with patch("integrations.google_auth.get_credentials", return_value=None):
        count, err = sheets_sync.sync_jobs_to_sheet("dummy_sheet_id")
        assert count == 0
        assert "Not authenticated" in err


def test_build_row_spec():
    """Ensure _build_row outputs exact 13 columns matching the spec."""
    sample_job = {
        "first_seen_utc": "2026-11-21T10:00:00+00:00",
        "posted_at_utc": "2026-11-21T10:00:00+00:00",
        "source": "linkedin",
        "company": "Acme Corp",
        "title": "Product Manager",
        "location": "Remote",
        "apply_type": "External ATS",
        "apply_url": "https://example.com/apply",
        "contact_info": "jobs@acme.com",
        "status": "To Apply",
        "resume_mode_used": "single_base",
        "resume_drive_url": "https://drive.google.com/test",
        "job_hash": "testhash12345",
    }
    row = sheets_sync._build_row(sample_job)
    assert len(row) == 13
    assert row[0] == "2026-11-21"
    assert row[2] == "linkedin"
    assert row[3] == "Acme Corp"
    assert row[4] == "Product Manager"
    assert row[9] == "To Apply"
    assert row[12] == "testhash12345"


def test_update_status_mock(tmp_path):
    """Verify update_status_in_sheet locates row by Job Hash and updates Status column."""
    mock_service = MagicMock()
    mock_values = mock_service.spreadsheets().values()
    
    # Simulate Job Hash column (M:M) where row 3 has our hash
    mock_values.get.return_value.execute.return_value = {
        "values": [["Job Hash"], ["other_hash"], ["target_hash_999"]]
    }
    
    with patch("integrations.google_auth.get_credentials", return_value=MagicMock()):
        with patch("googleapiclient.discovery.build", return_value=mock_service):
            success = sheets_sync.update_status_in_sheet("sheet123", "target_hash_999", "Applied")
            assert success is True
            # Row index 3 (1-indexed), Status column is J -> J3
            mock_values.update.assert_called_once()
            call_kwargs = mock_values.update.call_args[1]
            assert call_kwargs["range"] == "J3"
            assert call_kwargs["body"] == {"values": [["Applied"]]}
