"""Human-readable reference generators (CASE-2026-000123, EV-..., ENF-...)."""
from __future__ import annotations

import datetime as dt
import secrets

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import Case, EnforcementAction, Evidence


def next_case_number(db: Session, when: dt.datetime | None = None) -> str:
    when = when or dt.datetime.utcnow()
    year = when.year
    prefix = f"CASE-{year}-"
    count = db.execute(
        select(func.count(Case.id)).where(Case.case_number.like(f"{prefix}%"))
    ).scalar_one()
    # Collision-safe: walk forward if a number is somehow taken.
    seq = count + 1
    for _ in range(50):
        candidate = f"{prefix}{seq:06d}"
        exists = db.execute(
            select(Case.id).where(Case.case_number == candidate)
        ).first()
        if not exists:
            return candidate
        seq += 1
    return f"{prefix}{secrets.randbelow(900000) + 100000:06d}"


def next_evidence_ref(db: Session, case_id: int) -> str:
    count = db.execute(
        select(func.count(Evidence.id)).where(Evidence.case_id == case_id)
    ).scalar_one()
    seq = count + 1
    for _ in range(50):
        candidate = f"EV-{case_id:05d}-{seq:03d}"
        if not db.execute(select(Evidence.id).where(Evidence.evidence_ref == candidate)).first():
            return candidate
        seq += 1
    return f"EV-{case_id:05d}-{secrets.token_hex(3)}"


def next_enforcement_ref(db: Session, case_id: int) -> str:
    count = db.execute(
        select(func.count(EnforcementAction.id)).where(
            EnforcementAction.case_id == case_id
        )
    ).scalar_one()
    seq = count + 1
    for _ in range(50):
        candidate = f"ENF-{case_id:05d}-{seq:02d}"
        if not db.execute(
            select(EnforcementAction.id).where(EnforcementAction.reference == candidate)
        ).first():
            return candidate
        seq += 1
    return f"ENF-{case_id:05d}-{secrets.token_hex(2)}"
