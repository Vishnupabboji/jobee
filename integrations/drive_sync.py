"""
integrations/drive_sync.py

Uploads resumes/base_resume.pdf to a Google Drive folder and returns the Drive URL.
If Google auth is not configured → returns None (Local-Only mode).
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional

log = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parent.parent
BASE_RESUME_PATH = ROOT / "resumes" / "base_resume.pdf"
_CACHED_URL: Optional[str] = None  # module-level cache for the session


def upload_base_resume(drive_folder_id: Optional[str] = None) -> Optional[str]:
    """
    Upload (or update) base_resume.pdf to Google Drive.
    Returns the shareable Drive URL, or None if not configured / auth missing.
    """
    global _CACHED_URL

    if not BASE_RESUME_PATH.exists():
        log.info("No base_resume.pdf found at %s — skipping Drive upload.", BASE_RESUME_PATH)
        return None

    from integrations.google_auth import get_credentials
    creds = get_credentials()
    if creds is None:
        log.info("Drive sync skipped — not authenticated (Local-Only mode).")
        return None

    try:
        from googleapiclient.discovery import build
        from googleapiclient.http import MediaFileUpload

        service = build("drive", "v3", credentials=creds)

        file_metadata: dict = {"name": "base_resume.pdf"}
        if drive_folder_id:
            file_metadata["parents"] = [drive_folder_id]

        # Check if file already exists in folder
        query = "name='base_resume.pdf' and trashed=false"
        if drive_folder_id:
            query += f" and '{drive_folder_id}' in parents"

        existing = service.files().list(
            q=query, fields="files(id,webViewLink)", spaces="drive"
        ).execute()
        files = existing.get("files", [])

        media = MediaFileUpload(str(BASE_RESUME_PATH), mimetype="application/pdf")

        if files:
            # Update existing file
            file_id = files[0]["id"]
            service.files().update(fileId=file_id, media_body=media).execute()
            log.info("Drive: updated existing base_resume.pdf (id=%s)", file_id)
        else:
            # Create new file
            created = service.files().create(
                body=file_metadata, media_body=media, fields="id"
            ).execute()
            file_id = created["id"]
            # Make it anyone-with-link readable
            service.permissions().create(
                fileId=file_id,
                body={"type": "anyone", "role": "reader"},
            ).execute()
            log.info("Drive: uploaded new base_resume.pdf (id=%s)", file_id)

        # Get shareable link
        file_info = service.files().get(
            fileId=file_id, fields="webViewLink"
        ).execute()
        url = file_info.get("webViewLink", "")
        _CACHED_URL = url
        return url

    except Exception as exc:
        log.error("Drive upload failed: %s", exc)
        return None


def get_cached_resume_url() -> Optional[str]:
    """Return the last known Drive URL without re-uploading."""
    return _CACHED_URL
