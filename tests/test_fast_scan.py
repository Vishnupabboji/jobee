"""
tests/test_fast_scan.py

Verifies parallel scan execution speed and multi-threaded adapter coordination.
"""

import sys
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core import db as jobee_db
from core.models import AppConfig
from core.pipeline import run_scan


@pytest.fixture(autouse=True)
def setup_db(tmp_path):
    jobee_db.DB_PATH = tmp_path / "test_fast_scan.db"
    jobee_db.init_db()


def test_parallel_scan_finishes_fast():
    """Verify that multiple adapters run concurrently rather than serially."""
    cfg = AppConfig(
        roles=["Product Manager"],
        locations=["Hyderabad"],
        enable_jobspy=True,
        enable_ats=True,
        enable_reddit=False,
    )

    # Mock fetch methods to simulate 0.5s network delay each
    def mock_fetch():
        time.sleep(0.5)
        return []

    with patch("adapters.jobspy_adapter.JobSpyAdapter.fetch", side_effect=mock_fetch):
        with patch("adapters.ats_adapter.ATSAdapter.fetch", side_effect=mock_fetch):
            t0 = time.time()
            summary = run_scan(cfg)
            elapsed = time.time() - t0

            # If serial: 0.5 + 0.5 = 1.0s. If parallel: ~0.55s.
            assert elapsed < 0.9, f"Expected parallel run < 0.9s, got {elapsed:.2f}s"
            assert "jobspy" in summary
            assert "ats" in summary
