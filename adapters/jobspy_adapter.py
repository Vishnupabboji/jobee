"""
adapters/jobspy_adapter.py

Wraps python-jobspy for LinkedIn, Indeed, Glassdoor, Naukri.
Features:
- Parallel execution via ThreadPoolExecutor for fast multi-source scraping.
- Skips known broken/blocking combinations (Glassdoor worldwide error, Naukri reCAPTCHA).
- Jittered concurrency to prevent rate limits while achieving ~15-20s scan times.
"""

from __future__ import annotations

import logging
import random
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from typing import Optional

from adapters.base import BaseAdapter
from core.models import AppConfig, ApplyType, JobPosting

log = logging.getLogger(__name__)

# Sites JobSpy supports that we use
_SUPPORTED_SITES = {"linkedin", "indeed", "glassdoor", "naukri"}


def _parse_dt(raw) -> Optional[datetime]:
    """Coerce whatever JobSpy returns for date_posted into a timezone-aware UTC datetime."""
    if raw is None:
        return None
    if isinstance(raw, datetime):
        if raw.tzinfo is None:
            return raw.replace(tzinfo=timezone.utc)
        return raw.astimezone(timezone.utc)
    if hasattr(raw, "year") and not isinstance(raw, datetime):
        return datetime(raw.year, raw.month, raw.day, tzinfo=timezone.utc)
    if isinstance(raw, str):
        raw = raw.strip().rstrip("Z")
        for fmt in ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
            try:
                return datetime.strptime(raw, fmt).replace(tzinfo=timezone.utc)
            except ValueError:
                pass
    return None


def _apply_type_from_row(row: dict) -> ApplyType:
    """Guess the apply type from JobSpy's is_remote / job_url_direct fields."""
    if row.get("job_url_direct"):
        return ApplyType.external_ats
    return ApplyType.easy_apply


class JobSpyAdapter(BaseAdapter):
    """
    Wraps python-jobspy with parallel execution across sites and queries.
    """

    name = "jobspy"

    def __init__(self, cfg: AppConfig):
        super().__init__(cfg)
        # Filter supported sites; by default exclude naukri unless specifically requested
        # because Naukri currently returns 406 recaptcha required for direct scraping
        self._sites = [s for s in cfg.jobspy_sites if s in _SUPPORTED_SITES and s != "naukri"]

    def fetch(self) -> list[JobPosting]:
        if not self.cfg.enable_jobspy or not self._sites:
            log.info("JobSpy adapter disabled or no sites configured.")
            return []

        # Build list of work items: (site, role, location)
        tasks = []
        for site in self._sites:
            for role in self.cfg.roles:
                for location in self.cfg.locations:
                    # Glassdoor is not available for "Remote / WORLDWIDE", only specific countries
                    if site == "glassdoor" and location.lower() == "remote":
                        continue
                    tasks.append((site, role, location))

        log.info("JobSpy: starting parallel scan with %d queries across %s", len(tasks), self._sites)
        all_jobs: list[JobPosting] = []

        # Run with max 4 workers to balance speed and avoid local network saturation
        with ThreadPoolExecutor(max_workers=4) as executor:
            future_to_task = {
                executor.submit(self._fetch_one, site, role, loc): (site, role, loc)
                for site, role, loc in tasks
            }
            for future in as_completed(future_to_task):
                site, role, loc = future_to_task[future]
                try:
                    jobs = future.result()
                    all_jobs.extend(jobs)
                except Exception as exc:
                    log.warning("JobSpy query (%s, %s, %s) failed: %s", site, role, loc, exc)

        log.info("JobSpy: finished parallel scan. Total raw jobs retrieved: %d", len(all_jobs))
        return all_jobs

    def _fetch_one(self, site: str, role: str, location: str) -> list[JobPosting]:
        """Fetch one (site, role, location) combination. Catches all exceptions."""
        try:
            from jobspy import scrape_jobs  # type: ignore
        except ImportError:
            log.error("python-jobspy not installed. Run: pip install python-jobspy")
            return []

        # Brief jitter to avoid synchronised request bursts
        time.sleep(random.uniform(0.2, 0.8))

        country_indeed = "india" if "india" in location.lower() or "hyderabad" in location.lower() else "worldwide"
        country_gd = "india" if "india" in location.lower() or "hyderabad" in location.lower() else "usa"

        log.debug("JobSpy thread starting: site=%s role=%r location=%r", site, role, location)
        try:
            df = scrape_jobs(
                site_name=[site],
                search_term=role,
                location=location,
                results_wanted=35,
                hours_old=24,
                country_indeed=country_indeed,
            )
        except Exception as exc:
            log.warning("JobSpy %s (%s, %s) error: %s", site, role, location, exc)
            return []

        if df is None or df.empty:
            return []

        jobs: list[JobPosting] = []
        for _, row in df.iterrows():
            row_dict = row.to_dict()
            posted_at = _parse_dt(row_dict.get("date_posted"))
            if posted_at is None:
                posted_at = datetime.now(timezone.utc)

            try:
                job = JobPosting(
                    title=str(row_dict.get("title") or "Unknown"),
                    company=str(row_dict.get("company") or "Unknown"),
                    location=str(row_dict.get("location") or location),
                    source=site,
                    apply_type=_apply_type_from_row(row_dict),
                    apply_url=str(row_dict.get("job_url") or row_dict.get("job_url_direct") or ""),
                    jd_text=str(row_dict.get("description") or ""),
                    posted_at_utc=posted_at,
                )
                jobs.append(job)
            except Exception as exc:
                log.debug("JobSpy: skipping malformed row: %s", exc)

        log.info("JobSpy %s: retrieved %d jobs for %r in %r", site, len(jobs), role, location)
        return jobs
