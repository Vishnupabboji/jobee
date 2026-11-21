"""
tests/test_jd_scraper.py

Tests the live JD web scraper and location filtering logic.
"""

import sys
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from bs4 import BeautifulSoup

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core import db as jobee_db
from core.jd_scraper import _clean_soup_text, fetch_live_jd
from core.models import ApplyType, JobPosting


@pytest.fixture(autouse=True)
def setup_db(tmp_path):
    jobee_db.DB_PATH = tmp_path / "test_jd_jobs.db"
    jobee_db.init_db()


def test_clean_soup_text_formatting():
    html = """
    <div>
        <h2>About the Role</h2>
        <p>We are hiring an AI Engineer.</p>
        <ul>
            <li>Build models</li>
            <li>Deploy to production</li>
        </ul>
    </div>
    """
    soup = BeautifulSoup(html, "html.parser")
    cleaned = _clean_soup_text(soup)
    assert "About the Role" in cleaned
    assert "We are hiring an AI Engineer." in cleaned
    assert "• Build models" in cleaned
    assert "• Deploy to production" in cleaned


def test_fetch_live_jd_mock():
    mock_html = """
    <html>
        <body>
            <nav>Header Nav Ignored</nav>
            <div id="content">
                <h1>Senior Product Lead</h1>
                <p>Lead our core growth initiatives across payment rails.</p>
                <ul>
                    <li>5+ years required</li>
                    <li>FastAPI and Python experience</li>
                </ul>
            </div>
            <footer>Copyright 2026</footer>
        </body>
    </html>
    """
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.text = mock_html

    with patch("requests.get", return_value=mock_resp):
        jd = fetch_live_jd("https://boards.greenhouse.io/stripe/jobs/12345")
        assert "Lead our core growth initiatives" in jd
        assert "• 5+ years required" in jd
        assert "Header Nav" not in jd
        assert "Copyright" not in jd


def test_fetch_live_jd_404_handling():
    mock_resp = MagicMock()
    mock_resp.status_code = 404
    with patch("requests.get", return_value=mock_resp):
        res = fetch_live_jd("https://boards.greenhouse.io/stripe/jobs/99999")
        assert "HTTP 404" in res


def test_db_location_filtering():
    job1 = JobPosting(
        title="Frontend Engineer",
        company="Company A",
        location="Remote, India",
        source="linkedin",
        apply_type=ApplyType.external_ats,
        posted_at_utc=datetime.now(timezone.utc),
    )
    job2 = JobPosting(
        title="Backend Engineer",
        company="Company B",
        location="Hyderabad, Telangana",
        source="indeed",
        apply_type=ApplyType.external_ats,
        posted_at_utc=datetime.now(timezone.utc),
    )
    jobee_db.upsert_job(job1)
    jobee_db.upsert_job(job2)

    remote_jobs = jobee_db.get_all_jobs(location="Remote")
    assert len(remote_jobs) == 1
    assert remote_jobs[0]["company"] == "Company A"

    hyd_jobs = jobee_db.get_all_jobs(location="Hyderabad")
    assert len(hyd_jobs) == 1
    assert hyd_jobs[0]["company"] == "Company B"


def test_db_update_job_jd():
    job = JobPosting(
        title="Product Manager",
        company="Jobee Co",
        location="Remote",
        source="greenhouse",
        apply_type=ApplyType.external_ats,
        posted_at_utc=datetime.now(timezone.utc),
    )
    jobee_db.upsert_job(job)

    # Initial JD is None
    record = jobee_db.get_job_by_hash(job.job_hash)
    assert record["jd_text"] is None

    # Update with live scraped text
    scraped_jd = "Full scraped job description text with lots of details."
    ok = jobee_db.update_job_jd(job.job_hash, scraped_jd)
    assert ok is True

    updated_record = jobee_db.get_job_by_hash(job.job_hash)
    assert updated_record["jd_text"] == scraped_jd
