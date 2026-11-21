"""
adapters/base.py — Abstract BaseAdapter for Jobee.
Every job source must inherit from this and implement `fetch()`.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from core.models import AppConfig, JobPosting


class BaseAdapter(ABC):
    """
    Every adapter returns a flat list of JobPosting objects.
    One adapter failure MUST NOT crash other adapters.
    All rate-limit/network errors are caught internally and logged to ScrapeRun.
    """

    name: str = "base"

    def __init__(self, cfg: "AppConfig"):
        self.cfg = cfg

    @abstractmethod
    def fetch(self) -> list["JobPosting"]:
        """
        Fetch raw jobs from the source.
        Returns a list of JobPosting objects (pre-filter, may include old jobs).
        Never raises — catches all exceptions internally.
        """
        ...
