"""
Unit tests for the Gmail parsing helpers. These don't touch the network
or real auth — they construct fake Gmail API payload dicts (the same
shape the API returns) and check that parsing behaves correctly.

Run with:
    python -m pytest tests/
"""

import base64
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.gmail.client import MAX_BODY_CHARS, _extract_body_text, _html_to_text


def _b64(text: str) -> str:
    return base64.urlsafe_b64encode(text.encode("utf-8")).decode("ascii")


def test_extracts_plain_text_when_present():
    payload = {
        "mimeType": "multipart/alternative",
        "parts": [
            {"mimeType": "text/plain", "body": {"data": _b64("Hello plain world")}},
            {"mimeType": "text/html", "body": {"data": _b64("<p>Hello html world</p>")}},
        ],
    }
    assert _extract_body_text(payload) == "Hello plain world"


def test_falls_back_to_html_when_no_plain_part():
    payload = {
        "mimeType": "multipart/alternative",
        "parts": [
            {"mimeType": "text/html", "body": {"data": _b64("<p>Only <b>html</b> here</p>")}},
        ],
    }
    assert "Only html here" in _extract_body_text(payload)
    assert "<" not in _extract_body_text(payload)


def test_recurses_into_nested_multipart():
    payload = {
        "mimeType": "multipart/mixed",
        "parts": [
            {
                "mimeType": "multipart/alternative",
                "parts": [
                    {"mimeType": "text/plain", "body": {"data": _b64("Nested plain text")}},
                ],
            },
            {"mimeType": "application/pdf", "body": {"attachmentId": "abc123"}},
        ],
    }
    assert _extract_body_text(payload) == "Nested plain text"


def test_html_to_text_strips_tags_and_unescapes_entities():
    html = "<div>Hi &amp; welcome<br>Line two</div>"
    text = _html_to_text(html)
    assert "&amp;" not in text
    assert "Hi & welcome" in text
    assert "Line two" in text


def test_no_body_returns_empty_string():
    payload = {"mimeType": "text/plain", "body": {}}
    assert _extract_body_text(payload) == ""


def test_body_truncation_constant_is_reasonable():
    # Guards against accidentally changing this without noticing —
    # get_message() relies on this to avoid context window bloat downstream.
    assert MAX_BODY_CHARS == 10_000
