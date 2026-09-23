"""SLA computation.

A case's SLA clock starts at creation and stops when a human decision has
been recorded (approve / reject) or the case is closed.
"""
from __future__ import annotations

import datetime as dt
from typing import Dict, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import settings
from app.constants import CLOSED_CASE_STATUSES, CaseStatus, Priority, SlaState
from app.database import utcnow
from app.models import Case, SlaPolicy

#: Priority multipliers applied to a policy's base response window.
PRIORITY_FACTORS = {
    Priority.CRITICAL.value: 0.25,
    Priority.HIGH.value: 0.5,
    Priority.MEDIUM.value: 1.0,
    Priority.LOW.value: 1.5,
}


def resolve_policy(db: Session, vendor_id: Optional[int]) -> Optional[SlaPolicy]:
    """Vendor-specific default first, then the global default."""
    if vendor_id is not None:
        stmt = (
            select(SlaPolicy)
            .where(
                SlaPolicy.vendor_id == vendor_id,
                SlaPolicy.is_active.is_(True),
                SlaPolicy.is_default.is_(True),
            )
            .limit(1)
        )
        policy = db.execute(stmt).scalars().first()
        if policy:
            return policy
    stmt = (
        select(SlaPolicy)
        .where(
            SlaPolicy.vendor_id.is_(None),
            SlaPolicy.is_active.is_(True),
            SlaPolicy.is_default.is_(True),
        )
        .limit(1)
    )
    return db.execute(stmt).scalars().first()


def compute_deadline(
    started_at: dt.datetime,
    policy: Optional[SlaPolicy],
    priority: str = Priority.MEDIUM.value,
) -> dt.datetime:
    base_hours = policy.response_hours if policy else settings.default_sla_hours
    factor = PRIORITY_FACTORS.get(priority, 1.0)
    hours = max(1.0, base_hours * factor)
    return started_at + dt.timedelta(hours=hours)


def evaluate(
    *,
    started_at: dt.datetime,
    deadline: Optional[dt.datetime],
    resolved_at: Optional[dt.datetime] = None,
    at_risk_percent: Optional[int] = None,
    now: Optional[dt.datetime] = None,
) -> Dict[str, object]:
    """Return a full SLA view: state, remaining time, % elapsed."""
    now = now or utcnow()
    at_risk_percent = at_risk_percent or settings.sla_at_risk_percent

    if deadline is None:
        return {
            "state": SlaState.ON_TRACK.value,
            "deadline": None,
            "remaining_seconds": None,
            "remaining_label": "No SLA",
            "elapsed_percent": 0,
            "is_breached": False,
        }

    total = max(1.0, (deadline - started_at).total_seconds())

    if resolved_at is not None:
        elapsed = (resolved_at - started_at).total_seconds()
        met = resolved_at <= deadline
        return {
            "state": SlaState.MET.value if met else SlaState.BREACHED.value,
            "deadline": deadline,
            "remaining_seconds": int((deadline - resolved_at).total_seconds()),
            "remaining_label": "Met" if met else "Breached",
            "elapsed_percent": min(100, int(round(elapsed / total * 100))),
            "is_breached": not met,
        }

    remaining = (deadline - now).total_seconds()
    elapsed_pct = min(100, max(0, int(round((total - remaining) / total * 100))))

    if remaining <= 0:
        state = SlaState.BREACHED.value
    elif elapsed_pct >= at_risk_percent:
        state = SlaState.AT_RISK.value
    else:
        state = SlaState.ON_TRACK.value

    return {
        "state": state,
        "deadline": deadline,
        "remaining_seconds": int(remaining),
        "remaining_label": humanize(remaining),
        "elapsed_percent": elapsed_pct,
        "is_breached": remaining <= 0,
    }


def humanize(seconds: float) -> str:
    if seconds <= 0:
        overdue = abs(int(seconds))
        return f"{_hm(overdue)} overdue"
    return f"{_hm(int(seconds))} remaining"


def _hm(seconds: int) -> str:
    days, rem = divmod(seconds, 86400)
    hours, rem = divmod(rem, 3600)
    minutes = rem // 60
    if days:
        return f"{days}d {hours}h"
    if hours:
        return f"{hours}h {minutes}m"
    return f"{minutes}m"


def case_sla_view(case: Case, now: Optional[dt.datetime] = None) -> Dict[str, object]:
    at_risk = case.sla_policy.at_risk_percent if case.sla_policy else None
    return evaluate(
        started_at=case.created_at,
        deadline=case.sla_deadline,
        resolved_at=case.sla_resolved_at,
        at_risk_percent=at_risk,
        now=now,
    )


def refresh_case_sla(db: Session, case: Case, now: Optional[dt.datetime] = None) -> Dict:
    """Recompute and persist a case's SLA state."""
    view = case_sla_view(case, now=now)
    if case.sla_state != view["state"]:
        case.sla_state = str(view["state"])
        db.add(case)
    return view


def stop_clock(case: Case, when: Optional[dt.datetime] = None) -> None:
    """Freeze the SLA clock once a human decision exists."""
    if case.sla_resolved_at is None:
        case.sla_resolved_at = when or utcnow()


def refresh_all(db: Session, limit: int = 1000) -> int:
    """Sweep open cases and update SLA state. Called on startup and on demand."""
    stmt = (
        select(Case)
        .where(Case.status.notin_(CLOSED_CASE_STATUSES))
        .where(Case.sla_resolved_at.is_(None))
        .limit(limit)
    )
    changed = 0
    for case in db.execute(stmt).scalars().all():
        before = case.sla_state
        refresh_case_sla(db, case)
        if case.sla_state != before:
            changed += 1
    if changed:
        db.commit()
    return changed
