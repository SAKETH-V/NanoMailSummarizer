"""
Manual acceptance test for Phase 2.

Pulls the 5 most recent inbox emails (via Phase 1's fetch code), runs
each through summarize_email(), and prints subject next to the model's
category/priority/summary/reasoning so you can eyeball quality.

Run with:
    python scripts/summarize_test.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from googleapiclient.errors import HttpError

from src.ai import summarize_email
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

    print("Authenticated. Fetching 5 most recent inbox messages...\n")

    try:
        message_ids = list_recent_messages(service, max_results=5)
    except HttpError as exc:
        print(f"Failed to list messages: {exc}")
        sys.exit(1)

    if not message_ids:
        print("No messages found (empty inbox, or the list call failed above).")
        return

    total_input_tokens = 0
    total_output_tokens = 0
    summarized_count = 0
    skipped_count = 0

    for i, message_id in enumerate(message_ids, start=1):
        email = get_message(service, message_id)
        if email is None:
            print(f"[{i}] Skipped (failed to fetch/parse message {message_id})")
            skipped_count += 1
            continue

        print(SEPARATOR)
        print(f"[{i}] Subject: {email.subject}")
        print(f"    From:    {email.sender}")

        try:
            summary = summarize_email(email)
        except RuntimeError as exc:
            # Missing ANTHROPIC_API_KEY -- no point continuing the loop.
            print(f"\nSetup error: {exc}")
            sys.exit(1)

        if summary is None:
            print("    -> Skipped: could not get a valid structured summary "
                  "(see [summarize_email] log above for details).")
            skipped_count += 1
            continue

        summarized_count += 1
        print(f"    Category:  {summary.category}")
        print(f"    Priority:  {summary.priority}/5")
        print(f"    Summary:   {summary.summary}")
        print(f"    Reasoning: {summary.reasoning}")

    print(SEPARATOR)
    print(
        f"\nDone. Summarized {summarized_count}/{len(message_ids)} message(s) "
        f"({skipped_count} skipped)."
    )
    print(
        "\nToken usage per call is logged above as it happens "
        "(see [usage] lines) -- use those for a real cost-per-email estimate."
    )


if __name__ == "__main__":
    main()
