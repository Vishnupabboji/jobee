"""
adapters/ats_adapter.py

Polls public Greenhouse and Lever JSON APIs for job boards.

Architecture adapted from job-scraper (https://github.com/ghiarishi/job-scraper):
  - Greenhouse: boards-api.greenhouse.io/v1/boards/{slug}/jobs?content=true
  - Lever:      api.lever.co/v0/postings/{slug}?mode=json
  - Company slugs are configured in config.yaml → ats_slugs

Jobee-specific additions:
  - posted_at_utc derived from Greenhouse updated_at / Lever createdAt (Unix ms)
  - <24h filter applied by FilterEngine downstream (but we still parse times here)
  - Returns List[JobPosting] via BaseAdapter
  - Per-slug try/except isolation — one bad slug never crashes others
"""

from __future__ import annotations

import logging
import time
from datetime import datetime, timezone
from typing import Optional

import requests

from adapters.base import BaseAdapter
from core.models import AppConfig, ApplyType, JobPosting

log = logging.getLogger(__name__)

_HEADERS = {
    "User-Agent": "Jobee/1.0 (open-source job discovery; local use)",
    "Accept": "application/json",
}
_TIMEOUT = 12  # seconds


def _get_json(url: str) -> Optional[dict | list]:
    """HTTP GET → parsed JSON, or None on any error."""
    try:
        resp = requests.get(url, headers=_HEADERS, timeout=_TIMEOUT)
        if resp.status_code == 404:
            log.debug("ATS: 404 for %s (slug may be wrong)", url)
            return None
        resp.raise_for_status()
        return resp.json()
    except requests.exceptions.HTTPError as exc:
        code = exc.response.status_code if exc.response else "?"
        if code == 429:
            log.warning("ATS: 429 rate-limited on %s", url)
        else:
            log.warning("ATS: HTTP %s for %s", code, url)
    except requests.exceptions.RequestException as exc:
        log.warning("ATS: request error for %s: %s", url, exc)
    return None


def _utc_from_iso(s: Optional[str]) -> Optional[datetime]:
    """Parse ISO-8601 string → UTC-aware datetime."""
    if not s:
        return None
    s = s.rstrip("Z")
    for fmt in ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
        try:
            return datetime.strptime(s, fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            pass
    return None


def _utc_from_unix_ms(ts: Optional[int]) -> Optional[datetime]:
    """Convert Unix millisecond timestamp → UTC-aware datetime."""
    if not ts:
        return None
    try:
        return datetime.fromtimestamp(ts / 1000.0, tz=timezone.utc)
    except (OSError, ValueError, OverflowError):
        return None


# ---------------------------------------------------------------------------
# Greenhouse
# ---------------------------------------------------------------------------

def _fetch_greenhouse(slug: str) -> list[JobPosting]:
    """
    Greenhouse public board API:
      GET boards-api.greenhouse.io/v1/boards/{slug}/jobs?content=true
    Returns jobs with full content (title, location, content, updated_at).
    Reference: job-scraper's greenhouse.io URL cleaning + getJobInfo() pattern.
    """
    url = f"https://boards-api.greenhouse.io/v1/boards/{slug}/jobs?content=true"
    data = _get_json(url)
    if not data or not isinstance(data, dict):
        return []

    raw_jobs = data.get("jobs", [])
    if not raw_jobs:
        log.info("Greenhouse %s: 0 jobs returned", slug)
        return []

    jobs: list[JobPosting] = []
    for item in raw_jobs:
        title = item.get("title", "Unknown")
        location = ""
        loc_obj = item.get("location")
        if isinstance(loc_obj, dict):
            location = loc_obj.get("name", "")
        elif isinstance(loc_obj, str):
            location = loc_obj

        apply_url = item.get("absolute_url", "")
        jd_text = item.get("content", "")  # HTML or plain text JD
        # Strip basic HTML tags from content for storage
        import re
        jd_text = re.sub(r"<[^>]+>", " ", jd_text or "").strip()

        # Greenhouse updated_at is ISO-8601
        updated_at = _utc_from_iso(item.get("updated_at"))
        posted_at = updated_at or datetime.now(timezone.utc)

        try:
            job = JobPosting(
                title=title,
                company=slug.replace("-", " ").title(),
                location=location,
                source="greenhouse",
                apply_type=ApplyType.external_ats,
                apply_url=apply_url,
                posted_at_utc=posted_at,
                jd_text=jd_text[:5000] if jd_text else None,
            )
            jobs.append(job)
        except Exception as exc:
            log.debug("Greenhouse %s: skipping malformed job: %s", slug, exc)

    log.info("Greenhouse %s: %d raw jobs fetched", slug, len(jobs))
    return jobs


# ---------------------------------------------------------------------------
# Lever
# ---------------------------------------------------------------------------

def _fetch_lever(slug: str) -> list[JobPosting]:
    """
    Lever public postings API:
      GET api.lever.co/v0/postings/{slug}?mode=json
    Returns array of postings with text, categories, createdAt (Unix ms).
    Reference: job-scraper's lever.co URL cleaning + getJobInfo() pattern,
    but we use the JSON API directly instead of HTML scraping.
    """
    url = f"https://api.lever.co/v0/postings/{slug}?mode=json"
    data = _get_json(url)
    if not data or not isinstance(data, list):
        return []

    jobs: list[JobPosting] = []
    for item in data:
        title = item.get("text", "Unknown")
        categories = item.get("categories", {})
        location = categories.get("location") or categories.get("allLocations", [""])[0] if isinstance(categories.get("allLocations"), list) else ""
        apply_url = item.get("hostedUrl") or item.get("applyUrl", "")

        # createdAt is Unix milliseconds (Lever API)
        created_ms = item.get("createdAt")
        posted_at = _utc_from_unix_ms(created_ms) or datetime.now(timezone.utc)

        # Build JD text from lists section
        lists = item.get("lists", [])
        jd_parts = []
        for lst in lists:
            if isinstance(lst, dict):
                jd_parts.append(lst.get("text", ""))
                jd_parts.append(lst.get("content", ""))
        import re
        jd_text = re.sub(r"<[^>]+>", " ", " ".join(jd_parts)).strip()

        try:
            job = JobPosting(
                title=title,
                company=slug.replace("-", " ").title(),
                location=location or "",
                source="lever",
                apply_type=ApplyType.external_ats,
                apply_url=apply_url,
                posted_at_utc=posted_at,
                jd_text=jd_text[:5000] if jd_text else None,
            )
            jobs.append(job)
        except Exception as exc:
            log.debug("Lever %s: skipping malformed posting: %s", slug, exc)

    log.info("Lever %s: %d raw jobs fetched", slug, len(jobs))
    return jobs


# ---------------------------------------------------------------------------
# Adapter class
# ---------------------------------------------------------------------------

class ATSAdapter(BaseAdapter):
    """
    Polls Greenhouse and Lever public JSON boards.
    Slugs configured in config.yaml → ats_slugs.
    Each slug is independently try/excepted.
    """

    name = "ats"

    def fetch(self) -> list[JobPosting]:
        if not self.cfg.enable_ats:
            log.info("ATS adapter disabled.")
            return []

        slugs_cfg = self.cfg.ats_slugs
        gh_slugs: list[str] = slugs_cfg.get("greenhouse", [])
        lv_slugs: list[str] = slugs_cfg.get("lever", [])

        if not gh_slugs and not lv_slugs:
            log.info("ATS adapter: no slugs configured. Add them to config.yaml → ats_slugs.")
            return []

        all_jobs: list[JobPosting] = []

        for slug in gh_slugs:
            try:
                jobs = _fetch_greenhouse(slug)
                all_jobs.extend(jobs)
            except Exception as exc:
                log.error("Greenhouse slug %r: unexpected error: %s", slug, exc, exc_info=True)
            time.sleep(1.5)

        for slug in lv_slugs:
            try:
                jobs = _fetch_lever(slug)
                all_jobs.extend(jobs)
            except Exception as exc:
                log.error("Lever slug %r: unexpected error: %s", slug, exc, exc_info=True)
            time.sleep(1.5)

        return all_jobs
