"""
Phase 3: Agent tools.

Each tool has two halves:
  - a JSON schema in TOOL_DEFINITIONS (what Claude sees and can call)
  - a plain Python function below (what actually runs locally when
    Claude calls it)

None of these write to Gmail. propose_gmail_action only *records* a
proposed action for later human/Phase 6 review -- it never calls the
Gmail API to actually archive or label anything.
"""

from email.utils import parseaddr
from typing import Optional

from src.gmail.client import ParsedEmail, get_thread_messages

MAX_THREAD_MESSAGES = 5  # cap how much thread history we hand back to Claude
SNIPPET_CHARS = 200  # thread context is for gauging tone/topic, not full re-reading

TOOL_DEFINITIONS = [
    {
        "name": "get_thread_context",
        "description": (
            "Fetch prior messages in this email's thread (sender, subject, "
            "a short snippet, and timestamp for each, oldest first). Only "
            "call this when the email looks like a reply -- e.g. the "
            "subject starts with 'Re:' or otherwise clearly continues a "
            "prior exchange. Do not call this on emails that are obviously "
            "first contact (newsletters, cold outreach, automated "
            "notifications, receipts) -- there is no useful thread history "
            "to fetch and it wastes a call."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "thread_id": {
                    "type": "string",
                    "description": "The Gmail thread ID of the email being triaged.",
                }
            },
            "required": ["thread_id"],
        },
    },
    {
        "name": "get_sender_history",
        "description": (
            "Look up how many emails from this sender appear in the "
            "recently-fetched batch, and the timestamp of the most recent "
            "one. Only call this when it is genuinely ambiguous whether "
            "the sender is a known, regular correspondent or a stranger. "
            "Do not call this for obvious cases -- e.g. a sender whose "
            "name/domain already makes clear they're a newsletter, an "
            "automated system, or an already-established personal contact."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "sender_email": {
                    "type": "string",
                    "description": "The sender's bare email address (not the full 'Name <email>' header).",
                }
            },
            "required": ["sender_email"],
        },
    },
    {
        "name": "propose_gmail_action",
        "description": (
            "Record a proposed Gmail action for later human review. This "
            "does NOT actually archive or label anything in Gmail -- it "
            "only logs a suggestion for now. Only call this when you are "
            "highly confident (e.g. an obvious newsletter or automated "
            "notification -> propose archive). If you are at all "
            "uncertain, do not call this tool -- leave the email with no "
            "proposed action rather than guessing."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "message_id": {"type": "string", "description": "The Gmail message ID."},
                "action": {
                    "type": "string",
                    "enum": ["archive", "label"],
                    "description": "The proposed action.",
                },
                "label": {
                    "type": "string",
                    "description": "Required when action is 'label' -- the label to apply.",
                },
            },
            "required": ["message_id", "action"],
        },
    },
]


def get_thread_context(service, thread_id: str, exclude_message_id: Optional[str] = None) -> list[dict]:
    """
    Returns up to MAX_THREAD_MESSAGES prior messages in the thread, oldest
    first, as compact dicts -- not full ParsedEmail objects, since the
    agent needs enough to gauge the conversation, not full bodies.
    """
    messages = get_thread_messages(service, thread_id)
    if exclude_message_id:
        messages = [m for m in messages if m.id != exclude_message_id]
    messages = messages[-MAX_THREAD_MESSAGES:]

    return [
        {
            "sender": m.sender,
            "subject": m.subject,
            "snippet": m.body_text[:SNIPPET_CHARS],
            "received_at": m.received_at.isoformat() if m.received_at else None,
        }
        for m in messages
    ]


def get_sender_history(sender_email: str, known_emails: list[ParsedEmail]) -> dict:
    """
    Counts how many of the currently in-memory fetched emails (this run's
    Phase 1 batch) came from this sender, and the most recent timestamp.
    Deliberately not exhaustive -- no DB, no full-mailbox search. Good
    enough for a triage-time "regular or stranger" signal.
    """
    target = _normalize_address(sender_email)
    matches = [m for m in known_emails if _normalize_address(m.sender) == target]

    most_recent = None
    for m in matches:
        if m.received_at and (most_recent is None or m.received_at > most_recent):
            most_recent = m.received_at

    return {
        "sender_email": sender_email,
        "count_in_recent_batch": len(matches),
        "most_recent_at": most_recent.isoformat() if most_recent else None,
    }


def propose_gmail_action(message_id: str, action: str, label: Optional[str] = None) -> dict:
    """
    Records (does not execute) a proposed action. Actually writing to
    Gmail -- archiving, applying labels -- is deliberately deferred to a
    later phase with a real human approval flow.
    """
    if action not in ("archive", "label"):
        return {"error": f"Unknown action '{action}'. Must be 'archive' or 'label'."}
    if action == "label" and not label:
        return {"error": "action='label' requires a non-empty 'label' value."}

    return {
        "message_id": message_id,
        "action": action,
        "label": label,
        "status": "proposed_only",
        "note": "Not executed -- awaiting a human/Phase 6 approval flow.",
    }


def _normalize_address(sender_header: str) -> str:
    _, address = parseaddr(sender_header)
    return address.lower().strip()
