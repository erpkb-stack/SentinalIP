"""Executive dashboard + reports.

Every aggregate is computed over the caller's tenant scope. A vendor user's
dashboard is their vendor's dashboard; a Super Admin sees the platform.
"""
from __future__ import annotations

import datetime as dt
from typing import Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy import case as sa_case, func, select
from sqlalchemy.orm import Session

from app.auth import get_current_user, scope_to_vendor
from app.constants import (
    ACTIVE_INVESTIGATION_STATUSES,
    ENFORCEMENT_ACTION_LABELS,
    INFRINGEMENT_LABELS,
    SUCCESSFUL_ENFORCEMENT_RESULTS,
    CaseStatus,
    EnforcementStatus,
    RiskLevel,
    SlaState,
)
from app.database import get_db, utcnow
from app.errors import VendorAccessDenied
from app.models import Case, EnforcementAction, Marketplace, User, Vendor
from app.schemas.system import ChartSeries, DashboardResponse, StatCard
from app.services import sla

router = APIRouter(prefix="/api", tags=["dashboard"])

PALETTE = [
    "#BF5700", "#8C3F00", "#D97706", "#16A34A", "#DC2626",
    "#6B7280", "#1F1F1F", "#E08A46", "#A8541F", "#9CA3AF",
]


def _scoped_cases(db: Session, user: User, vendor_id: Optional[int]):
    stmt = scope_to_vendor(select(Case), Case, user)
    if vendor_id is not None:
        if not user.is_global and vendor_id != user.vendor_id:
            raise VendorAccessDenied()
        stmt = stmt.where(Case.vendor_id == vendor_id)
    return stmt


@router.get("/dashboard", response_model=DashboardResponse)
def dashboard(
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
    vendor_id: Optional[int] = None,
    days: int = Query(90, ge=7, le=365),
):
    now = utcnow()
    window_start = now - dt.timedelta(days=days)
    base = _scoped_cases(db, user, vendor_id)
    case_ids = base.with_only_columns(Case.id).subquery()

    def count_where(*conditions) -> int:
        stmt = base.with_only_columns(func.count(Case.id))
        for cond in conditions:
            stmt = stmt.where(cond)
        return int(db.execute(stmt).scalar() or 0)

    total_cases = count_where()
    active = count_where(Case.status.in_(ACTIVE_INVESTIGATION_STATUSES))
    high_risk = count_where(
        Case.risk_level.in_([RiskLevel.HIGH.value, RiskLevel.CRITICAL.value])
    )
    awaiting = count_where(Case.status == CaseStatus.AWAITING_APPROVAL.value)
    at_risk = count_where(
        Case.sla_state == SlaState.AT_RISK.value,
        Case.status.notin_([CaseStatus.RESOLVED.value, CaseStatus.CLOSED.value]),
    )
    breached = count_where(Case.sla_state == SlaState.BREACHED.value)

    # ---- enforcement success ----
    enf = scope_to_vendor(select(EnforcementAction), EnforcementAction, user)
    if vendor_id is not None:
        enf = enf.where(EnforcementAction.vendor_id == vendor_id)
    concluded = db.execute(
        enf.with_only_columns(func.count(EnforcementAction.id))
        .where(EnforcementAction.result.isnot(None))
    ).scalar() or 0
    succeeded = db.execute(
        enf.with_only_columns(func.count(EnforcementAction.id))
        .where(EnforcementAction.result.in_(list(SUCCESSFUL_ENFORCEMENT_RESULTS)))
    ).scalar() or 0
    success_rate = round((succeeded / concluded * 100), 1) if concluded else 0.0

    cards = [
        StatCard(key="total_cases", label="Total Cases", value=total_cases,
                 hint="All cases in scope"),
        StatCard(key="active", label="Active Investigations", value=active,
                 hint="In analysis, approval or filing"),
        StatCard(key="high_risk", label="High-Risk Cases", value=high_risk,
                 tone="warning", hint="Confidence 70% and above"),
        StatCard(key="awaiting", label="Awaiting Approval", value=awaiting,
                 tone="warning", hint="A human decision is required"),
        StatCard(key="sla_at_risk", label="SLA At Risk", value=at_risk, tone="warning"),
        StatCard(key="sla_breached", label="SLA Breached", value=breached, tone="danger"),
        StatCard(key="success_rate", label="Successful Enforcement",
                 value=success_rate, unit="%", tone="success",
                 hint=f"{succeeded} of {concluded} concluded filings"),
    ]

    # ---- cases by status ----
    status_rows = db.execute(
        base.with_only_columns(Case.status, func.count(Case.id))
        .group_by(Case.status)
    ).all()
    status_map = {s: c for s, c in status_rows}
    status_order = [s for s in CaseStatus.values() if status_map.get(s)]
    cases_by_status = ChartSeries(
        labels=[s.replace("_", " ").title() for s in status_order],
        values=[float(status_map[s]) for s in status_order],
        colors=[PALETTE[i % len(PALETTE)] for i in range(len(status_order))],
    )

    # ---- cases by marketplace ----
    mp_rows = db.execute(
        base.with_only_columns(Marketplace.name, func.count(Case.id))
        .join(Marketplace, Case.marketplace_id == Marketplace.id)
        .group_by(Marketplace.name)
        .order_by(func.count(Case.id).desc())
        .limit(10)
    ).all()
    cases_by_marketplace = ChartSeries(
        labels=[r[0] for r in mp_rows],
        values=[float(r[1]) for r in mp_rows],
        colors=[PALETTE[i % len(PALETTE)] for i in range(len(mp_rows))],
    )

    # ---- cases by infringement type ----
    inf_rows = db.execute(
        base.with_only_columns(Case.infringement_type, func.count(Case.id))
        .group_by(Case.infringement_type)
        .order_by(func.count(Case.id).desc())
    ).all()
    cases_by_infringement = ChartSeries(
        labels=[INFRINGEMENT_LABELS.get(r[0], r[0]) for r in inf_rows],
        values=[float(r[1]) for r in inf_rows],
        colors=[PALETTE[i % len(PALETTE)] for i in range(len(inf_rows))],
    )

    # ---- confidence distribution ----
    bands = [
        ("Low (0-39)", 0, 39, "#6B7280"),
        ("Medium (40-69)", 40, 69, "#D97706"),
        ("High (70-89)", 70, 89, "#BF5700"),
        ("Critical (90-100)", 90, 100, "#DC2626"),
    ]
    conf_values, conf_labels, conf_colors = [], [], []
    for label, lo, hi, color in bands:
        conf_labels.append(label)
        conf_colors.append(color)
        conf_values.append(float(count_where(
            Case.ai_confidence >= lo, Case.ai_confidence <= hi
        )))
    confidence_distribution = ChartSeries(
        labels=conf_labels, values=conf_values, colors=conf_colors
    )

    # ---- SLA performance ----
    sla_labels = ["On Track", "At Risk", "Breached", "Met"]
    sla_states = [SlaState.ON_TRACK.value, SlaState.AT_RISK.value,
                  SlaState.BREACHED.value, SlaState.MET.value]
    sla_performance = ChartSeries(
        labels=sla_labels,
        values=[float(count_where(Case.sla_state == s)) for s in sla_states],
        colors=["#16A34A", "#D97706", "#DC2626", "#BF5700"],
    )

    # ---- enforcement success rate by action type ----
    action_rows = db.execute(
        enf.with_only_columns(
            EnforcementAction.action_type,
            func.count(EnforcementAction.id),
            func.sum(
                sa_case(
                    (EnforcementAction.result.in_(list(SUCCESSFUL_ENFORCEMENT_RESULTS)), 1),
                    else_=0,
                )
            ),
        )
        .where(EnforcementAction.result.isnot(None))
        .group_by(EnforcementAction.action_type)
    ).all()
    enforcement_success = ChartSeries(
        labels=[ENFORCEMENT_ACTION_LABELS.get(r[0], r[0]) for r in action_rows],
        values=[
            round(float(r[2] or 0) / float(r[1]) * 100, 1) if r[1] else 0.0
            for r in action_rows
        ],
        colors=[PALETTE[i % len(PALETTE)] for i in range(len(action_rows))],
    )

    # ---- cases over time (weekly buckets, portable across MySQL/SQLite) ----
    created_rows = db.execute(
        base.with_only_columns(Case.created_at).where(Case.created_at >= window_start)
    ).all()
    buckets: dict[dt.date, int] = {}
    for (created,) in created_rows:
        monday = (created - dt.timedelta(days=created.weekday())).date()
        buckets[monday] = buckets.get(monday, 0) + 1
    week = (window_start - dt.timedelta(days=window_start.weekday())).date()
    end_week = (now - dt.timedelta(days=now.weekday())).date()
    labels, values = [], []
    while week <= end_week:
        labels.append(week.strftime("%d %b"))
        values.append(float(buckets.get(week, 0)))
        week += dt.timedelta(days=7)
    cases_over_time = ChartSeries(
        labels=labels, values=values, colors=["#BF5700"]
    )

    # ---- recent + attention lists ----
    recent = db.execute(
        base.order_by(Case.created_at.desc()).limit(8)
    ).scalars().all()
    attention = db.execute(
        base.where(Case.status == CaseStatus.AWAITING_APPROVAL.value)
        # MySQL has no NULLS LAST; sorting on the null-ness first is portable.
        .order_by(Case.sla_deadline.is_(None).asc(), Case.sla_deadline.asc())
        .limit(8)
    ).scalars().all()

    def row(c: Case) -> dict:
        view = sla.case_sla_view(c, now=now)
        return {
            "id": c.id,
            "case_number": c.case_number,
            "title": c.title,
            "vendor": c.vendor.name if c.vendor else None,
            "status": c.status,
            "confidence": float(c.ai_confidence) if c.ai_confidence is not None else None,
            "risk_level": c.risk_level,
            "recommended_action": ENFORCEMENT_ACTION_LABELS.get(
                c.recommended_action or "", None),
            "sla_state": view["state"],
            "sla_label": view["remaining_label"],
            "created_at": c.created_at,
        }

    scope_vendor = None
    if not user.is_global:
        scope_vendor = user.vendor.name if user.vendor else None
    elif vendor_id:
        v = db.get(Vendor, vendor_id)
        scope_vendor = v.name if v else None

    return DashboardResponse(
        scope="platform" if user.is_global and vendor_id is None else "vendor",
        vendor_name=scope_vendor,
        generated_at=now,
        cards=cards,
        cases_by_status=cases_by_status,
        cases_by_marketplace=cases_by_marketplace,
        cases_by_infringement=cases_by_infringement,
        confidence_distribution=confidence_distribution,
        sla_performance=sla_performance,
        enforcement_success=enforcement_success,
        cases_over_time=cases_over_time,
        recent_cases=[row(c) for c in recent],
        attention=[row(c) for c in attention],
    )


@router.get("/reports/summary")
def reports_summary(
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
    vendor_id: Optional[int] = None,
    days: int = Query(90, ge=7, le=365),
):
    """Tabular report data: vendor breakdown, marketplace effectiveness, agent load."""
    now = utcnow()
    since = now - dt.timedelta(days=days)
    base = _scoped_cases(db, user, vendor_id).where(Case.created_at >= since)

    by_vendor = db.execute(
        base.with_only_columns(
            Vendor.name,
            func.count(Case.id),
            func.avg(Case.ai_confidence),
            func.sum(sa_case((Case.sla_state == SlaState.BREACHED.value, 1), else_=0)),
            func.sum(sa_case((Case.status == CaseStatus.RESOLVED.value, 1), else_=0)),
        )
        .join(Vendor, Case.vendor_id == Vendor.id)
        .group_by(Vendor.name)
        .order_by(func.count(Case.id).desc())
    ).all()

    by_marketplace = db.execute(
        base.with_only_columns(
            Marketplace.name,
            func.count(Case.id),
            func.avg(Case.ai_confidence),
            func.sum(sa_case((Case.status == CaseStatus.RESOLVED.value, 1), else_=0)),
        )
        .join(Marketplace, Case.marketplace_id == Marketplace.id)
        .group_by(Marketplace.name)
        .order_by(func.count(Case.id).desc())
    ).all()

    return {
        "generated_at": now,
        "window_days": days,
        "by_vendor": [
            {
                "vendor": r[0], "cases": int(r[1]),
                "avg_confidence": round(float(r[2]), 1) if r[2] is not None else None,
                "sla_breached": int(r[3] or 0),
                "resolved": int(r[4] or 0),
            }
            for r in by_vendor
        ],
        "by_marketplace": [
            {
                "marketplace": r[0], "cases": int(r[1]),
                "avg_confidence": round(float(r[2]), 1) if r[2] is not None else None,
                "resolved": int(r[3] or 0),
            }
            for r in by_marketplace
        ],
    }
