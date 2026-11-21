"""
core/jd_scraper.py

Live on-demand web scraper for full job descriptions.
Takes an apply URL, scrapes the target webpage live over the internet,
cleans HTML, strips boilerplate/navigation, and returns formatted text.
"""

from __future__ import annotations

import logging
import re
from typing import Optional
from urllib.parse import urlparse

import requests
from bs4 import BeautifulSoup

log = logging.getLogger(__name__)

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/128.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}
_TIMEOUT = 12


def _clean_soup_text(element) -> str:
    """Format HTML element into readable clean text with linebreaks and bullet points."""
    # Convert lists to bullets
    for li in element.find_all("li"):
        li.insert_before("\n• ")
    for p in element.find_all(["p", "div", "h1", "h2", "h3", "h4", "h5", "h6", "br"]):
        p.insert_after("\n\n")

    text = element.get_text()
    # Normalize multiple newlines and spaces
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n\s*\n\s*\n+", "\n\n", text)
    return text.strip()


def fetch_live_jd(url: str) -> str:
    """
    Fetch and clean job description from a live URL over the internet.
    Returns cleaned text string. Never raises.
    """
    if not url or not url.startswith("http"):
        return "Invalid or missing application link."

    try:
        resp = requests.get(url, headers=_HEADERS, timeout=_TIMEOUT, allow_redirects=True)
        if resp.status_code == 404:
            return "Job posting page was not found (HTTP 404). The role may have been closed."
        if resp.status_code in (401, 403):
            return f"Access to job page was blocked by the host (HTTP {resp.status_code})."
        resp.raise_for_status()
    except requests.exceptions.Timeout:
        return "Connection timed out while fetching the job description."
    except requests.exceptions.RequestException as exc:
        return f"Failed to fetch job page: {exc}"

    try:
        soup = BeautifulSoup(resp.text, "html.parser")

        # Remove irrelevant elements
        for tag in soup(["script", "style", "noscript", "header", "footer", "nav", "svg", "button", "iframe"]):
            tag.decompose()

        parsed = urlparse(url)
        domain = parsed.netloc.lower()

        target_el = None

        # 1. Greenhouse
        if "greenhouse.io" in domain or soup.find("div", id="content"):
            target_el = soup.find("div", id="content") or soup.find("div", class_="content")

        # 2. Lever
        elif "lever.co" in domain or soup.find("div", attrs={"data-qa": "job-description"}):
            target_el = soup.find("div", attrs={"data-qa": "job-description"}) or soup.find("div", class_="posting-headline")

        # 3. LinkedIn
        elif "linkedin.com" in domain:
            target_el = (
                soup.find("div", class_="show-more-less-html__markup")
                or soup.find("div", class_="description__text")
            )

        # 4. Indeed
        elif "indeed.com" in domain:
            target_el = soup.find("div", id="jobDescriptionText") or soup.find("div", class_="jobsearch-JobComponent-description")

        # 5. Generic heuristics: article, main, or largest text container
        if not target_el:
            target_el = soup.find("article") or soup.find("main") or soup.find("div", class_=re.compile(r"description|job-details|content", re.I))

        if not target_el:
            target_el = soup.body

        if not target_el:
            return "Unable to parse job description content from this page."

        cleaned = _clean_soup_text(target_el)
        if len(cleaned) < 50:
            return f"Extracted text too brief. Visit the link directly to apply:\n{url}"

        return cleaned

    except Exception as exc:
        log.error("Error parsing JD for %s: %s", url, exc)
        return f"Error parsing job description: {exc}"
