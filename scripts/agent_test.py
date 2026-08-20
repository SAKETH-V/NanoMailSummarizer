"""
Manual acceptance test for Phase 3.

Runs the agent over the most recent inbox emails. For each one, prints
category/priority/summary as before, plus which tools (if any) the
agent chose to call, and any proposed action.

The thing to eyeball: get_thread_context should fire on reply emails
but not cold/first-contact ones, and get_sender_history should only
fire when it's genuinely ambiguous -- not on every email. If every
email shows the same tool-call pattern, that's a sign the agent is
running a fixed pipeline rather than actually reasoning about each one.

Run with:
    python scripts/agent_test.py [N]

N defaults to 10.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from googleapiclient.errors import HttpError

from src.agent import run_agent
from src.auth import get_gmail_service
from src.gmail import get_message, list_recent_messages

SEPARATOR = "-" * 70


def main() -> None:
    max_results = int(sys.argv[1]) if len(sys.argv) > 1 else 10

    print("Authenticating with Gmail...")
    try:
        service = get_gmail_service()
    except FileNotFoundError as exc:
        print(f"\nSetup error: {exc}")
        sys.exit(1)

    print(f"Authenticated. Fetching {max_results} most recent inbox messages...\n")

    try:
        message_ids = list_recent_messages(service, max_results=max_results)
    except HttpError as exc:
        print(f"Failed to list messages: {exc}")
        sys.exit(1)

    if not message_ids:
        print("No messages found (empty inbox, or the list call failed above).")
        return

    # Fetch every email up front -- this batch doubles as get_sender_history's
    # in-memory lookup pool, same as it would in the real pipeline.
    emails = []
    for message_id in message_ids:
        email = get_message(service, message_id)
        if email is not None:
            emails.append(email)

    tool_call_counts = {"get_thread_context": 0, "get_sender_history": 0, "propose_gmail_action": 0}
    summarized_count = 0
    skipped_count = 0

    for i, email in enumerate(emails, start=1):
        print(SEPARATOR)
        print(f"[{i}] Subject: {email.subject}")
        print(f"    From:    {email.sender}")

        try:
            result = run_agent(email, service, known_emails=emails)
        except RuntimeError as exc:
            print(f"\nSetup error: {exc}")
            sys.exit(1)

        if result is None:
            print("    -> Skipped: agent did not return a valid structured result "
                  "(see [run_agent] log above for details).")
            skipped_count += 1
            continue

        summarized_count += 1
        for name in result.tools_used:
            tool_call_counts[name] = tool_call_counts.get(name, 0) + 1

        print(f"    Category:       {result.category}")
        print(f"    Priority:       {result.priority}/5")
        print(f"    Summary:        {result.summary}")
        print(f"    Reasoning:      {result.reasoning}")
        print(f"    Tools used:     {result.tools_used or 'none'}")
        print(f"    Proposed action: {result.proposed_action or 'none'}")

    print(SEPARATOR)
    print(f"\nDone. {summarized_count}/{len(emails)} summarized ({skipped_count} skipped).")
    print(f"Tool call totals across this batch: {tool_call_counts}")
    print(
        "\nIf get_thread_context or get_sender_history fired on every single "
        "email, or never fired at all, that's worth a second look -- the "
        "point of this phase is selective tool use, not a fixed pipeline."
    )


if __name__ == "__main__":
    main()
