"""Cases, assignments and evidence."""
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

from app.constants import (
    CaseStatus,
    EvidenceRelevance,
    Priority,
    RiskLevel,
    SlaState,
)
from app.database import Base, utcnow


class Case(Base):
    __tablename__ = "cases"
    __table_args__ = (
        Index("ix_cases_vendor_status", "vendor_id", "status"),
        Index("ix_cases_vendor_created", "vendor_id", "created_at"),
        Index("ix_cases_sla", "sla_state", "sla_deadline"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    case_number: Mapped[str] = mapped_column(
        String(64), unique=True, nullable=False, index=True
    )

    # ---- tenancy: the security boundary ----
    vendor_id: Mapped[int] = mapped_column(
        ForeignKey("vendors.id", ondelete="RESTRICT"), nullable=False, index=True
    )

    title: Mapped[str] = mapped_column(String(512), nullable=False)
    description: Mapped[Optional[str]] = mapped_column(Text)

    product_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("products.id", ondelete="SET NULL"), index=True
    )
    listing_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("listings.id", ondelete="SET NULL"), index=True
    )
    marketplace_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("marketplaces.id", ondelete="SET NULL"), index=True
    )

    infringement_type: Mapped[str] = mapped_column(String(48), nullable=False, index=True)
    status: Mapped[str] = mapped_column(
        String(48), default=CaseStatus.DRAFT.value, nullable=False, index=True
    )
    priority: Mapped[str] = mapped_column(
        String(32), default=Priority.MEDIUM.value, nullable=False, index=True
    )

    # ---- AI outcome (denormalised for list/filter performance) ----
    ai_confidence: Mapped[Optional[float]] = mapped_column(Numeric(5, 2), index=True)
    risk_level: Mapped[Optional[str]] = mapped_column(String(32), index=True)
    ai_summary: Mapped[Optional[str]] = mapped_column(Text)
    recommended_action: Mapped[Optional[str]] = mapped_column(String(64), index=True)
    analysis_started_at: Mapped[Optional[dt.datetime]] = mapped_column(DateTime)
    analysis_completed_at: Mapped[Optional[dt.datetime]] = mapped_column(DateTime)

    # ---- autonomy & approval ----
    autonomy_tier: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    requires_approval: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    approved_at: Mapped[Optional[dt.datetime]] = mapped_column(DateTime)
    approved_by_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )

    # ---- SLA ----
    sla_policy_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("sla_policies.id", ondelete="SET NULL")
    )
    sla_deadline: Mapped[Optional[dt.datetime]] = mapped_column(DateTime, index=True)
    sla_state: Mapped[str] = mapped_column(
        String(32), default=SlaState.ON_TRACK.value, nullable=False, index=True
    )
    sla_resolved_at: Mapped[Optional[dt.datetime]] = mapped_column(DateTime)

    assigned_to_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), index=True
    )
    created_by_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), index=True
    )

    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime, default=utcnow, nullable=False, index=True
    )
    updated_at: Mapped[dt.datetime] = mapped_column(
        DateTime, default=utcnow, onupdate=utcnow, nullable=False
    )
    closed_at: Mapped[Optional[dt.datetime]] = mapped_column(DateTime)

    # ---- relationships ----
    vendor: Mapped["Vendor"] = relationship(lazy="joined")            # noqa: F821
    product: Mapped[Optional["Product"]] = relationship(lazy="joined")  # noqa: F821
    marketplace: Mapped[Optional["Marketplace"]] = relationship(lazy="joined")  # noqa: F821
    listing: Mapped[Optional["Listing"]] = relationship(  # noqa: F821
        foreign_keys=[listing_id], lazy="joined"
    )
    assigned_to: Mapped[Optional["User"]] = relationship(  # noqa: F821
        foreign_keys=[assigned_to_id], lazy="joined"
    )
    created_by: Mapped[Optional["User"]] = relationship(  # noqa: F821
        foreign_keys=[created_by_id]
    )
    sla_policy: Mapped[Optional["SlaPolicy"]] = relationship(lazy="joined")  # noqa: F821

    evidence: Mapped[List["Evidence"]] = relationship(
        back_populates="case", cascade="all, delete-orphan"
    )
    agent_runs: Mapped[List["AiAgentRun"]] = relationship(  # noqa: F821
        back_populates="case", cascade="all, delete-orphan",
        order_by="AiAgentRun.sequence"
    )

    @property
    def risk_band(self) -> str:
        return self.risk_level or RiskLevel.LOW.value

    def __repr__(self) -> str:  # pragma: no cover
        return f"<Case {self.case_number} vendor={self.vendor_id} {self.status}>"


class CaseAssignment(Base):
    """Assignment history for a case (who owned it, when)."""

    __tablename__ = "case_assignments"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    case_id: Mapped[int] = mapped_column(
        ForeignKey("cases.id", ondelete="CASCADE"), nullable=False, index=True
    )
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    vendor_id: Mapped[int] = mapped_column(
        ForeignKey("vendors.id", ondelete="CASCADE"), nullable=False, index=True
    )
    assigned_by_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )
    note: Mapped[Optional[str]] = mapped_column(String(512))
    assigned_at: Mapped[dt.datetime] = mapped_column(DateTime, default=utcnow, nullable=False)
    unassigned_at: Mapped[Optional[dt.datetime]] = mapped_column(DateTime)

    user: Mapped["User"] = relationship(foreign_keys=[user_id], lazy="joined")  # noqa: F821


class Evidence(Base):
    """An immutable item of evidence.

    Evidence is never hard-deleted; it can only be *archived* with a reason,
    and archived items stay visible in the case record and the audit trail.
    """

    __tablename__ = "evidence"
    __table_args__ = (
        Index("ix_evidence_case_category", "case_id", "category"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    evidence_ref: Mapped[str] = mapped_column(
        String(64), unique=True, nullable=False, index=True
    )
    case_id: Mapped[int] = mapped_column(
        ForeignKey("cases.id", ondelete="CASCADE"), nullable=False, index=True
    )
    vendor_id: Mapped[int] = mapped_column(
        ForeignKey("vendors.id", ondelete="CASCADE"), nullable=False, index=True
    )

    category: Mapped[str] = mapped_column(String(48), nullable=False, index=True)
    evidence_type: Mapped[str] = mapped_column(String(32), nullable=False)
    title: Mapped[str] = mapped_column(String(512), nullable=False)
    description: Mapped[Optional[str]] = mapped_column(Text)
    source: Mapped[Optional[str]] = mapped_column(String(512))
    source_url: Mapped[Optional[str]] = mapped_column(String(1024))

    #: 0-100 assessment of how strongly this item supports the finding.
    strength: Mapped[int] = mapped_column(Integer, default=50, nullable=False)
    checksum: Mapped[Optional[str]] = mapped_column(String(128), index=True)

    file_path: Mapped[Optional[str]] = mapped_column(String(1024))
    file_name: Mapped[Optional[str]] = mapped_column(String(512))
    mime_type: Mapped[Optional[str]] = mapped_column(String(191))
    file_size: Mapped[Optional[int]] = mapped_column(Integer)

    #: "SCOUT" / "VERIFICATION" / "USER:12"
    collected_by: Mapped[str] = mapped_column(String(96), default="USER", nullable=False)
    agent_run_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("ai_agent_runs.id", ondelete="SET NULL"), index=True
    )
    related_agent: Mapped[Optional[str]] = mapped_column(String(64))
    uploaded_by_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )

    relevance: Mapped[str] = mapped_column(
        String(32), default=EvidenceRelevance.UNREVIEWED.value, nullable=False, index=True
    )
    relevance_note: Mapped[Optional[str]] = mapped_column(Text)
    reviewed_by_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )
    reviewed_at: Mapped[Optional[dt.datetime]] = mapped_column(DateTime)

    #: Soft-archive only. Evidence is never silently removed.
    is_archived: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    archived_reason: Mapped[Optional[str]] = mapped_column(String(512))
    archived_by_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )
    archived_at: Mapped[Optional[dt.datetime]] = mapped_column(DateTime)

    payload: Mapped[Optional[dict]] = mapped_column(JSON)
    collected_at: Mapped[dt.datetime] = mapped_column(DateTime, default=utcnow, nullable=False)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=utcnow, nullable=False)

    case: Mapped["Case"] = relationship(back_populates="evidence")
    uploaded_by: Mapped[Optional["User"]] = relationship(  # noqa: F821
        foreign_keys=[uploaded_by_id], lazy="joined"
    )
