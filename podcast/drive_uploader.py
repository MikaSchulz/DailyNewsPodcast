"""Uploads a file to a Google Drive folder via a service account.

Uses a service account (not personal OAuth) so this runs fully headless —
no browser, no interactive consent, no refresh-token expiry. The target
Drive folder must be shared with the service account's email (Editor
access); the file then counts against the sharing account's storage quota
and still syncs to the Drive app on the phone, just with the service
account listed as owner instead of you.
"""

import logging
import os

from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload

logger = logging.getLogger(__name__)

SCOPES = ["https://www.googleapis.com/auth/drive.file"]


def _get_credentials() -> service_account.Credentials:
    key_path = os.environ.get("GOOGLE_APPLICATION_CREDENTIALS", "credentials/service-account.json")
    if not os.path.exists(key_path):
        raise RuntimeError(
            f"Service account key not found at '{key_path}'. "
            "Set GOOGLE_APPLICATION_CREDENTIALS or place the key there (see README.md)."
        )
    return service_account.Credentials.from_service_account_file(key_path, scopes=SCOPES)


def upload_file(file_path: str, config: dict) -> str:
    folder_id = config["drive"]["folder_id"]
    if not folder_id or folder_id == "YOUR_GOOGLE_DRIVE_FOLDER_ID":
        raise RuntimeError("drive.folder_id is not set in config.yaml.")

    try:
        creds = _get_credentials()
        service = build("drive", "v3", credentials=creds)
    except Exception as exc:
        raise RuntimeError(f"Could not authenticate with Google Drive: {exc}") from exc

    file_metadata = {
        "name": os.path.basename(file_path),
        "parents": [folder_id],
    }
    media = MediaFileUpload(file_path, mimetype="audio/mpeg", resumable=True)

    try:
        uploaded = service.files().create(body=file_metadata, media_body=media, fields="id, webViewLink").execute()
    except Exception as exc:
        raise RuntimeError(
            f"Upload to Google Drive failed: {exc}. "
            "Check that the target folder is shared with the service account's email as Editor."
        ) from exc

    logger.info("Uploaded %s to Drive (file id %s).", file_path, uploaded.get("id"))
    return uploaded.get("id")
