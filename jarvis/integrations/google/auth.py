"""Google OAuth + service builder for Calendar / Gmail / Tasks.

One-time consent via `python -m jarvis --google-auth` (opens a browser), token stored next to the
client secret. Google library imports are lazy so the rest of Jarvis works without them installed.

Setup (one time):
  1. Google Cloud Console → create a project → enable Calendar, Gmail, and Tasks APIs.
  2. Create an OAuth client ID of type "Desktop app"; download the JSON.
  3. Save it to ~/.config/jarvis/client_secret.json (or set GOOGLE_CLIENT_SECRET_FILE).
  4. Run: python -m jarvis --google-auth
"""

from __future__ import annotations

from typing import Optional

SCOPES = [
    "https://www.googleapis.com/auth/calendar",       # read + write calendar
    "https://www.googleapis.com/auth/gmail.modify",   # read + mark read / label
    "https://www.googleapis.com/auth/gmail.send",     # send mail
    "https://www.googleapis.com/auth/tasks",          # read + write tasks
]


def load_credentials(config):
    """Return valid Credentials (refreshing if needed), or None if not connected."""
    token = config.google_token_file
    if not token.exists():
        return None
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials

    creds = Credentials.from_authorized_user_file(str(token), SCOPES)
    if creds and creds.expired and creds.refresh_token:
        try:
            creds.refresh(Request())
            token.write_text(creds.to_json())
        except Exception:  # noqa: BLE001
            return None
    return creds if creds and creds.valid else None


def run_oauth_flow(config):
    """Interactive one-time consent. Requires the downloaded client secret."""
    secret = config.google_client_secret
    if not secret.exists():
        raise SystemExit(
            f"Google client secret not found at {secret}.\n"
            "Create an OAuth 'Desktop app' client in Google Cloud Console, download the JSON, and "
            "save it there (or set GOOGLE_CLIENT_SECRET_FILE). See jarvis/integrations/google/auth.py."
        )
    from google_auth_oauthlib.flow import InstalledAppFlow

    secret.parent.mkdir(parents=True, exist_ok=True)
    flow = InstalledAppFlow.from_client_secrets_file(str(secret), SCOPES)
    creds = flow.run_local_server(port=0)
    config.google_token_file.write_text(creds.to_json())
    return creds


def service(config, name: str, version: str) -> Optional[object]:
    """Build a Google API service client, or None if not connected."""
    creds = load_credentials(config)
    if not creds:
        return None
    from googleapiclient.discovery import build

    return build(name, version, credentials=creds, cache_discovery=False)
