"""
tests/test_db_search.py

Verifies flexible filtering in SQLite:
  - By role only
  - By location only (e.g. Hyderabad)
  - By both role and location
  - By freeform keywords
"""

import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core import db as jobee_db
from core.models import ApplyType, JobPosting


@pytest.fixture(autouse=True)
def setup_db(tmp_path):
    jobee_db.DB_PATH = tmp_path / "test_flex_search.db"
    jobee_db.init_db()

    # Seed 3 distinct jobs
    j1 = JobPosting(
        title="Product Manager",
        company="Swiggy",
        location="Hyderabad, Telangana",
        source="linkedin",
        apply_type=ApplyType.external_ats,
        posted_at_utc=datetime.now(timezone.utc),
    )
    j2 = JobPosting(
        title="AI Engineer",
        company="Microsoft",
        location="Hyderabad",
        source="linkedin",
        apply_type=ApplyType.external_ats,
        posted_at_utc=datetime.now(timezone.utc),
    )
    j3 = JobPosting(
        title="Frontend Developer",
        company="Razorpay",
        location="Bangalore",
        source="indeed",
        apply_type=ApplyType.easy_apply,
        posted_at_utc=datetime.now(timezone.utc),
    )
    jobee_db.upsert_job(j1)
    jobee_db.upsert_job(j2)
    jobee_db.upsert_job(j3)


def test_filter_by_location_only():
    results = jobee_db.get_all_jobs(location="Hyderabad")
    assert len(results) == 2
    titles = [r["title"] for r in results]
    assert "Product Manager" in titles
    assert "AI Engineer" in titles


def test_filter_by_role_only():
    results = jobee_db.get_all_jobs(role="Product Manager")
    assert len(results) == 1
    assert results[0]["company"] == "Swiggy"


def test_filter_by_both_role_and_location():
    results = jobee_db.get_all_jobs(role="AI Engineer", location="Hyderabad")
    assert len(results) == 1
    assert results[0]["company"] == "Microsoft"

    no_match = jobee_db.get_all_jobs(role="Frontend Developer", location="Hyderabad")
    assert len(no_match) == 0


def test_filter_by_freeform_keyword():
    results = jobee_db.get_all_jobs(keyword="Razorpay")
    assert len(results) == 1
    assert results[0]["title"] == "Frontend Developer"
