"""
adapters/reddit_adapter.py

Scrapes public Reddit .json endpoints for hiring posts — no API key required.

Architecture adapted from YARS (https://github.com/datavorous/yars):
  - RandomUserAgentSession pattern (rotating user-agents via YARS sessions.py)
  - fetch_subreddit_posts() → r/{sub}/new.json with raw_json=1 + pagination via `after`
  - Retry on [429, 500, 502, 503, 504] with exponential backoff (urllib3 Retry)
  - created_utc (Unix timestamp) for exact age calculation
  - 2s polite delay between subreddit calls (from YARS's time.sleep pattern)

Jobee-specific additions:
  - Strict <24h filter on created_utc
  - Hiring post detection (title/flair whitelist + noise exclusion)
  - Regex extraction of apply URLs and contact info from post body
  - Returns List[JobPosting] via BaseAdapter interface
"""

from __future__ import annotations

import logging
import random
import re
import time
from datetime import datetime, timezone
from typing import Optional

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from adapters.base import BaseAdapter
from core.models import AppConfig, ApplyType, JobPosting

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# User-agent pool (adapted from YARS agents.py pattern)
# ---------------------------------------------------------------------------
_USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:129.0) Gecko/20100101 Firefox/129.0",
    "Mozilla/5.0 (X11; Linux x86_64; rv:128.0) Gecko/20100101 Firefox/128.0",
]

# ---------------------------------------------------------------------------
# Hiring-post detection patterns (what to ACCEPT)
# ---------------------------------------------------------------------------
_HIRING_TITLE_RE = re.compile(
    r"(\[Hiring\]|\bHiring\b|\bLooking for\b|\bWe.re hiring\b|\bNow hiring\b|\bJob opening\b|\bJob opportunity\b|\bopen position\b)",
    re.IGNORECASE,
)

# Posts to EXCLUDE (noise / for-hire / seeking work)
_NOISE_TITLE_RE = re.compile(
    r"(\[For Hire\]|\[Seeking Work\]|\bSeeking Work\b|\bLooking for work\b|\bLooking for a job\b|\bAvailable for\b|\bFreelance available\b)",
    re.IGNORECASE,
)

# ---------------------------------------------------------------------------
# Apply URL / contact extraction patterns
# ---------------------------------------------------------------------------
_APPLY_URL_RE = re.compile(
    r"https?://(?:"
    r"(?:[\w\-]+\.lever\.co[\w/?=&#%+\-]*)"
    r"|(?:boards\.greenhouse\.io[\w/?=&#%+\-]*)"
    r"|(?:docs\.google\.com/forms[\w/?=&#%+\-]*)"
    r"|(?:[\w\-]+\.typeform\.com[\w/?=&#%+\-]*)"
    r"|(?:[\w\-]+\.notion\.so[\w/?=&#%+\-]*)"
    r"|(?:[\w\-]+\.workable\.com[\w/?=&#%+\-]*)"
    r")",
    re.IGNORECASE,
)
_EMAIL_RE = re.compile(r"[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}")


def _extract_apply_info(body: str, permalink: str, author: str) -> tuple[str, str]:
    """
    Returns (apply_url, contact_info) extracted from post body.
    Falls back to Reddit post URL and u/{author}.
    """
    apply_url = ""
    contact_info = ""

    url_match = _APPLY_URL_RE.search(body)
    if url_match:
        apply_url = url_match.group(0)

    email_match = _EMAIL_RE.search(body)
    if email_match:
        contact_info = email_match.group(0)

    if not apply_url:
        apply_url = f"https://www.reddit.com{permalink}"
    if not contact_info:
        contact_info = f"u/{author}"

    return apply_url, contact_info


def _make_session() -> requests.Session:
    """
    Build a requests.Session with:
    - Random modern browser user-agent (YARS pattern)
    - Browser accept headers to avoid 403 blocks
    - Retry on 429/5xx with exponential backoff (YARS Retry pattern)
    """
    session = requests.Session()
    session.headers.update({
        "User-Agent": random.choice(_USER_AGENTS),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
        "Sec-Ch-Ua": '"Chromium";v="128", "Not;A=Brand";v="24"',
        "Sec-Ch-Ua-Mobile": "?0",
        "Sec-Ch-Ua-Platform": '"Windows"',
    })

    retry = Retry(
        total=4,
        backoff_factor=2,                        # 2, 4, 8, 16 seconds
        status_forcelist=[429, 500, 502, 503, 504],
        raise_on_status=False,
    )
    adapter = HTTPAdapter(max_retries=retry)
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    return session


def _fetch_new_posts(
    session: requests.Session,
    subreddit: str,
    limit: int = 100,
    timeout: int = 10,
) -> list[dict]:
    """
    Fetch up to `limit` newest posts from r/{subreddit}/new.json.
    Paginates via `after` token (YARS fetch_subreddit_posts pattern).
    Returns raw post data dicts.
    """
    url = f"https://www.reddit.com/r/{subreddit}/new.json"
    batch_size = min(100, limit)
    after: Optional[str] = None
    all_posts: list[dict] = []

    while len(all_posts) < limit:
        params: dict = {"limit": batch_size, "raw_json": 1}
        if after:
            params["after"] = after

        try:
            resp = session.get(url, params=params, timeout=timeout)
            resp.raise_for_status()
        except requests.exceptions.HTTPError as exc:
            if resp.status_code == 429:
                log.warning("Reddit r/%s: HTTP 429 rate-limited. Stopping.", subreddit)
            else:
                log.warning("Reddit r/%s: HTTP error %s", subreddit, exc)
            break
        except requests.exceptions.RequestException as exc:
            log.warning("Reddit r/%s: request failed: %s", subreddit, exc)
            break

        try:
            data = resp.json()
            posts = data["data"]["children"]
        except (ValueError, KeyError) as exc:
            log.warning("Reddit r/%s: unexpected JSON structure: %s", subreddit, exc)
            break

        if not posts:
            break

        for post in posts:
            if post.get("kind") == "t3":
                all_posts.append(post["data"])

        after = data["data"].get("after")
        if not after or len(all_posts) >= limit:
            break

        time.sleep(random.uniform(1.0, 2.0))  # polite delay (YARS pattern)

    return all_posts


class RedditAdapter(BaseAdapter):
    """
    Scrapes /r/{subreddit}/new.json (no API key) for hiring posts.
    Architecture adapted from YARS; Jobee-specific filtering added.
    """

    name = "reddit"

    def fetch(self) -> list[JobPosting]:
        if not self.cfg.enable_reddit or not self.cfg.subreddits:
            log.info("Reddit adapter disabled or no subreddits configured.")
            return []

        session = _make_session()
        all_jobs: list[JobPosting] = []
        cutoff_utc = datetime.now(timezone.utc).timestamp() - 24 * 3600  # Unix seconds

        for sub in self.cfg.subreddits:
            log.info("Reddit: scanning r/%s", sub)
            try:
                posts = _fetch_new_posts(session, sub, limit=100)
                jobs = self._parse_posts(posts, sub, cutoff_utc)
                log.info("Reddit r/%s: %d raw posts → %d hiring jobs", sub, len(posts), len(jobs))
                all_jobs.extend(jobs)
            except Exception as exc:
                log.error("Reddit r/%s: unexpected error: %s", sub, exc, exc_info=True)
            # 2s delay between subreddits (spec requirement)
            time.sleep(2.0)

        return all_jobs

    def _parse_posts(
        self, posts: list[dict], subreddit: str, cutoff_unix: float
    ) -> list[JobPosting]:
        jobs: list[JobPosting] = []

        for post in posts:
            # 1. Age check (Unix created_utc — exact, from YARS data model)
            created_utc: float = post.get("created_utc", 0)
            if created_utc < cutoff_unix:
                continue  # older than 24h

            title: str = post.get("title", "")
            body: str = post.get("selftext", "")
            author: str = post.get("author", "")
            permalink: str = post.get("permalink", "")
            flair: str = post.get("link_flair_text") or ""

            # 2. Hiring post filter
            if _NOISE_TITLE_RE.search(title):
                continue
            if not (_HIRING_TITLE_RE.search(title) or _HIRING_TITLE_RE.search(flair)):
                continue

            # 3. Try to extract a job title and company from post text
            job_title, company = self._extract_title_company(title, body, subreddit)

            # 4. Extract apply URL and contact info
            apply_url, contact_info = _extract_apply_info(body, permalink, author)

            apply_type = (
                ApplyType.reddit_dm
                if contact_info.startswith("u/")
                else ApplyType.direct
            )

            posted_at = datetime.fromtimestamp(created_utc, tz=timezone.utc)

            try:
                job = JobPosting(
                    title=job_title,
                    company=company,
                    location=self._extract_location(title, body),
                    source="reddit",
                    apply_type=apply_type,
                    apply_url=apply_url,
                    contact_info=contact_info,
                    posted_at_utc=posted_at,
                    jd_text=body[:5000] if body else None,
                )
                jobs.append(job)
            except Exception as exc:
                log.debug("Reddit: skipping malformed post '%s': %s", title, exc)

        return jobs

    def _extract_title_company(
        self, title: str, body: str, subreddit: str
    ) -> tuple[str, str]:
        """
        Best-effort extraction of job title + company from Reddit post title.
        Common formats:
          [Hiring] Software Engineer @ Acme Corp - Remote
          [Hiring] Product Manager | Startup | India | $50k
        """
        # Strip common flair prefixes
        clean = re.sub(r"^\[.*?\]\s*", "", title).strip()

        # Try "Role @ Company" or "Role | Company"
        for sep in [" @ ", " at ", " - ", " | "]:
            if sep in clean:
                parts = clean.split(sep, 1)
                return parts[0].strip(), parts[1].split(sep)[0].strip()

        # Fallback: use whole title as role, subreddit as company source
        return clean or "Unknown Role", f"Reddit r/{subreddit}"

    def _extract_location(self, title: str, body: str) -> str:
        """Simple location hint extraction."""
        combined = f"{title} {body}"
        location_patterns = [
            r"\b(Remote|WFH|Work from home)\b",
            r"\b(Hyderabad|Bangalore|Bengaluru|Mumbai|Delhi|Pune|Chennai|India)\b",
            r"\b(USA|US|UK|Canada|Australia|Singapore)\b",
        ]
        for pat in location_patterns:
            m = re.search(pat, combined, re.IGNORECASE)
            if m:
                return m.group(0)
        return ""
