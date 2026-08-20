"""
Phase 4: persistence layer.

Engine/session setup plus the functions the pipeline uses to check for
and write processed emails. Everything a single email produces (the
email row, its summary, every tool-call log, and any proposed action)
is written through save_processed_email(), which wraps them all in one
transaction -- if any part fails, none of it is left half-written.
"""

from contextlib import contextmanager
from typing import Optional

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from src.agent.agent_core import AgentResult
from src.config import db_path
from src.db.models import AgentLog, Base, Email, ProposedAction, Summary
from src.gmail.client import ParsedEmail

_engine = create_engine(f"sqlite:///{db_path()}")
SessionLocal = sessionmaker(bind=_engine)


def init_db() -> None:
    """Creates all tables if they don't already exist. Safe to call every run."""
    Base.metadata.create_all(_engine)


@contextmanager
def _session_scope():
    """One transaction: commits on clean exit, rolls back on any exception."""
    session = SessionLocal()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def email_exists(email_id: str) -> bool:
    """The dedup check: has this Gmail message already been processed?"""
    with SessionLocal() as session:
        return session.get(Email, email_id) is not None


def save_email(session: Session, email: ParsedEmail) -> Email:
    row = Email(
        id=email.id,
        thread_id=email.thread_id,
        sender=email.sender,
        subject=email.subject,
        received_at=email.received_at,
        body_text=email.body_text,
    )
    session.add(row)
    return row


def save_summary(session: Session, email_id: str, result: AgentResult) -> Summary:
    row = Summary(
        email_id=email_id,
        category=result.category,
        priority=result.priority,
        summary=result.summary,
        reasoning=result.reasoning,
    )
    session.add(row)
    return row


def save_agent_log(
    session: Session, email_id: str, tool_name: str, tool_input: dict, tool_result: dict
) -> AgentLog:
    row = AgentLog(email_id=email_id, tool_name=tool_name, tool_input=tool_input, tool_result=tool_result)
    session.add(row)
    return row


def save_proposed_action(
    session: Session,
    email_id: str,
    action_type: str,
    label: Optional[str] = None,
    status: str = "pending",
) -> ProposedAction:
    row = ProposedAction(email_id=email_id, action_type=action_type, label=label, status=status)
    session.add(row)
    return row


def save_processed_email(email: ParsedEmail, result: AgentResult) -> None:
    """
    Persists everything produced for one email -- the email row, its
    summary, every tool call the agent made, and any proposed action --
    in a single transaction. If anything in here raises, the whole
    transaction rolls back, so a failure never leaves an email row with
    no summary, or a summary with no email.
    """
    with _session_scope() as session:
        save_email(session, email)
        save_summary(session, email.id, result)
        for call in result.tool_call_log:
            save_agent_log(session, email.id, call["tool_name"], call["tool_input"], call["tool_result"])
        if result.proposed_action:
            save_proposed_action(
                session,
                email.id,
                action_type=result.proposed_action.get("action"),
                label=result.proposed_action.get("label"),
            )
