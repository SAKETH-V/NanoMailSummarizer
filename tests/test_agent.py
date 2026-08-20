"""
Unit tests for the Phase 3 agent: the plain tool implementations in
src/agent/tools.py, and the tool-use loop in src/agent/agent_core.py.
The Anthropic client is mocked throughout -- no real API calls.

Run with:
    python -m pytest tests/
"""

import sys
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.agent.agent_core import run_agent
from src.agent.tools import get_sender_history, propose_gmail_action
from src.config import AGENT_MAX_TOOL_CALLS
from src.gmail.client import ParsedEmail


def _fake_email(**overrides) -> ParsedEmail:
    defaults = dict(
        id="msg1",
        thread_id="thread1",
        sender="alice@example.com",
        subject="Hello",
        received_at=datetime(2026, 7, 1, tzinfo=timezone.utc),
        body_text="Just checking in.",
    )
    defaults.update(overrides)
    return ParsedEmail(**defaults)


# --------------------------------------------------------------------------
# tools.py -- plain function tests, no API involved
# --------------------------------------------------------------------------


def test_get_sender_history_counts_matching_sender():
    known = [
        _fake_email(id="1", sender="Alice <alice@example.com>"),
        _fake_email(id="2", sender="alice@example.com"),
        _fake_email(id="3", sender="bob@example.com"),
    ]
    result = get_sender_history("alice@example.com", known)
    assert result["count_in_recent_batch"] == 2


def test_get_sender_history_no_matches():
    known = [_fake_email(id="1", sender="bob@example.com")]
    result = get_sender_history("nobody@example.com", known)
    assert result["count_in_recent_batch"] == 0
    assert result["most_recent_at"] is None


def test_propose_gmail_action_archive_is_recorded_not_executed():
    result = propose_gmail_action("msg1", "archive")
    assert result["status"] == "proposed_only"
    assert result["action"] == "archive"


def test_propose_gmail_action_label_requires_label_value():
    result = propose_gmail_action("msg1", "label")
    assert "error" in result


def test_propose_gmail_action_rejects_unknown_action():
    result = propose_gmail_action("msg1", "delete")
    assert "error" in result


# --------------------------------------------------------------------------
# agent_core.py -- tool-use loop tests, with a fully mocked Anthropic client
# --------------------------------------------------------------------------


class _FakeMessages:
    """Returns a fixed final JSON response with no tool calls."""

    def __init__(self, final_json: str):
        self.final_json = final_json
        self.calls = 0

    def create(self, **kwargs):
        self.calls += 1
        return SimpleNamespace(
            content=[SimpleNamespace(type="text", text=self.final_json)],
            usage=SimpleNamespace(input_tokens=10, output_tokens=5),
        )


class _FakeClient:
    def __init__(self, final_json: str):
        self.messages = _FakeMessages(final_json)


class _AlwaysToolCallMessages:
    """
    Simulates a model that keeps wanting to call a tool. Respects the
    `tools` kwarg the way the real API would: if tools=[] (agent's tool
    budget exhausted), it returns the final answer instead of a tool call.
    """

    def __init__(self, final_json: str):
        self.final_json = final_json
        self.calls = 0

    def create(self, **kwargs):
        self.calls += 1
        if kwargs.get("tools"):
            block = SimpleNamespace(
                type="tool_use",
                name="get_sender_history",
                input={"sender_email": "alice@example.com"},
                id=f"call_{self.calls}",
            )
            return SimpleNamespace(content=[block], usage=SimpleNamespace(input_tokens=10, output_tokens=5))
        return SimpleNamespace(
            content=[SimpleNamespace(type="text", text=self.final_json)],
            usage=SimpleNamespace(input_tokens=10, output_tokens=5),
        )


class _AlwaysToolCallClient:
    def __init__(self, final_json: str):
        self.messages = _AlwaysToolCallMessages(final_json)


_VALID_FINAL_JSON = (
    '{"category": "Personal", "priority": 2, '
    '"summary": "Alice is checking in.", "reasoning": "Casual personal note."}'
)


def test_agent_returns_result_with_no_tool_calls_when_none_needed():
    client = _FakeClient(_VALID_FINAL_JSON)
    result = run_agent(_fake_email(), service=None, known_emails=[], client=client)

    assert result is not None
    assert result.category == "Personal"
    assert result.tools_used == []
    assert result.proposed_action is None


def test_agent_caps_tool_calls_at_configured_max():
    client = _AlwaysToolCallClient(_VALID_FINAL_JSON)
    result = run_agent(_fake_email(), service=None, known_emails=[], client=client)

    assert result is not None
    # A model that always wants to call tools should still be capped,
    # never allowed to loop indefinitely.
    assert len(result.tools_used) == AGENT_MAX_TOOL_CALLS


def test_agent_rejects_off_enum_category_in_final_answer():
    bad_json = '{"category": "Not Real", "priority": 3, "summary": "x", "reasoning": "y"}'
    client = _FakeClient(bad_json)
    result = run_agent(_fake_email(), service=None, known_emails=[], client=client)

    assert result is None


def test_agent_survives_malformed_final_json():
    client = _FakeClient("not json {{{")
    result = run_agent(_fake_email(), service=None, known_emails=[], client=client)

    assert result is None
