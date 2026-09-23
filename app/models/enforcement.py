"""Enforcement actions and the human approval records that gate them."""
from __future__ import annotations

import datetime as dt
from typing import List, Optional

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.constants import EnforcementStatus, SlaState
from app.database import Base, utcnow


class EnforcementAction(Base):
    __tablename__ = "enforcement_actions"
    __table_args__ = (
        Index("ix_enforcement_vendor_status", "vendor_id", "status"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    reference: Mapped[str] = mapped_column(
        String(64), unique=True, nullable=False, index=True
    )
    case_id: Mapped[int] = mapped_column(
        ForeignKey("cases.id", ondelete="CASCADE"), nullable=False, index=True
    )
    vendor_id: Mapped[int] = mapped_column(
        ForeignKey("vendors.id", ondelete="CASCADE"), nullable=False, index=True
    )

    action_type: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    status: Mapped[str] = mapped_column(
        String(48), default=EnforcementStatus.DRAFT.value, nullable=False, index=True
    )

    #: The recommendation that produced this action.
    recommended_by_run_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("ai_agent_runs.id", ondelete="SET NULL")
    )
    recommendation_version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    ai_confidence: Mapped[Optional[float]] = mapped_column(Numeric(5, 2))
    reasoning: Mapped[Optional[str]] = mapped_column(Text)
    supporting_evidence: Mapped[Optional[list]] = mapped_column(JSON)
    risks: Mapped[Optional[list]] = mapped_column(JSON)
    alternatives: Mapped[Optional[list]] = mapped_column(JSON)

    notice_draft: Mapped[Optional[str]] = mapped_column(Text)
    evidence_package: Mapped[Optional[dict]] = mapped_column(JSON)

    # ---- the approval gate ----
    requires_approval: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    # enforcement_actions <-> approvals is a cycle (the action records the
    # approval that authorized it; the approval records what it approved), so
    # this constraint is added with ALTER after both tables exist.
    approval_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("approvals.id", ondelete="SET NULL", use_alter=True), index=True
    )

    # ---- filing / tracking ----
    #: Every filing in this build is simulated and labelled DEMO / SIMULATED.
    is_simulated: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    submitted_at: Mapped[Optional[dt.datetime]] = mapped_column(DateTime, index=True)
    submitted_by_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )
    submission_reference: Mapped[Optional[str]] = mapped_column(String(191))
    sla_deadline: Mapped[Optional[dt.datetime]] = mapped_column(DateTime, index=True)
    sla_state: Mapped[str] = mapped_column(
        String(32), default=SlaState.ON_TRACK.value, nullable=False
    )
    response_at: Mapped[Optional[dt.datetime]] = mapped_column(DateTime)
    marketplace_response: Mapped[Optional[str]] = mapped_column(Text)
    #: LISTING_REMOVED / SELLER_SUSPENDED / SELLER_COMPLIED / REJECTED / NO_RESPONSE
    result: Mapped[Optional[str]] = mapped_column(String(64), index=True)
    is_escalated: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    escalated_at: Mapped[Optional[dt.datetime]] = mapped_column(DateTime)
    escalation_note: Mapped[Optional[str]] = mapped_column(Text)

    created_by_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=utcnow, nullable=False)
    updated_at: Mapped[dt.datetime] = mapped_column(
        DateTime, default=utcnow, onupdate=utcnow, nullable=False
    )

    case: Mapped["Case"] = relationship()  # noqa: F821
    approvals: Mapped[List["Approval"]] = relationship(
        back_populates="enforcement_action",
        foreign_keys="Approval.enforcement_action_id",
        order_by="Approval.created_at",
    )

    def __repr__(self) -> str:  # pragma: no cover
        return f"<EnforcementAction {self.reference} {self.action_type} {self.status}>"


class Approval(Base):
    """An immutable record of a human decision on an AI recommendation."""

    __tablename__ = "approvals"
    __table_args__ = (
        Index("ix_approvals_case_created", "case_id", "created_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    case_id: Mapped[int] = mapped_column(
        ForeignKey("cases.id", ondelete="CASCADE"), nullable=False, index=True
    )
    vendor_id: Mapped[int] = mapped_column(
        ForeignKey("vendors.id", ondelete="CASCADE"), nullable=False, index=True
    )
    enforcement_action_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("enforcement_actions.id", ondelete="CASCADE"), index=True
    )

    decision: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    decided_by_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    decided_by_email: Mapped[str] = mapped_column(String(191), nullable=False)
    decided_by_role: Mapped[str] = mapped_column(String(64), nullable=False)
    decided_at: Mapped[dt.datetime] = mapped_column(DateTime, default=utcnow, nullable=False)

    comments: Mapped[Optional[str]] = mapped_column(Text)
    reason: Mapped[Optional[str]] = mapped_column(Text)

    # ---- what exactly was approved (frozen at decision time) ----
    ai_recommendation: Mapped[Optional[str]] = mapped_column(String(64))
    ai_confidence: Mapped[Optional[float]] = mapped_column(Numeric(5, 2))
    ai_risk_level: Mapped[Optional[str]] = mapped_column(String(32))
    recommendation_version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    evidence_snapshot: Mapped[Optional[dict]] = mapped_column(JSON)
    autonomy_tier: Mapped[int] = mapped_column(Integer, default=1, nullable=False)

    ip_address: Mapped[Optional[str]] = mapped_column(String(64))
    correlation_id: Mapped[Optional[str]] = mapped_column(String(64))
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=utcnow, nullable=False)

    enforcement_action: Mapped[Optional["EnforcementAction"]] = relationship(
        back_populates="approvals", foreign_keys=[enforcement_action_id]
    )
    decided_by: Mapped["User"] = relationship(  # noqa: F821
        foreign_keys=[decided_by_id], lazy="joined"
    )
