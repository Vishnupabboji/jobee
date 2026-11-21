"""
scripts/dry_run.py

Milestone 2 live dry-run verification script.
Tests JobSpy, Reddit, and ATS adapters with live networks,
proves zero crashes on rate limits, and prints a summary table of real jobs.
"""

import logging
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core.models import AppConfig
from core.pipeline import run_scan
from core import db as jobee_db

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")


def main():
    print("=" * 70)
    print("JOBEE LIVE DRY-RUN TEST")
    print("=" * 70)

    # Configure a focused live scan for verification
    cfg = AppConfig(
        roles=["Product Manager", "AI Engineer"],
        locations=["India", "Remote"],
        jobspy_sites=["linkedin", "indeed"],
        subreddits=["forhire", "hiring"],
        enable_jobspy=True,
        enable_reddit=True,
        enable_ats=True,
        ats_slugs={
            "greenhouse": ["stripe"],  # Stripe uses Greenhouse boards
            "lever": []
        }
    )

    print("\n[1/3] Initializing DB & Running live scan pipeline...")
    summary = run_scan(cfg)

    print("\n[2/3] Scraper Runs Summary Table:")
    print("-" * 75)
    print(f"{'Source':<15} | {'Status':<10} | {'Raw':<6} | {'<24h New':<9} | {'Duplicates':<10} | {'Filtered':<8}")
    print("-" * 75)
    for source, stats in summary.items():
        print(f"{source:<15} | {stats['status']:<10} | {stats['raw']:<6} | {stats['new']:<9} | {stats['duplicates']:<10} | {stats['filtered_out']:<8}")
    print("-" * 75)

    print("\n[3/3] Inspecting Top 5 Saved Jobs in SQLite:")
    jobs = jobee_db.get_all_jobs(limit=5)
    if not jobs:
        print("No active jobs within 24h passed all filters.")
    else:
        for idx, j in enumerate(jobs, 1):
            print(f"  {idx}. [{j['source'].upper()}] {j['title']} @ {j['company']} ({j['location']})")
            print(f"     Apply: {j['apply_url'] or j['contact_info']}")
            print(f"     Status: {j['status']} | Hash: {j['job_hash'][:12]}...")

    print("\nDry run completed successfully with zero crashes!")


if __name__ == "__main__":
    main()
