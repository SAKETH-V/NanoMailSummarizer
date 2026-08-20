"""
Thin wrapper around the Gmail API that returns clean, parsed Python
objects instead of raw Gmail JSON. This is the shape every later phase
(summarization, categorization, replies, etc.) builds on top of.
"""

import base64
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from html import unescape
from typing import Optional

from googleapiclient.errors import HttpError

MAX_BODY_CHARS = 10_000


@dataclass
class ParsedEmail:
    id: str
    thread_id: str
    sender: str
    subject: str
    received_at: Optional[datetime]
    body_text: str

    def as_dict(self) -> dict:
        return {
            "id": self.id,
            "thread_id": self.thread_id,
            "sender": self.sender,
            "subject": self.subject,
            "received_at": self.received_at.isoformat() if self.received_at else None,
            "body_text": self.body_text,
        }


def list_recent_messages(service, max_results: int = 10) -> list[str]:
    """
    Returns a list of message IDs from the inbox, most recent first.
    Gmail's `messages.list` already returns results in reverse
    chronological order for the inbox, so no extra sorting is needed.
    """
    try:
        response = (
            service.users()
            .messages()
            .list(userId="me", labelIds=["INBOX"], maxResults=max_results)
            .execute()
        )
    except HttpError as err:
        print(f"Gmail API error while listing messages: {_clean_http_error(err)}")
        return []

    return [m["id"] for m in response.get("messages", [])]


def get_message(service, message_id: str) -> Optional[ParsedEmail]:
    """
    Fetches a single full message and parses it into a ParsedEmail.
    Returns None (and prints a clean error) if the fetch fails.
    """
    try:
        raw = (
            service.users()
            .messages()
            .get(userId="me", id=message_id, format="full")
            .execute()
        )
    except HttpError as err:
        print(f"Gmail API error while fetching message {message_id}: {_clean_http_error(err)}")
        return None

    return _parse_raw_message(raw)


def get_thread_messages(service, thread_id: str) -> list[ParsedEmail]:
    """
    Fetches every message in a thread and parses each into a ParsedEmail,
    oldest first (Gmail's threads.get already returns them in that order).

    Used by the Phase 3 agent's get_thread_context tool -- lets it see
    prior messages in a conversation without re-implementing MIME parsing.
    """
    try:
        raw = (
            service.users()
            .threads()
            .get(userId="me", id=thread_id, format="full")
            .execute()
        )
    except HttpError as err:
        print(f"Gmail API error while fetching thread {thread_id}: {_clean_http_error(err)}")
        return []

    parsed = [_parse_raw_message(m) for m in raw.get("messages", [])]
    return [p for p in parsed if p is not None]


def _parse_raw_message(raw: dict) -> Optional[ParsedEmail]:
    """Shared parsing logic used by both get_message and get_thread_messages."""
    payload = raw.get("payload", {})
    headers = _headers_to_dict(payload.get("headers", []))

    body_text = _extract_body_text(payload)
    if len(body_text) > MAX_BODY_CHARS:
        body_text = body_text[:MAX_BODY_CHARS] + "\n[...truncated...]"

    message_id = raw.get("id")
    if not message_id:
        return None

    return ParsedEmail(
        id=message_id,
        thread_id=raw.get("threadId", ""),
        sender=headers.get("from", "(unknown sender)"),
        subject=headers.get("subject", "(no subject)"),
        received_at=_parse_received_at(raw, headers),
        body_text=body_text.strip() or "(no readable body)",
    )


# --------------------------------------------------------------------------
# Internal helpers
# --------------------------------------------------------------------------


def _headers_to_dict(headers: list[dict]) -> dict:
    return {h["name"].lower(): h["value"] for h in headers if "name" in h and "value" in h}


def _parse_received_at(raw: dict, headers: dict) -> Optional[datetime]:
    # Prefer Gmail's own internalDate (ms since epoch, UTC) since it's
    # reliable and doesn't depend on parsing a freeform Date header.
    internal_date = raw.get("internalDate")
    if internal_date:
        try:
            return datetime.fromtimestamp(int(internal_date) / 1000, tz=timezone.utc)
        except (ValueError, OSError):
            pass

    date_header = headers.get("date")
    if date_header:
        try:
            return parsedate_to_datetime(date_header)
        except (TypeError, ValueError):
            pass

    return None


def _extract_body_text(payload: dict) -> str:
    """
    Gmail messages are MIME multipart trees (multipart/alternative nested
    inside multipart/mixed nested inside multipart/related, etc.). We
    recursively walk the whole tree, collecting:
      - the first text/plain part we find, preferred, and
      - the first text/html part we find, as a fallback.
    Then we prefer plain text; if only HTML exists, we strip it to text.
    """
    plain_parts: list[str] = []
    html_parts: list[str] = []
    _walk_parts(payload, plain_parts, html_parts)

    if plain_parts:
        return "\n".join(plain_parts)
    if html_parts:
        return _html_to_text("\n".join(html_parts))
    return ""


def _walk_parts(part: dict, plain_parts: list[str], html_parts: list[str]) -> None:
    mime_type = part.get("mimeType", "")
    body = part.get("body", {})
    data = body.get("data")

    if data and mime_type == "text/plain":
        plain_parts.append(_decode_body_data(data))
    elif data and mime_type == "text/html":
        html_parts.append(_decode_body_data(data))

    # Recurse into any nested parts (multipart/*, attachments, etc.)
    for child in part.get("parts", []) or []:
        _walk_parts(child, plain_parts, html_parts)


def _decode_body_data(data: str) -> str:
    try:
        raw_bytes = base64.urlsafe_b64decode(data.encode("utf-8"))
    except (base64.binascii.Error, ValueError):
        return ""
    return raw_bytes.decode("utf-8", errors="replace")


_TAG_RE = re.compile(r"<[^>]+>")
_SCRIPT_STYLE_RE = re.compile(r"<(script|style)\b.*?</\1>", re.IGNORECASE | re.DOTALL)
_BLOCK_BREAK_RE = re.compile(r"</(p|div|br|tr|li|h[1-6])\s*/?>", re.IGNORECASE)
_WHITESPACE_RE = re.compile(r"[ \t]+")
_BLANK_LINES_RE = re.compile(r"\n{3,}")


def _html_to_text(html: str) -> str:
    """
    Minimal, dependency-free HTML-to-text fallback for HTML-only emails.
    Not a full HTML parser — just enough to turn marketing/HTML email
    bodies into readable plain text without leaving raw tags/entities in.
    """
    text = _SCRIPT_STYLE_RE.sub(" ", html)
    text = _BLOCK_BREAK_RE.sub("\n", text)
    text = _TAG_RE.sub(" ", text)
    text = unescape(text)
    text = _WHITESPACE_RE.sub(" ", text)
    text = _BLANK_LINES_RE.sub("\n\n", text)
    return text.strip()


def _clean_http_error(err: HttpError) -> str:
    try:
        status = err.resp.status
        reason = err.error_details or err._get_reason().strip()
        return f"HTTP {status} - {reason}"
    except Exception:
        return str(err)
