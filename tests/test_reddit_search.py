"""
tests/test_reddit_search.py

Tests for the dedicated unauthenticated Reddit search engine and import logic.
"""

import sys
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from adapters.reddit_search import (
    _parse_reddit_child,
    import_reddit_post_to_db,
    search_reddit_jobs,
)
from core import db as jobee_db


@pytest.fixture(autouse=True)
def setup_db(tmp_path):
    jobee_db.DB_PATH = tmp_path / "test_reddit_jobs.db"
    jobee_db.init_db()


def test_parse_reddit_child_valid():
    raw_post = {
        "id": "abc1234",
        "title": "[Hiring] Python & FastAPI Developer in Hyderabad",
        "selftext": "We are looking for a backend engineer. Contact: hiring@techstartup.in",
        "author": "tech_recruiter",
        "permalink": "/r/forhire/comments/abc1234/hiring_python_fastapi/",
        "subreddit": "forhire",
        "created_utc": datetime.now(timezone.utc).timestamp() - 7200,  # 2 hours ago
    }
    parsed = _parse_reddit_child(raw_post, "forhire")
    assert parsed is not None
    assert parsed["id"] == "abc1234"
    assert "Python & FastAPI" in parsed["title"]
    assert parsed["subreddit"] == "r/forhire"
    assert parsed["contact_info"] == "hiring@techstartup.in"
    assert "2.0h ago" in parsed["age_str"]
    assert "https://www.reddit.com/r/forhire" in parsed["url"]


def test_parse_reddit_child_excludes_for_hire():
    raw_post = {
        "title": "[For Hire] Senior Full Stack Dev available for freelance",
        "selftext": "Portfolio at example.com",
        "author": "dev_person",
        "permalink": "/r/forhire/comments/xyz999/",
    }
    parsed = _parse_reddit_child(raw_post, "forhire")
    assert parsed is None


def test_search_reddit_jobs_mock():
    mock_json = {
        "data": {
            "children": [
                {
                    "data": {
                        "id": "p1",
                        "title": "[Hiring] AI Engineer - Hyderabad",
                        "selftext": "Apply at jobs@ai.io",
                        "author": "founder_ai",
                        "permalink": "/r/IndianStartups/comments/p1/hiring/",
                        "subreddit": "IndianStartups",
                        "created_utc": datetime.now(timezone.utc).timestamp() - 3600,
                    }
                }
            ]
        }
    }
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = mock_json

    with patch("requests.get", return_value=mock_resp):
        results = search_reddit_jobs("AI Engineer", location="Hyderabad")
        assert len(results) >= 1
        assert results[0]["title"] == "[Hiring] AI Engineer - Hyderabad"
        assert results[0]["contact_info"] == "jobs@ai.io"


def test_import_reddit_post_to_db():
    post = {
        "id": "test_import_123",
        "title": "Looking for Product Manager (Remote / Hyderabad)",
        "url": "https://www.reddit.com/r/startups/comments/123/pm_role/",
        "subreddit": "r/startups",
        "contact_info": "pm@startups.com",
        "preview": "We are hiring our first PM.",
    }
    ok, msg = import_reddit_post_to_db(post, default_location="Hyderabad")
    assert ok is True

    # Verify job is now in jobs.db
    all_jobs = jobee_db.get_all_jobs(source="reddit")
    assert len(all_jobs) == 1
    assert all_jobs[0]["title"] == "Looking for Product Manager (Remote / Hyderabad)"
    assert all_jobs[0]["location"] == "Hyderabad"
    assert all_jobs[0]["status"] == "To Apply"
