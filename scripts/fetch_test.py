"""
Manual acceptance test for Phase 1.

Authenticates with Gmail, fetches the 10 most recent inbox messages,
and prints their parsed fields to the console.

Run with:
    python scripts/fetch_test.py
"""

import sys
from pathlib import Path

# Allow running this script directly (python scripts/fetch_test.py)
# without needing to install the package or set PYTHONPATH manually.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from googleapiclient.errors import HttpError

from src.auth import get_gmail_service
from src.gmail import get_message, list_recent_messages

SEPARATOR = "-" * 70


def main() -> None:
    print("Authenticating with Gmail...")
    try:
        service = get_gmail_service()
    except FileNotFoundError as exc:
        print(f"\nSetup error: {exc}")
        sys.exit(1)

    print("Authenticated. Fetching 10 most recent inbox messages...\n")

    try:
        message_ids = list_recent_messages(service, max_results=10)
    except HttpError as exc:
        print(f"Failed to list messages: {exc}")
        sys.exit(1)

    if not message_ids:
        print("No messages found (empty inbox, or the list call failed above).")
        return

    for i, message_id in enumerate(message_ids, start=1):
        email = get_message(service, message_id)
        if email is None:
            print(f"[{i}] Skipped (failed to fetch/parse message {message_id})")
            continue

        print(SEPARATOR)
        print(f"[{i}] Subject:     {email.subject}")
        print(f"    From:        {email.sender}")
        print(f"    Received:    {email.received_at}")
        print(f"    Message ID:  {email.id}")
        print(f"    Thread ID:   {email.thread_id}")
        print("    Body:")
        for line in email.body_text.splitlines()[:15]:
            print(f"      {line}")
        if len(email.body_text.splitlines()) > 15:
            print("      [...body truncated for console display...]")

    print(SEPARATOR)
    print(f"\nDone. Parsed {len(message_ids)} message(s).")


if __name__ == "__main__":
    main()
