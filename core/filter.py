"""
Gatekeeper / filter engine for Jobee.

Responsibilities:
1. 24-hour freshness cutoff (timezone-aware UTC).
2. Title keyword exclusion (configurable list).
3. Experience regex detection → drop if above max_years_experience.
4. Job-hash deduplication against SQLite.
5. Fuzzy cross-platform deduplication via rapidfuzz.
"""

from __future__ import annotations

import logging
import re
from datetime import datetime, timedelta, timezone
from typing import Optional

from rapidfuzz import fuzz

from core.models import AppConfig, JobPosting

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

# Patterns that express years of experience.
# Matches things like "10+ years", "8-10 years", "minimum 6 years", "6 yrs"
_EXP_PATTERNS: list[re.Pattern] = [
    re.compile(r"(\d+)\s*\+\s*years?", re.IGNORECASE),
    re.compile(r"(\d+)\s*-\s*(\d+)\s*years?", re.IGNORECASE),
    re.compile(r"minimum\s+(\d+)\s*years?", re.IGNORECASE),
    re.compile(r"at least\s+(\d+)\s*years?", re.IGNORECASE),
    re.compile(r"(\d+)\s*(?:years?|yrs?)\s+(?:of\s+)?experience", re.IGNORECASE),
]


def _max_years_in_text(text: str) -> Optional[int]:
    """Return the highest year-number found in text, or None."""
    found: list[int] = []
    for pat in _EXP_PATTERNS:
        for m in pat.finditer(text):
            found.extend(int(g) for g in m.groups() if g is not None)
    return max(found) if found else None


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def is_within_24h(job: JobPosting) -> bool:
    """Return True if the job was posted within the last 24 hours (UTC)."""
    now = datetime.now(timezone.utc)
    posted = job.posted_at_utc

    # Ensure timezone-awareness; treat naive datetimes as UTC
    if posted.tzinfo is None:
        posted = posted.replace(tzinfo=timezone.utc)

    age = now - posted
    return age <= timedelta(hours=24, seconds=0)


def passes_title_filter(job: JobPosting, cfg: AppConfig) -> bool:
    """
    Return False (drop) if the job title contains any excluded keyword.
    Case-insensitive whole-word match.
    """
    title_lower = job.title.lower()
    for kw in cfg.exclude_title_keywords:
        pattern = r"\b" + re.escape(kw.lower()) + r"\b"
        if re.search(pattern, title_lower):
            log.debug("Dropping '%s' — excluded keyword '%s'", job.title, kw)
            return False
    return True


def passes_experience_filter(job: JobPosting, cfg: AppConfig) -> bool:
    """
    Return False (drop) if the JD requires more years than max_years_experience.
    Checks both title and jd_text.
    """
    combined = " ".join(filter(None, [job.title, job.jd_text or ""]))
    max_found = _max_years_in_text(combined)
    if max_found is not None and max_found > cfg.max_years_experience:
        log.debug(
            "Dropping '%s' — requires %d years (max %d)",
            job.title, max_found, cfg.max_years_experience
        )
        return False
    return True


def is_fuzzy_duplicate(
    candidate: JobPosting,
    existing_jobs: list[dict],
    threshold: int = 88,
) -> bool:
    """
    Return True if any existing job in the same normalised company
    has a title fuzzy-similarity >= threshold with the candidate.

    `existing_jobs` should be the list of dicts already in the DB
    (fetched cheaply by normalised_company).
    """
    for existing in existing_jobs:
        if existing.get("normalized_company") != candidate.normalized_company:
            continue
        sim = fuzz.ratio(
            candidate.normalized_title,
            existing.get("normalized_title", "")
        )
        if sim >= threshold:
            log.debug(
                "Fuzzy duplicate detected: '%s' vs '%s' (%.0f%%)",
                candidate.normalized_title,
                existing.get("normalized_title", ""),
                sim
            )
            return True
    return False


class FilterEngine:
    """
    Stateless callable pipeline. Each method is independently testable.
    Usage: engine.run(jobs, existing_db_jobs) → (accepted, stats)
    """

    def __init__(self, cfg: AppConfig):
        self.cfg = cfg

    def run(
        self,
        candidates: list[JobPosting],
        existing_db_jobs: list[dict],
    ) -> tuple[list[JobPosting], dict[str, int]]:
        """
        Apply all filters in order.
        Returns (accepted_jobs, stats_dict).
        """
        stats = {
            "raw": len(candidates),
            "dropped_old": 0,
            "dropped_title_kw": 0,
            "dropped_experience": 0,
            "dropped_hash_dup": 0,
            "dropped_fuzzy_dup": 0,
            "accepted": 0,
        }

        # Build sets for fast hash lookup
        existing_hashes: set[str] = {r.get("job_hash", "") for r in existing_db_jobs}

        accepted: list[JobPosting] = []
        # Track accepted normalised titles within this batch for intra-batch dedup
        batch_lookup: list[dict] = list(existing_db_jobs)

        for job in candidates:
            # 1. 24-hour freshness
            if not is_within_24h(job):
                stats["dropped_old"] += 1
                continue

            # 2. Title keyword filter
            if not passes_title_filter(job, self.cfg):
                stats["dropped_title_kw"] += 1
                continue

            # 3. Experience filter
            if not passes_experience_filter(job, self.cfg):
                stats["dropped_experience"] += 1
                continue

            # 4. Exact hash dedup (against DB + this batch)
            if job.job_hash in existing_hashes:
                stats["dropped_hash_dup"] += 1
                continue

            # 5. Fuzzy dedup (against DB + this batch)
            if is_fuzzy_duplicate(job, batch_lookup, self.cfg.fuzzy_threshold):
                stats["dropped_fuzzy_dup"] += 1
                continue

            # Passed all filters
            accepted.append(job)
            existing_hashes.add(job.job_hash)
            batch_lookup.append({
                "normalized_company": job.normalized_company,
                "normalized_title":   job.normalized_title,
                "job_hash":           job.job_hash,
            })

        stats["accepted"] = len(accepted)
        return accepted, stats
