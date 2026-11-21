"""
adapters/reddit_search.py

Dedicated unauthenticated Reddit job search engine.
Powers the 'Reddit Jobs' tab.
Allows searching hiring posts by keywords, role, and location (defaults to Hyderabad).
No API keys required.
"""

from __future__ import annotations

import logging
import random
import re
import urllib.parse
from datetime import datetime, timezone
from typing import Optional

import requests
from bs4 import BeautifulSoup

from core import db as jobee_db
from core.models import ApplyType, JobPosting

log = logging.getLogger(__name__)

_USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4 Safari/605.1.15",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:129.0) Gecko/20100101 Firefox/129.0",
]

_DEFAULT_SUBREDDITS = ["forhire", "hiring", "IndianStartups", "startups", "freelance_forhire"]


def _get_headers() -> dict:
    return {
        "User-Agent": random.choice(_USER_AGENTS),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
    }


def search_reddit_jobs(
    query: str,
    location: str = "Hyderabad",
    subreddits: Optional[list[str]] = None,
    limit: int = 30,
) -> list[dict]:
    """
    Search Reddit for job postings matching query + location.
    Uses unauthenticated public search endpoints with DuckDuckGo fallback.
    Returns list of dicts:
      [{id, title, subreddit, author, age_str, preview, url, contact_info}, ...]
    """
    subs = subreddits or _DEFAULT_SUBREDDITS
    combined_query_parts = []

    if query.strip():
        combined_query_parts.append(query.strip())
    if location.strip():
        combined_query_parts.append(location.strip())

    if not combined_query_parts:
        combined_query_parts.append("hiring")

    search_term = " ".join(combined_query_parts)
    log.info("Searching Reddit for: %r across %s", search_term, subs)

    results: list[dict] = []

    # 1. Primary Strategy: Query Reddit's public .json search per subreddit
    for sub in subs:
        url = f"https://www.reddit.com/r/{sub}/search.json"
        params = {
            "q": search_term,
            "restrict_sr": "on",
            "sort": "new",
            "limit": 15,
            "raw_json": 1,
        }
        try:
            resp = requests.get(url, params=params, headers=_get_headers(), timeout=8)
            if resp.status_code == 200:
                data = resp.json()
                children = data.get("data", {}).get("children", [])
                for item in children:
                    post = item.get("data", {})
                    parsed = _parse_reddit_child(post, sub)
                    if parsed:
                        results.append(parsed)
            else:
                log.debug("Reddit sub %s returned status %d", sub, resp.status_code)
        except Exception as exc:
            log.debug("Error querying Reddit sub %s: %s", sub, exc)

    # 2. Secondary Strategy: If direct JSON was rate-limited / blocked, use DuckDuckGo HTML fallback
    if len(results) < 5:
        log.info("Primary search returned %d results. Running fallback search...", len(results))
        fallback_results = _duckduckgo_reddit_search(search_term, subs)
        results.extend(fallback_results)

    # Deduplicate results by URL
    seen_urls = set()
    deduped = []
    for r in results:
        if r["url"] not in seen_urls:
            seen_urls.add(r["url"])
            deduped.append(r)

    log.info("Reddit search complete: %d unique posts found for %r", len(deduped), search_term)
    return deduped[:limit]


def _parse_reddit_child(post: dict, default_sub: str) -> Optional[dict]:
    """Parse raw Reddit post JSON child."""
    title = post.get("title", "")
    # Exclude noise like [For Hire]
    if re.search(r"(\[For Hire\]|\[Seeking Work\])", title, re.I):
        return None

    body = post.get("selftext", "")
    author = post.get("author", "unknown")
    permalink = post.get("permalink", "")
    full_url = f"https://www.reddit.com{permalink}" if permalink.startswith("/") else post.get("url", "")
    sub = post.get("subreddit", default_sub)

    created_utc = post.get("created_utc", 0)
    age_str = "recent"
    if created_utc:
        diff_hours = (datetime.now(timezone.utc).timestamp() - created_utc) / 3600
        if diff_hours < 24:
            age_str = f"{round(diff_hours, 1)}h ago"
        else:
            age_str = f"{int(diff_hours // 24)}d ago"

    # Extract email or forms if present
    emails = re.findall(r"[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}", body)
    contact = emails[0] if emails else f"u/{author}"

    return {
        "id": post.get("id", str(random.randint(100000, 999999))),
        "title": title,
        "subreddit": f"r/{sub}",
        "author": author,
        "age_str": age_str,
        "preview": (body[:280] + "...") if len(body) > 280 else (body or "No post body text provided."),
        "url": full_url,
        "contact_info": contact,
    }


def _duckduckgo_reddit_search(query: str, subreddits: list[str]) -> list[dict]:
    """DuckDuckGo HTML fallback to find Reddit hiring posts without credentials."""
    sub_clause = " OR ".join([f"site:reddit.com/r/{s}" for s in subreddits])
    search_q = f'({sub_clause}) (hiring OR [Hiring]) {query}'
    url = f"https://html.duckduckgo.com/html/?q={urllib.parse.quote(search_q)}"

    results = []
    try:
        resp = requests.get(url, headers=_get_headers(), timeout=10)
        if resp.status_code == 200:
            soup = BeautifulSoup(resp.text, "html.parser")
            for result in soup.find_all("div", class_="result"):
                link_el = result.find("a", class_="result__url")
                title_el = result.find("a", class_="result__snippet") or result.find("h2")
                snippet_el = result.find("a", class_="result__snippet")

                if link_el and title_el:
                    raw_href = link_el.get("href", "")
                    title_text = title_el.get_text().strip()
                    snippet_text = snippet_el.get_text().strip() if snippet_el else ""

                    # Clean Reddit URL from DDG redirect
                    match = re.search(r"https?://(?:www\.)?reddit\.com/r/[\w]+/comments/[\w]+/[\w_/-]+", raw_href)
                    if match:
                        reddit_url = match.group(0)
                        sub_match = re.search(r"/r/([\w]+)/", reddit_url)
                        sub_name = sub_match.group(1) if sub_match else "reddit"

                        results.append({
                            "id": str(abs(hash(reddit_url))),
                            "title": title_text,
                            "subreddit": f"r/{sub_name}",
                            "author": "Reddit User",
                            "age_str": "recent",
                            "preview": snippet_text or "Click to read full Reddit discussion.",
                            "url": reddit_url,
                            "contact_info": f"Reddit r/{sub_name}",
                        })
    except Exception as exc:
        log.debug("DuckDuckGo fallback search error: %s", exc)

    return results


def import_reddit_post_to_db(post_data: dict, default_location: str = "Hyderabad") -> tuple[bool, str]:
    """
    Import a Reddit search result directly into SQLite jobs.db as 'To Apply'.
    Returns (success, message).
    """
    title = post_data.get("title", "Reddit Job Posting")
    url = post_data.get("url", "")
    sub = post_data.get("subreddit", "r/reddit")
    contact = post_data.get("contact_info", "")
    preview = post_data.get("preview", "")

    # Derive company from subreddit / author
    company = f"Reddit ({sub})"

    job = JobPosting(
        title=title,
        company=company,
        location=default_location,
        source="reddit",
        apply_type=ApplyType.direct if "@" in contact else ApplyType.reddit_dm,
        apply_url=url,
        contact_info=contact,
        posted_at_utc=datetime.now(timezone.utc),
        jd_text=preview,
    )

    is_new, is_repost = jobee_db.upsert_job(job)
    if is_new:
        return True, "Imported to Jobs Feed successfully!"
    else:
        return True, "Job already existed in tracker (updated seen count)."
