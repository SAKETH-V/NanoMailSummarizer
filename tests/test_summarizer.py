"""
Unit tests for the summarizer's response parsing/validation. We mock
the Anthropic client entirely -- no real API calls, no API key needed.

Run with:
    python -m pytest tests/
"""

import sys
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.ai.summarizer import EmailSummary, summarize_email
from src.gmail.client import ParsedEmail


def _fake_email() -> ParsedEmail:
    return ParsedEmail(
        id="msg123",
        thread_id="thread123",
        sender="newsletter@example.com",
        subject="Your weekly digest",
        received_at=None,
        body_text="Here are this week's top stories from around the web...",
    )


class _FakeMessages:
    def __init__(self, response_text: str):
        self._response_text = response_text

    def create(self, **kwargs):
        return SimpleNamespace(
            content=[SimpleNamespace(type="text", text=self._response_text)],
            usage=SimpleNamespace(input_tokens=42, output_tokens=17),
        )


class _FakeClient:
    def __init__(self, response_text: str):
        self.messages = _FakeMessages(response_text)


def test_parses_valid_json_response():
    valid_json = (
        '{"category": "Newsletter", "priority": 1, '
        '"summary": "A roundup of the week\'s top stories.", '
        '"reasoning": "Recurring bulk content, no action needed."}'
    )
    client = _FakeClient(valid_json)
    result = summarize_email(_fake_email(), client=client)

    assert isinstance(result, EmailSummary)
    assert result.category == "Newsletter"
    assert result.priority == 1


def test_strips_markdown_fences_before_parsing():
    fenced = (
        '```json\n{"category": "FYI", "priority": 2, '
        '"summary": "Just an update.", "reasoning": "Informational only."}\n```'
    )
    client = _FakeClient(fenced)
    result = summarize_email(_fake_email(), client=client)

    assert result is not None
    assert result.category == "FYI"


def test_rejects_off_enum_category_as_parse_failure():
    bad_category = (
        '{"category": "Super Important", "priority": 5, '
        '"summary": "x", "reasoning": "y"}'
    )
    client = _FakeClient(bad_category)
    result = summarize_email(_fake_email(), client=client)

    assert result is None


def test_malformed_json_does_not_crash_and_returns_none():
    client = _FakeClient("this is not json at all {{{")
    result = summarize_email(_fake_email(), client=client)

    assert result is None


def test_priority_out_of_range_is_rejected():
    bad_priority = (
        '{"category": "Urgent", "priority": 9, '
        '"summary": "x", "reasoning": "y"}'
    )
    client = _FakeClient(bad_priority)
    result = summarize_email(_fake_email(), client=client)

    assert result is None
