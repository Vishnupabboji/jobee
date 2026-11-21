"""
Pydantic v2 schemas for Jobee.
All models used throughout core, adapters, integrations, and UI.
"""

from __future__ import annotations

import hashlib
import re
from datetime import datetime, timezone
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field, model_validator


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------

class JobStatus(str, Enum):
    to_apply  = "To Apply"
    applied   = "Applied"
    interview = "Interview"
    rejected  = "Rejected"
    skipped   = "Skipped"


class ApplyType(str, Enum):
    easy_apply   = "Easy Apply"
    external_ats = "External ATS"
    direct       = "Direct Email/Form"
    reddit_dm    = "Reddit DM"


class ResumeMode(str, Enum):
    single_base  = "single_base"
    ats_tailored = "ats_tailored"   # V2 — not active yet


class ScrapeStatus(str, Enum):
    success = "success"
    partial = "partial"
    failed  = "failed"


# ---------------------------------------------------------------------------
# JobPosting
# ---------------------------------------------------------------------------

def _normalize_company(name: str) -> str:
    """Strip legal suffixes, punctuation, extra whitespace and lowercase."""
    name = name.lower()
    # Remove common legal suffixes
    name = re.sub(
        r"\b(inc|ltd|llc|pvt|private|limited|corp|corporation|co|gmbh|ag|plc|llp)\b\.?",
        "", name
    )
    # Remove punctuation (keep alphanumeric and spaces)
    name = re.sub(r"[^a-z0-9\s]", "", name)
    # Collapse whitespace
    return re.sub(r"\s+", " ", name).strip()


def _normalize_title(title: str) -> str:
    """Lowercase and strip punctuation/whitespace."""
    title = title.lower()
    title = re.sub(r"[^a-z0-9\s]", "", title)
    return re.sub(r"\s+", " ", title).strip()


def _make_job_hash(normalized_company: str, normalized_title: str) -> str:
    """SHA-256 of 'normalized_company||normalized_title'."""
    raw = f"{normalized_company}||{normalized_title}"
    return hashlib.sha256(raw.encode()).hexdigest()


class JobPosting(BaseModel):
    # --- Hashes / normalized fields ---
    job_hash:           str = Field(default="", description="SHA-256(norm_company + norm_title)")
    normalized_company: str = Field(default="")
    normalized_title:   str = Field(default="")

    # --- Raw fields (from source) ---
    title:    str
    company:  str
    location: str = ""
    source:   str = ""   # linkedin | indeed | naukri | glassdoor | reddit | greenhouse | lever

    # --- Apply info ---
    apply_type:   ApplyType       = ApplyType.external_ats
    apply_url:    Optional[str]   = None
    contact_info: Optional[str]   = None

    # --- Timing ---
    posted_at_utc: datetime   # MUST be timezone-aware UTC

    # --- Content ---
    jd_text: Optional[str] = None

    # --- Status / tracking ---
    status:          JobStatus  = JobStatus.to_apply
    resume_mode_used: str       = ResumeMode.single_base.value
    resume_drive_url: Optional[str] = None

    # --- Dedup / repost ---
    is_repost:  bool = False
    seen_count: int  = 1

    # --- Sync ---
    synced_to_sheet: bool = False

    @model_validator(mode="after")
    def _compute_normalized_fields(self) -> "JobPosting":
        if not self.normalized_company:
            self.normalized_company = _normalize_company(self.company)
        if not self.normalized_title:
            self.normalized_title = _normalize_title(self.title)
        if not self.job_hash:
            self.job_hash = _make_job_hash(self.normalized_company, self.normalized_title)
        return self


# ---------------------------------------------------------------------------
# ScrapeRun (logged to scrape_runs table)
# ---------------------------------------------------------------------------

class ScrapeRun(BaseModel):
    source:            str
    started_at:        datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    finished_at:       Optional[datetime] = None
    status:            ScrapeStatus = ScrapeStatus.success
    raw_count:         int = 0
    dropped_old_count: int = 0
    filtered_out_count: int = 0
    duplicate_count:   int = 0
    new_count:         int = 0
    error_message:     Optional[str] = None


# ---------------------------------------------------------------------------
# AppConfig (mirrors config.yaml)
# ---------------------------------------------------------------------------

class AppConfig(BaseModel):
    # Job search targets
    roles:     list[str] = ["Product Manager", "Associate Product Manager", "AI Engineer"]
    locations: list[str] = ["India", "Hyderabad", "Remote"]

    # Filter rules
    max_years_experience:    int        = 5
    exclude_title_keywords:  list[str]  = ["Senior", "Staff", "Principal", "Lead", "Head", "Director", "VP", "Manager of Managers"]

    # Sources
    enable_jobspy:   bool = True
    jobspy_sites:    list[str] = ["linkedin", "indeed", "glassdoor", "naukri"]
    enable_reddit:   bool = True
    subreddits:      list[str] = ["forhire", "hiring", "IndianStartups", "startups"]
    enable_ats:      bool = True
    ats_slugs:       dict[str, list[str]] = {
        "greenhouse": [],
        "lever":      []
    }

    # Scheduler
    schedule_hours: int = 0   # 0 = off; 4, 6, or 12

    # Resume
    resume_mode: str = ResumeMode.single_base.value

    # Google
    google_sheet_id:    Optional[str] = None
    google_drive_folder_id: Optional[str] = None

    # Fuzzy dedup threshold (percentage, 0-100)
    fuzzy_threshold: int = 88
