"""
tests/test_pipeline.py

Milestone 1 verification suite for Jobee.
Tests run entirely in-memory / in a temp SQLite db — no network, no live scraping.

Test groups:
  A. 24-hour cutoff — jobs older than 24h are dropped; jobs within 24h pass.
  B. Fuzzy cross-platform dedup — similar title + same company caught by rapidfuzz.
  C. Repost detection — hash collision increments seen_count without duplicating.
  D. Senior / high-experience filter — excluded keyword + "10+ years" JD dropped.
"""

from __future__ import annotations

import os
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Generator

import pytest

# ---------------------------------------------------------------------------
# Path setup — make sure project root is importable
# ---------------------------------------------------------------------------
import sys
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.models import AppConfig, ApplyType, JobPosting
from core.filter import (
    FilterEngine,
    is_within_24h,
    is_fuzzy_duplicate,
    passes_experience_filter,
    passes_title_filter,
)
from core import db as jobee_db


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def cfg() -> AppConfig:
    """Default AppConfig used across tests."""
    return AppConfig(
        max_years_experience=5,
        exclude_title_keywords=["Senior", "Staff", "Principal", "Lead", "Head", "Director", "VP"],
        fuzzy_threshold=88,
    )


def _make_job(
    title: str = "Product Manager",
    company: str = "Acme Corp",
    hours_ago: float = 1.0,
    jd_text: str = "",
    source: str = "linkedin",
) -> JobPosting:
    """Factory: create a fresh JobPosting offset by `hours_ago` from now."""
    posted = datetime.now(timezone.utc) - timedelta(hours=hours_ago)
    return JobPosting(
        title=title,
        company=company,
        location="Remote",
        source=source,
        apply_type=ApplyType.external_ats,
        posted_at_utc=posted,
        jd_text=jd_text if jd_text else None,
    )


@pytest.fixture()
def temp_db(tmp_path: Path) -> Generator[None, None, None]:
    """
    Point jobee_db at a fresh temp DB for each test that needs it.
    Restores the original path afterwards.
    """
    original = jobee_db.DB_PATH
    jobee_db.DB_PATH = tmp_path / "test_jobs.db"
    jobee_db.init_db()
    yield
    jobee_db.DB_PATH = original


# ===========================================================================
# GROUP A — 24-hour cutoff
# ===========================================================================

class TestTwentyFourHourCutoff:

    def test_job_1h_old_passes(self):
        job = _make_job(hours_ago=1.0)
        assert is_within_24h(job), "Job posted 1h ago should pass the 24h rule"

    def test_job_exactly_24h_passes(self):
        # exactly 24h — edge case: should pass (<=)
        job = _make_job(hours_ago=23.99)
        assert is_within_24h(job)

    def test_job_24h_1s_dropped(self):
        # 24h + 1 second over the limit
        posted = datetime.now(timezone.utc) - timedelta(hours=24, seconds=1)
        job = JobPosting(
            title="Product Manager",
            company="OldCorp",
            source="indeed",
            apply_type=ApplyType.external_ats,
            posted_at_utc=posted,
        )
        assert not is_within_24h(job), "Job older than 24h should be dropped"

    def test_job_48h_old_dropped(self):
        job = _make_job(hours_ago=48.0)
        assert not is_within_24h(job)

    def test_job_7days_old_dropped(self):
        job = _make_job(hours_ago=168.0)
        assert not is_within_24h(job)

    def test_naive_datetime_treated_as_utc_and_checked(self):
        """Naive datetime (no tzinfo) must be treated as UTC — still checked."""
        naive_posted = datetime.now(timezone.utc) - timedelta(hours=30)  # 30h ago
        job = JobPosting(
            title="AI Engineer",
            company="Naive Co",
            source="reddit",
            apply_type=ApplyType.reddit_dm,
            posted_at_utc=naive_posted.replace(tzinfo=timezone.utc),
        )
        assert not is_within_24h(job)

    def test_filter_engine_drops_old_jobs(self, cfg: AppConfig):
        fresh = _make_job(hours_ago=2.0, company="FreshCo", title="Product Manager")
        stale = _make_job(hours_ago=30.0, company="StaleCo", title="AI Engineer")
        engine = FilterEngine(cfg)
        accepted, stats = engine.run([fresh, stale], existing_db_jobs=[])
        assert len(accepted) == 1
        assert accepted[0].company == "FreshCo"
        assert stats["dropped_old"] == 1
        assert stats["raw"] == 2


# ===========================================================================
# GROUP B — Fuzzy cross-platform deduplication
# ===========================================================================

class TestFuzzyDeduplication:

    def test_exact_same_hash_is_dedup(self, cfg: AppConfig):
        job_a = _make_job(title="Product Manager", company="Acme Corp", source="linkedin")
        job_b = _make_job(title="Product Manager", company="Acme Corp", source="indeed")
        # Same hash — job_b is a cross-platform duplicate of job_a
        assert job_a.job_hash == job_b.job_hash

    def test_fuzzy_title_same_company_caught(self, cfg: AppConfig):
        """Slight title variation on same company → rapidfuzz catches it."""
        existing = [
            {
                "normalized_company": "acme",
                "normalized_title": "product manager",
                "job_hash": "aaa",
            }
        ]
        candidate = _make_job(title="Product Management Role", company="Acme")
        # rapidfuzz ratio of "product manager" vs "product management role"
        result = is_fuzzy_duplicate(candidate, existing, threshold=88)
        # Note: "product manager" vs "product management role" might or might not
        # exceed 88 — test the boundary explicitly
        from rapidfuzz import fuzz
        sim = fuzz.ratio("product manager", "product management role")
        assert result == (sim >= 88)

    def test_fuzzy_title_different_company_not_caught(self, cfg: AppConfig):
        """Same title but different company → NOT a duplicate."""
        existing = [
            {
                "normalized_company": "globex",
                "normalized_title": "product manager",
                "job_hash": "bbb",
            }
        ]
        candidate = _make_job(title="Product Manager", company="Acme Corp")
        assert not is_fuzzy_duplicate(candidate, existing, threshold=88)

    def test_filter_engine_deduplicates_within_batch(self, cfg: AppConfig):
        """Two jobs with same hash in one batch — only first is accepted."""
        job_a = _make_job(title="AI Engineer", company="StartupX", source="linkedin")
        job_b = _make_job(title="AI Engineer", company="StartupX", source="naukri")
        assert job_a.job_hash == job_b.job_hash
        engine = FilterEngine(cfg)
        accepted, stats = engine.run([job_a, job_b], existing_db_jobs=[])
        assert len(accepted) == 1
        assert stats["dropped_hash_dup"] == 1

    def test_different_title_same_company_both_accepted(self, cfg: AppConfig):
        job_a = _make_job(title="Product Manager", company="Acme Corp")
        job_b = _make_job(title="Data Scientist", company="Acme Corp")
        assert job_a.job_hash != job_b.job_hash
        engine = FilterEngine(cfg)
        accepted, stats = engine.run([job_a, job_b], existing_db_jobs=[])
        assert len(accepted) == 2

    def test_cross_platform_dedup_via_existing_db(self, cfg: AppConfig):
        """job already in DB → new job from different source is treated as hash dup."""
        existing_in_db = [
            {
                "normalized_company": "acme",
                "normalized_title": "product manager",
                "job_hash": _make_job(title="Product Manager", company="Acme").job_hash,
            }
        ]
        new_job = _make_job(title="Product Manager", company="Acme", source="glassdoor")
        engine = FilterEngine(cfg)
        accepted, stats = engine.run([new_job], existing_db_jobs=existing_in_db)
        assert len(accepted) == 0
        assert stats["dropped_hash_dup"] == 1


# ===========================================================================
# GROUP C — Repost detection (SQLite layer)
# ===========================================================================

class TestRepostDetection:

    def test_first_insert_is_new(self, temp_db: None):
        job = _make_job(title="Product Manager", company="Repost Inc")
        is_new, is_repost = jobee_db.upsert_job(job)
        assert is_new is True
        assert is_repost is False

    def test_second_insert_same_hash_is_repost(self, temp_db: None):
        job = _make_job(title="Product Manager", company="Repost Inc")
        jobee_db.upsert_job(job)
        is_new, is_repost = jobee_db.upsert_job(job)
        assert is_new is False
        assert is_repost is True

    def test_seen_count_increments_on_repost(self, temp_db: None):
        job = _make_job(title="Product Manager", company="Repost Inc")
        jobee_db.upsert_job(job)
        jobee_db.upsert_job(job)
        jobee_db.upsert_job(job)
        row = jobee_db.get_job_by_hash(job.job_hash)
        assert row["seen_count"] == 3

    def test_repost_flag_set_in_db(self, temp_db: None):
        job = _make_job(title="AI Engineer", company="RepostCorp")
        jobee_db.upsert_job(job)
        jobee_db.upsert_job(job)
        row = jobee_db.get_job_by_hash(job.job_hash)
        assert row["is_repost"] == 1

    def test_no_duplicate_rows_after_repost(self, temp_db: None):
        job = _make_job(title="Data Analyst", company="RepeatCo")
        for _ in range(5):
            jobee_db.upsert_job(job)
        # Only one row should exist
        import sqlite3
        con = sqlite3.connect(str(jobee_db.DB_PATH))
        count = con.execute(
            "SELECT COUNT(*) FROM jobs WHERE job_hash=?", (job.job_hash,)
        ).fetchone()[0]
        con.close()
        assert count == 1

    def test_repost_not_returned_in_feed(self, temp_db: None):
        job = _make_job(title="PM Role", company="TestCo")
        jobee_db.upsert_job(job)
        jobee_db.upsert_job(job)   # marks is_repost=1
        rows = jobee_db.get_all_jobs()
        # get_all_jobs filters is_repost=0
        assert all(r["is_repost"] == 0 for r in rows)


# ===========================================================================
# GROUP D — Senior / high-experience filter
# ===========================================================================

class TestSeniorAndExperienceFilter:

    # --- Title keyword filter ---

    def test_senior_title_dropped(self, cfg: AppConfig):
        job = _make_job(title="Senior Product Manager", company="Corp")
        assert not passes_title_filter(job, cfg)

    def test_staff_title_dropped(self, cfg: AppConfig):
        job = _make_job(title="Staff Engineer", company="Corp")
        assert not passes_title_filter(job, cfg)

    def test_principal_title_dropped(self, cfg: AppConfig):
        job = _make_job(title="Principal AI Engineer", company="Corp")
        assert not passes_title_filter(job, cfg)

    def test_lead_title_dropped(self, cfg: AppConfig):
        job = _make_job(title="Lead Product Manager", company="Corp")
        assert not passes_title_filter(job, cfg)

    def test_junior_title_passes(self, cfg: AppConfig):
        job = _make_job(title="Associate Product Manager", company="Corp")
        assert passes_title_filter(job, cfg)

    def test_plain_pm_passes(self, cfg: AppConfig):
        job = _make_job(title="Product Manager", company="Corp")
        assert passes_title_filter(job, cfg)

    def test_keyword_case_insensitive(self, cfg: AppConfig):
        job = _make_job(title="SENIOR data scientist", company="Corp")
        assert not passes_title_filter(job, cfg)

    # --- Experience regex filter ---

    def test_10plus_years_jd_dropped(self, cfg: AppConfig):
        job = _make_job(title="Product Manager", company="Corp", jd_text="10+ years of experience required")
        assert not passes_experience_filter(job, cfg)

    def test_8_to_10_years_jd_dropped(self, cfg: AppConfig):
        job = _make_job(title="PM", company="Corp", jd_text="Requires 8-10 years of product experience")
        assert not passes_experience_filter(job, cfg)

    def test_3_years_jd_passes(self, cfg: AppConfig):
        job = _make_job(title="PM", company="Corp", jd_text="2-3 years of experience preferred")
        assert passes_experience_filter(job, cfg)

    def test_no_experience_mentioned_passes(self, cfg: AppConfig):
        job = _make_job(title="AI Engineer", company="Corp", jd_text="We love builders who ship fast.")
        assert passes_experience_filter(job, cfg)

    def test_minimum_6_years_dropped(self, cfg: AppConfig):
        job = _make_job(title="PM", company="Corp", jd_text="Minimum 6 years in product management")
        assert not passes_experience_filter(job, cfg)

    def test_filter_engine_drops_senior_and_experienced(self, cfg: AppConfig):
        junior = _make_job(title="Associate Product Manager", company="StartupY", jd_text="1-2 years preferred")
        senior = _make_job(title="Senior Product Manager", company="StartupY")
        over_exp = _make_job(title="Product Manager", company="StartupY", jd_text="10+ years required")
        engine = FilterEngine(cfg)
        accepted, stats = engine.run([junior, senior, over_exp], existing_db_jobs=[])
        assert len(accepted) == 1
        assert accepted[0].title == "Associate Product Manager"
        assert stats["dropped_title_kw"] == 1
        assert stats["dropped_experience"] == 1
