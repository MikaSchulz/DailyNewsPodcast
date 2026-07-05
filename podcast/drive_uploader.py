"""Uploads a file to a Google Drive folder via OAuth (your personal Google account).

Google blocks service accounts from owning files in a personal
(non-Workspace) Drive — even in a folder shared with them as Editor — with
a storageQuotaExceeded error. The two official workarounds (Shared Drives,
domain-wide delegation) both require Google Workspace, which a regular
@gmail.com account doesn't have. So Drive upload uses OAuth with your own
account instead; Text-to-Speech still uses the service account
(podcast/tts.py), since that's a stateless API with no storage/quota
concept and works fine that way.

First run needs a one-time interactive browser login (see README.md) to
produce a refresh token, cached at GOOGLE_OAUTH_TOKEN. After that —
including headless cloud Routine runs, via ensure_oauth_token_file() in
main.py — it refreshes silently, no browser involved. Set the OAuth
consent screen's publishing status to "In production" (README.md) so that
refresh token doesn't expire after 7 days.
"""

import logging
import os

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload

logger = logging.getLogger(__name__)

SCOPES = ["https://www.googleapis.com/auth/drive.file"]


def _get_credentials() -> Credentials:
    client_secret_path = os.environ.get("GOOGLE_OAUTH_CLIENT_SECRET", "credentials/oauth_client_secret.json")
    token_path = os.environ.get("GOOGLE_OAUTH_TOKEN", "credentials/token.json")

    creds = None
    if os.path.exists(token_path):
        creds = Credentials.from_authorized_user_file(token_path, SCOPES)

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            if not os.path.exists(client_secret_path):
                raise RuntimeError(
                    f"No valid Drive token and no OAuth client secret at '{client_secret_path}'. "
                    "Run this locally once to complete the one-time browser login (see README.md) — "
                    "a headless run can't do the initial consent."
                )
            flow = InstalledAppFlow.from_client_secrets_file(client_secret_path, SCOPES)
            creds = flow.run_local_server(port=0)

        os.makedirs(os.path.dirname(token_path) or ".", exist_ok=True)
        with open(token_path, "w") as f:
            f.write(creds.to_json())

    return creds


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
        raise RuntimeError(f"Upload to Google Drive failed: {exc}") from exc

    logger.info("Uploaded %s to Drive (file id %s).", file_path, uploaded.get("id"))
    return uploaded.get("id")
