"""
Unit tests for src/db. Each test gets its own temp SQLite file (via the
temp_db fixture) so nothing here touches the real project database or
leaks state between tests.

Run with:
    python -m pytest tests/
"""

import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import src.db.database as dbmod
from src.agent.agent_core import AgentResult
from src.db import models
from src.gmail.client import ParsedEmail


@pytest.fixture
def temp_db(monkeypatch, tmp_path):
    """Points src.db.database at a fresh temp SQLite file for this test only."""
    engine = create_engine(f"sqlite:///{tmp_path / 'test.db'}")
    models.Base.metadata.create_all(engine)
    test_session_local = sessionmaker(bind=engine)

    monkeypatch.setattr(dbmod, "_engine", engine)
    monkeypatch.setattr(dbmod, "SessionLocal", test_session_local)
    return dbmod


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


def _fake_agent_result(**overrides) -> AgentResult:
    defaults = dict(
        category="Personal",
        priority=2,
        summary="Alice is checking in.",
        reasoning="Casual personal note.",
        tools_used=["get_sender_history"],
        proposed_action=None,
        tool_call_log=[
            {
                "tool_name": "get_sender_history",
                "tool_input": {"sender_email": "alice@example.com"},
                "tool_result": {"sender_email": "alice@example.com", "count_in_recent_batch": 3},
            }
        ],
    )
    defaults.update(overrides)
    return AgentResult(**defaults)


def test_email_exists_is_false_before_save(temp_db):
    assert temp_db.email_exists("msg1") is False


def test_email_exists_is_true_after_save(temp_db):
    temp_db.save_processed_email(_fake_email(), _fake_agent_result())
    assert temp_db.email_exists("msg1") is True


def test_save_processed_email_persists_summary_and_agent_logs(temp_db):
    email = _fake_email()
    result = _fake_agent_result()
    temp_db.save_processed_email(email, result)

    with temp_db.SessionLocal() as session:
        saved_email = session.get(dbmod.Email, "msg1")
        assert saved_email is not None
        assert saved_email.sender == "alice@example.com"
        assert len(saved_email.summaries) == 1
        assert saved_email.summaries[0].category == "Personal"
        assert len(saved_email.agent_logs) == 1
        assert saved_email.agent_logs[0].tool_name == "get_sender_history"


def test_save_processed_email_persists_proposed_action(temp_db):
    email = _fake_email(id="msg2")
    result = _fake_agent_result(proposed_action={"message_id": "msg2", "action": "archive", "label": None})
    temp_db.save_processed_email(email, result)

    with temp_db.SessionLocal() as session:
        saved_email = session.get(dbmod.Email, "msg2")
        assert len(saved_email.proposed_actions) == 1
        assert saved_email.proposed_actions[0].action_type == "archive"
        assert saved_email.proposed_actions[0].status == "pending"


def test_no_proposed_action_row_when_none_proposed(temp_db):
    email = _fake_email(id="msg3")
    result = _fake_agent_result(proposed_action=None)
    temp_db.save_processed_email(email, result)

    with temp_db.SessionLocal() as session:
        saved_email = session.get(dbmod.Email, "msg3")
        assert saved_email.proposed_actions == []


def test_failed_write_rolls_back_the_whole_transaction(temp_db, monkeypatch):
    """
    If any part of save_processed_email fails, nothing from that email
    should be left in the DB -- not even the email row itself.
    """

    def _boom(*args, **kwargs):
        raise RuntimeError("simulated failure while saving an agent log")

    monkeypatch.setattr(dbmod, "save_agent_log", _boom)

    with pytest.raises(RuntimeError):
        dbmod.save_processed_email(_fake_email(id="msg4"), _fake_agent_result())

    assert dbmod.email_exists("msg4") is False
