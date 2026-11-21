"""
V2 stub — ResumeEngineInterface.

This module is intentionally minimal. It defines the interface that
V2's ATS tailoring engine (RenderCV + Resume-Matcher + Ollama) will implement.

DO NOT activate or implement this in V1. The UI shows "Coming in V2" for this feature.

Dependencies to install in V2 (NOT NOW):
  pip install rendercv resume-matcher  # + local Ollama for rewrite mode

Reference repos:
  RenderCV:       https://github.com/rendercv/rendercv
  Resume-Matcher: https://github.com/srbhr/resume-matcher
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path
from typing import Optional


class ResumeEngineInterface(ABC):
    """
    Abstract interface for resume tailoring engines (V2+).

    V1 uses only SingleBaseResumeEngine (no tailoring — just returns the base PDF path).
    V2 will implement ATSTailoredResumeEngine using RenderCV + Resume-Matcher.
    """

    @abstractmethod
    def get_resume_for_job(
        self,
        job_hash: str,
        job_title: str,
        company: str,
        jd_text: Optional[str] = None,
    ) -> Path:
        """
        Return the local path to the resume PDF to use for this job.

        V1: always returns resumes/base_resume.pdf
        V2: generates a tailored PDF using keyword-swap or LLM rewrite
        """
        ...

    @abstractmethod
    def mode_name(self) -> str:
        """Human-readable mode label shown in the UI."""
        ...


class SingleBaseResumeEngine(ResumeEngineInterface):
    """
    V1 active engine: returns the same base resume PDF for every job.
    No tailoring. No dependencies beyond Python stdlib.
    """

    def __init__(self, base_resume_path: Path):
        self._path = base_resume_path

    def get_resume_for_job(
        self,
        job_hash: str,
        job_title: str,
        company: str,
        jd_text: Optional[str] = None,
    ) -> Path:
        if not self._path.exists():
            raise FileNotFoundError(
                f"Base resume not found at {self._path}. "
                "Please drop your base_resume.pdf into the resumes/ folder."
            )
        return self._path

    def mode_name(self) -> str:
        return "Single Base Resume"


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------

def get_resume_engine(mode: str, base_resume_path: Path) -> ResumeEngineInterface:
    """
    Return the correct engine for the configured resume_mode.

    Args:
        mode: "single_base" or "ats_tailored" (V2)
        base_resume_path: path to resumes/base_resume.pdf
    """
    if mode == "single_base":
        return SingleBaseResumeEngine(base_resume_path)
    elif mode == "ats_tailored":
        raise NotImplementedError(
            "ATS tailored resume mode is coming in V2. "
            "Install rendercv and resume-matcher when ready."
        )
    else:
        raise ValueError(f"Unknown resume mode: {mode!r}")
