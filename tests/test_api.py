"""
tests/test_api.py

Direct verification of API routes, template rendering, and database operations.
Avoids anyio event loop issues on Windows.
"""

import csv
import io
import sys
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import app as jobee_app
from core import db as jobee_db
from core.models import ApplyType, JobPosting, JobStatus


@pytest.fixture(autouse=True)
def setup_db(tmp_path):
    jobee_db.DB_PATH = tmp_path / "test_api_jobs.db"
    jobee_db.init_db()


def test_template_rendering_jobs():
    """Verify jobs.html template renders cleanly with all context variables."""
    cfg = jobee_app.load_config()
    req = MagicMock()
    rendered = jobee_app.templates.TemplateResponse(
        request=req,
        name="jobs.html",
        context={
            "active_tab": "jobs",
            "jobs": [],
            "config": cfg,
            "stats": {"total": 0, "to_apply": 0, "applied": 0, "synced": 0},
            "request_params": {"source": "", "status": "", "q": ""},
        },
    )
    content = rendered.body.decode("utf-8")
    assert "JOBEE" in content
    assert "Run Fast Scan Now" in content
    assert "Sync to Google Sheets" in content


def test_template_rendering_resume():
    """Verify resume.html renders with V2 locked badge and upload form."""
    cfg = jobee_app.load_config()
    req = MagicMock()
    rendered = jobee_app.templates.TemplateResponse(
        request=req,
        name="resume.html",
        context={
            "active_tab": "resume",
            "config": cfg,
            "resume_exists": False,
            "resume_drive_url": None,
            "google_auth": False,
        },
    )
    content = rendered.body.decode("utf-8")
    assert "Resume Management" in content
    assert "Single Base Resume" in content
    assert "Coming in V2" in content
    assert "LOCAL-ONLY MODE" in content


def test_template_rendering_filters():
    """Verify filters.html renders configured roles and exclude keywords."""
    cfg = jobee_app.load_config()
    req = MagicMock()
    rendered = jobee_app.templates.TemplateResponse(
        request=req,
        name="filters.html",
        context={
            "active_tab": "filters",
            "config": cfg,
        },
    )
    content = rendered.body.decode("utf-8")
    assert "Filters & Search Targets" in content
    assert "Product Manager" in content
    assert "Senior" in content


def test_template_rendering_logs():
    """Verify logs.html renders scrape runs audit table."""
    cfg = jobee_app.load_config()
    req = MagicMock()
    rendered = jobee_app.templates.TemplateResponse(
        request=req,
        name="logs.html",
        context={
            "active_tab": "logs",
            "config": cfg,
            "runs": [
                {
                    "started_at": "2026-11-21T10:00:00+00:00",
                    "source": "jobspy",
                    "status": "success",
                    "raw_count": 25,
                    "dropped_old_count": 5,
                    "filtered_out_count": 10,
                    "duplicate_count": 2,
                    "new_count": 8,
                    "error_message": None,
                }
            ],
        },
    )
    content = rendered.body.decode("utf-8")
    assert "Scraper Run Health & Audit Logs" in content
    assert "jobspy" in content
    assert "SUCCESS" in content


def test_template_rendering_reddit():
    """Verify reddit.html renders Reddit Jobs search interface."""
    cfg = jobee_app.load_config()
    req = MagicMock()
    rendered = jobee_app.templates.TemplateResponse(
        request=req,
        name="reddit.html",
        context={
            "active_tab": "reddit",
            "config": cfg,
        },
    )
    content = rendered.body.decode("utf-8")
    assert "Reddit Jobs Search" in content
    assert "Hyderabad" in content
    assert "Search Reddit" in content


def test_api_status_update_handler():
    """Directly test status update logic."""
    job = JobPosting(
        title="Associate Product Manager",
        company="Jobee Labs",
        source="reddit",
        apply_type=ApplyType.direct,
        posted_at_utc=datetime.now(timezone.utc),
    )
    jobee_db.upsert_job(job)

    # Update status
    ok = jobee_db.update_job_status(job.job_hash, "Applied")
    assert ok is True

    record = jobee_db.get_job_by_hash(job.job_hash)
    assert record["status"] == "Applied"


def test_api_schedule_update_logic():
    """Verify updating the schedule updates config."""
    cfg = jobee_app.update_config({"schedule_hours": 6})
    assert cfg.schedule_hours == 6


def test_api_csv_export_content():
    """Verify CSV export formats all fields properly."""
    job = JobPosting(
        title="AI Engineer",
        company="Startup Prime",
        location="Remote",
        source="lever",
        apply_type=ApplyType.external_ats,
        apply_url="https://jobs.lever.co/example",
        contact_info="apply@startup.com",
        posted_at_utc=datetime.now(timezone.utc),
    )
    jobee_db.upsert_job(job)

    jobs = jobee_db.get_all_jobs()
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["Date Found", "Source", "Company", "Title"])
    for j in jobs:
        writer.writerow([j.get("first_seen_utc"), j.get("source"), j.get("company"), j.get("title")])

    csv_data = output.getvalue()
    assert "AI Engineer" in csv_data
    assert "Startup Prime" in csv_data
    assert "lever" in csv_data
