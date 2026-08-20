"""
Handles Gmail OAuth: first-run browser consent, token caching, and
silent refresh on subsequent runs.

Usage:
    from src.auth import get_gmail_service
    service = get_gmail_service()
    service.users().messages().list(userId="me").execute()
"""

from google.auth.exceptions import RefreshError
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build

from src.config import GMAIL_SCOPES, credentials_path, token_path


def get_gmail_service():
    """
    Returns an authenticated Gmail API client (the `service` object from
    googleapiclient.discovery.build).

    Flow:
      1. If token.json exists, load it.
      2. If the loaded credentials are valid, use them as-is.
      3. If they're expired but have a refresh token, refresh silently.
      4. If refresh fails (revoked/expired refresh token), or no token.json
         exists yet, run the interactive InstalledAppFlow (opens a browser),
         then save the new token to token.json.
    """
    creds = _load_cached_credentials()

    if creds and creds.valid:
        return _build_service(creds)

    if creds and creds.expired and creds.refresh_token:
        try:
            creds.refresh(Request())
            _save_credentials(creds)
            return _build_service(creds)
        except RefreshError:
            # Refresh token is no longer valid (revoked, expired, or the
            # user changed their Google password). Don't crash — just
            # drop the stale token and fall through to a fresh login.
            print(
                "Saved Gmail credentials could not be refreshed "
                "(likely revoked or expired). Removing token.json and "
                "starting a new login."
            )
            _delete_token_file()

    creds = _run_interactive_login()
    _save_credentials(creds)
    return _build_service(creds)


def _load_cached_credentials():
    path = token_path()
    if not path.exists():
        return None
    try:
        return Credentials.from_authorized_user_file(str(path), GMAIL_SCOPES)
    except (ValueError, OSError) as exc:
        # token.json exists but is corrupt/unreadable/wrong shape.
        print(f"Ignoring unreadable token.json ({exc}); will re-authenticate.")
        return None


def _run_interactive_login():
    creds_path = credentials_path()
    if not creds_path.exists():
        raise FileNotFoundError(
            f"Could not find OAuth client secrets at '{creds_path}'.\n"
            "Download it from Google Cloud Console (APIs & Services > "
            "Credentials > your Desktop OAuth client > Download JSON), "
            "save it there, and try again. See README.md for the full "
            "setup steps."
        )
    flow = InstalledAppFlow.from_client_secrets_file(str(creds_path), GMAIL_SCOPES)
    # Opens a browser window for the user to log in and consent, then
    # spins up a local server to catch the OAuth redirect.
    return flow.run_local_server(port=0)


def _save_credentials(creds: Credentials) -> None:
    path = token_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(creds.to_json())


def _delete_token_file() -> None:
    path = token_path()
    if path.exists():
        path.unlink()


def _build_service(creds: Credentials):
    return build("gmail", "v1", credentials=creds)
