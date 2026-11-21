"""
integrations/google_auth.py

Local OAuth2 flow for Google APIs.
Uses InstalledAppFlow (credentials.json → token.json).
If credentials.json is missing → returns None silently (Local-Only Mode).
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional

log = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parent.parent
CREDENTIALS_PATH = ROOT / "credentials.json"
TOKEN_PATH = ROOT / "token.json"

SCOPES = [
    "https://www.googleapis.com/auth/drive.file",
    "https://www.googleapis.com/auth/spreadsheets",
]


def get_credentials():
    """
    Return valid Google OAuth2 credentials, or None if unavailable.
    Handles token refresh automatically. Never crashes.
    """
    if not CREDENTIALS_PATH.exists():
        log.info("credentials.json not found — running in Local-Only mode.")
        return None

    try:
        from google.oauth2.credentials import Credentials
        from google.auth.transport.requests import Request
        from google_auth_oauthlib.flow import InstalledAppFlow

        creds = None
        if TOKEN_PATH.exists():
            creds = Credentials.from_authorized_user_file(str(TOKEN_PATH), SCOPES)

        if not creds or not creds.valid:
            if creds and creds.expired and creds.refresh_token:
                creds.refresh(Request())
            else:
                flow = InstalledAppFlow.from_client_secrets_file(
                    str(CREDENTIALS_PATH), SCOPES
                )
                creds = flow.run_local_server(port=0)
            with TOKEN_PATH.open("w") as f:
                f.write(creds.to_json())

        return creds
    except Exception as exc:
        log.error("Google auth failed: %s — continuing in Local-Only mode.", exc)
        return None


def is_authenticated() -> bool:
    """Quick check: do we have valid stored credentials?"""
    if not CREDENTIALS_PATH.exists():
        return False
    try:
        from google.oauth2.credentials import Credentials
        if not TOKEN_PATH.exists():
            return False
        creds = Credentials.from_authorized_user_file(str(TOKEN_PATH), SCOPES)
        return bool(creds and creds.valid)
    except Exception:
        return False
