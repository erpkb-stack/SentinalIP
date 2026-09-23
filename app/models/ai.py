"""AI agent execution records and findings."""
from __future__ import annotations

import datetime as dt
from typing import List, Optional

from sqlalchemy import (
    JSON,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.constants import AgentRunStatus
from app.database import Base, utcnow


class AiAgentRun(Base):
    """One execution of one agent against one case. Append-only in practice."""

    __tablename__ = "ai_agent_runs"
    __table_args__ = (
        Index("ix_agent_runs_case_seq", "case_id", "sequence"),
        Index("ix_agent_runs_agent_started", "agent_name", "started_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    case_id: Mapped[int] = mapped_column(
        ForeignKey("cases.id", ondelete="CASCADE"), nullable=False, index=True
    )
    vendor_id: Mapped[int] = mapped_column(
        ForeignKey("vendors.id", ondelete="CASCADE"), nullable=False, index=True
    )

    agent_name: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    sequence: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    status: Mapped[str] = mapped_column(
        String(32), default=AgentRunStatus.PENDING.value, nullable=False, index=True
    )

    provider: Mapped[Optional[str]] = mapped_column(String(64))
    model: Mapped[Optional[str]] = mapped_column(String(128))

    started_at: Mapped[Optional[dt.datetime]] = mapped_column(DateTime, index=True)
    completed_at: Mapped[Optional[dt.datetime]] = mapped_column(DateTime)
    duration_ms: Mapped[Optional[int]] = mapped_column(Integer)

    confidence: Mapped[Optional[float]] = mapped_column(Numeric(5, 2))
    risk_level: Mapped[Optional[str]] = mapped_column(String(32))
    evidence_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    summary: Mapped[Optional[str]] = mapped_column(Text)

    #: Structured, auditable agent output. Never contains hidden reasoning -
    #: only the concise conclusions and the evidence references behind them.
    output: Mapped[Optional[dict]] = mapped_column(JSON)

    error: Mapped[Optional[str]] = mapped_column(Text)
    correlation_id: Mapped[Optional[str]] = mapped_column(String(64), index=True)
    triggered_by_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=utcnow, nullable=False)

    case: Mapped["Case"] = relationship(back_populates="agent_runs")  # noqa: F821
    findings: Mapped[List["AiFinding"]] = relationship(
        back_populates="agent_run", cascade="all, delete-orphan"
    )

    def __repr__(self) -> str:  # pragma: no cover
        return f"<AiAgentRun {self.agent_name} case={self.case_id} {self.status}>"


class AiFinding(Base):
    """A single, citable conclusion produced by an agent."""

    __tablename__ = "ai_findings"
    __table_args__ = (
        Index("ix_findings_case_kind", "case_id", "kind"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    case_id: Mapped[int] = mapped_column(
        ForeignKey("cases.id", ondelete="CASCADE"), nullable=False, index=True
    )
    vendor_id: Mapped[int] = mapped_column(
        ForeignKey("vendors.id", ondelete="CASCADE"), nullable=False, index=True
    )
    agent_run_id: Mapped[int] = mapped_column(
        ForeignKey("ai_agent_runs.id", ondelete="CASCADE"), nullable=False, index=True
    )

    kind: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    title: Mapped[str] = mapped_column(String(512), nullable=False)
    detail: Mapped[Optional[str]] = mapped_column(Text)
    #: relative contribution to the score, 0-100
    weight: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    confidence: Mapped[Optional[float]] = mapped_column(Numeric(5, 2))
    #: Evidence refs (EV-...) this finding rests on.
    evidence_refs: Mapped[Optional[list]] = mapped_column(JSON)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=utcnow, nullable=False)

    agent_run: Mapped["AiAgentRun"] = relationship(back_populates="findings")
