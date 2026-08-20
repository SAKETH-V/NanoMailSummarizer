"""
Phase 4: pipeline orchestration.

Ties fetch (Phase 1) -> dedup check -> agent (Phase 3) -> persist
(Phase 4) into one reusable function. Kept separate from
scripts/pipeline_test.py so Phase 5's scheduler can import and call
process_inbox() directly on a timer, without going through a script.
"""

from dataclasses import dataclass, field

from src.agent import run_agent
from src.auth import get_gmail_service
from src.db import email_exists, init_db, save_processed_email
from src.gmail import get_message, list_recent_messages


@dataclass
class PipelineStats:
    fetched: int = 0
    skipped_existing: int = 0
    processed: int = 0
    failed: int = 0
    processed_email_ids: list = field(default_factory=list)


def process_inbox(max_results: int = 10, service=None) -> PipelineStats:
    """
    Runs one full pass: fetch the most recent `max_results` inbox
    emails, skip any already in the DB (email_exists), run the agent on
    the rest, and persist each result in one transaction per email.

    Dedup is per-email and happens before the agent runs at all -- an
    already-processed email never reaches run_agent(), so it never
    triggers a Claude API call.
    """
    init_db()
    service = service or get_gmail_service()

    stats = PipelineStats()
    message_ids = list_recent_messages(service, max_results=max_results)
    stats.fetched = len(message_ids)

    # Fetch everything up front -- this batch also serves as
    # get_sender_history's in-memory lookup pool, same as Phase 3.
    emails = []
    for message_id in message_ids:
        email = get_message(service, message_id)
        if email is not None:
            emails.append(email)

    for email in emails:
        if email_exists(email.id):
            stats.skipped_existing += 1
            continue

        result = run_agent(email, service, known_emails=emails)
        if result is None:
            stats.failed += 1
            continue

        save_processed_email(email, result)
        stats.processed += 1
        stats.processed_email_ids.append(email.id)

    return stats
