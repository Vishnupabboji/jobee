"""
core/pipeline.py

Orchestrates the full scan pipeline with parallel multi-threading:
  1. Concurrently run enabled adapters (JobSpy, ATS)
  2. For each adapter: apply FilterEngine → upsert to SQLite → log ScrapeRun
  3. Returns a summary dict for the UI/logs
"""

from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from typing import Optional

from core import db as jobee_db
from core.config_loader import load_config
from core.filter import FilterEngine
from core.models import AppConfig, ScrapeRun, ScrapeStatus

log = logging.getLogger(__name__)


def _get_adapters(cfg: AppConfig):
    """Instantiate all enabled adapters. Import lazily to avoid circular imports."""
    adapters = []
    if cfg.enable_jobspy:
        try:
            from adapters.jobspy_adapter import JobSpyAdapter
            adapters.append(JobSpyAdapter(cfg))
        except Exception as exc:
            log.error("Failed to load JobSpyAdapter: %s", exc)
    if cfg.enable_ats:
        try:
            from adapters.ats_adapter import ATSAdapter
            adapters.append(ATSAdapter(cfg))
        except Exception as exc:
            log.error("Failed to load ATSAdapter: %s", exc)
    if cfg.enable_reddit:
        try:
            from adapters.reddit_adapter import RedditAdapter
            adapters.append(RedditAdapter(cfg))
        except Exception as exc:
            log.error("Failed to load RedditAdapter: %s", exc)
    return adapters


def _process_adapter(adapter, engine: FilterEngine, existing_db: list[dict], resume_drive_url: Optional[str]) -> tuple[str, dict]:
    """Execute a single adapter, filter its results, and persist to SQLite."""
    started = datetime.now(timezone.utc)
    run = ScrapeRun(source=adapter.name, started_at=started)

    try:
        raw_jobs = adapter.fetch()
        run.raw_count = len(raw_jobs)

        accepted, stats = engine.run(raw_jobs, existing_db)
        run.dropped_old_count = stats["dropped_old"]
        run.filtered_out_count = stats["dropped_title_kw"] + stats["dropped_experience"]
        run.duplicate_count = stats["dropped_hash_dup"] + stats["dropped_fuzzy_dup"]

        new_count = 0
        repost_count = 0
        for job in accepted:
            if resume_drive_url:
                job.resume_drive_url = resume_drive_url
            is_new, is_repost = jobee_db.upsert_job(job)
            if is_new:
                new_count += 1
                existing_db.append({
                    "normalized_company": job.normalized_company,
                    "normalized_title": job.normalized_title,
                    "job_hash": job.job_hash,
                })
            elif is_repost:
                repost_count += 1

        run.new_count = new_count
        run.duplicate_count += repost_count
        run.status = ScrapeStatus.success

    except Exception as exc:
        log.error("Adapter %s failed: %s", adapter.name, exc, exc_info=True)
        run.status = ScrapeStatus.failed
        run.error_message = str(exc)

    run.finished_at = datetime.now(timezone.utc)
    jobee_db.insert_scrape_run(run)

    return adapter.name, {
        "status": run.status.value,
        "raw": run.raw_count,
        "dropped_old": run.dropped_old_count,
        "filtered_out": run.filtered_out_count,
        "duplicates": run.duplicate_count,
        "new": run.new_count,
        "error": run.error_message,
    }


def run_scan(
    cfg: Optional[AppConfig] = None,
    resume_drive_url: Optional[str] = None,
) -> dict:
    """
    Run all enabled adapters in parallel → filter → upsert to DB → log runs.
    Returns a summary dict: {adapter_name: stats, ...}
    """
    if cfg is None:
        cfg = load_config()

    jobee_db.init_db()
    engine = FilterEngine(cfg)
    summary: dict[str, dict] = {}

    existing_db = jobee_db.get_all_jobs(limit=5000)
    adapters = _get_adapters(cfg)
    if not adapters:
        log.warning("No adapters active — check config.yaml.")
        return summary

    # Run adapters concurrently
    with ThreadPoolExecutor(max_workers=len(adapters)) as executor:
        futures = {
            executor.submit(_process_adapter, adapter, engine, existing_db, resume_drive_url): adapter.name
            for adapter in adapters
        }
        for future in as_completed(futures):
            adapter_name, stats = future.result()
            summary[adapter_name] = stats
            log.info("Adapter %s complete: new=%d raw=%d", adapter_name, stats["new"], stats["raw"])

    return summary
