"""
Phase 4: SQLite schema (SQLAlchemy ORM).

Four tables:
  - emails: one row per processed email. `id` (the Gmail message id) is
    the primary key and the dedup key the pipeline checks.
  - summaries: the agent's final category/priority/summary/reasoning
    for an email.
  - agent_logs: one row per tool call the agent made while processing
    an email -- the full reasoning trail, reconstructable per email.
  - proposed_actions: proposed-but-not-executed Gmail actions, sitting
    at status "pending" until a later phase adds real approval/execution.
"""

from datetime import datetime, timezone

from sqlalchemy import DateTime, ForeignKey, Integer, JSON, String, Text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Email(Base):
    __tablename__ = "emails"

    id: Mapped[str] = mapped_column(String, primary_key=True)  # Gmail message id
    thread_id: Mapped[str] = mapped_column(String, index=True)
    sender: Mapped[str] = mapped_column(String)
    subject: Mapped[str] = mapped_column(String)
    received_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    body_text: Mapped[str] = mapped_column(Text)
    processed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)

    summaries: Mapped[list["Summary"]] = relationship(back_populates="email", cascade="all, delete-orphan")
    agent_logs: Mapped[list["AgentLog"]] = relationship(back_populates="email", cascade="all, delete-orphan")
    proposed_actions: Mapped[list["ProposedAction"]] = relationship(
        back_populates="email", cascade="all, delete-orphan"
    )


class Summary(Base):
    __tablename__ = "summaries"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    email_id: Mapped[str] = mapped_column(ForeignKey("emails.id"), index=True)
    category: Mapped[str] = mapped_column(String)
    priority: Mapped[int] = mapped_column(Integer)
    summary: Mapped[str] = mapped_column(Text)
    reasoning: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)

    email: Mapped["Email"] = relationship(back_populates="summaries")


class AgentLog(Base):
    __tablename__ = "agent_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    email_id: Mapped[str] = mapped_column(ForeignKey("emails.id"), index=True)
    tool_name: Mapped[str] = mapped_column(String)
    tool_input: Mapped[dict] = mapped_column(JSON)
    tool_result: Mapped[dict] = mapped_column(JSON)
    called_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)

    email: Mapped["Email"] = relationship(back_populates="agent_logs")


class ProposedAction(Base):
    __tablename__ = "proposed_actions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    email_id: Mapped[str] = mapped_column(ForeignKey("emails.id"), index=True)
    action_type: Mapped[str] = mapped_column(String)  # "archive" | "label"
    label: Mapped[str | None] = mapped_column(String, nullable=True)
    status: Mapped[str] = mapped_column(String, default="pending")  # pending/approved/rejected
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    email: Mapped["Email"] = relationship(back_populates="proposed_actions")
