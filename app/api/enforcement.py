"""Enforcement filing and tracking endpoints.

`POST /api/cases/{id}/file` is the only route to a submitted enforcement
action, and it delegates to `enforcement_service.submit_action`, which refuses
without a matching human approval.
"""
from __future__ import annotations

from typing import List, Optional

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.api.cases import load_case, serialize_action
from app.auth import CsrfProtected, get_current_user, scope_to_vendor
from app.constants import (
    ENFORCEMENT_ACTION_LABELS,
    ENFORCEMENT_PIPELINE,
    EnforcementStatus,
    Permission,
)
from app.database import get_db
from app.errors import Conflict, NotFound, PermissionDenied, ValidationFailed
from app.models import Case, EnforcementAction, User
from app.schemas.cases import EnforcementActionOut, TimelineStep
from app.schemas.common import Page
from app.services import enforcement as enforcement_service

router = APIRouter(prefix="/api", tags=["enforcement"])


@router.get("/enforcement", response_model=Page[EnforcementActionOut])
def list_enforcement(
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
    status: Optional[List[str]] = Query(None),
    action_type: Optional[str] = None,
    q: Optional[str] = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(25, ge=1, le=200),
):
    stmt = scope_to_vendor(select(EnforcementAction), EnforcementAction, user)
    if status:
        stmt = stmt.where(EnforcementAction.status.in_(status))
    if action_type:
        stmt = stmt.where(EnforcementAction.action_type == action_type)
    if q:
        like = f"%{q.strip()}%"
        stmt = stmt.where(or_(
            EnforcementAction.reference.ilike(like),
            EnforcementAction.submission_reference.ilike(like),
        ))
    total = db.execute(
        select(func.count()).select_from(
            stmt.with_only_columns(EnforcementAction.id).subquery()
        )
    ).scalar_one()
    rows = db.execute(
        stmt.order_by(EnforcementAction.created_at.desc())
        .offset((page - 1) * page_size).limit(page_size)
    ).scalars().all()
    return Page.build(
        [serialize_action(db, a) for a in rows], total, page, page_size
    )


@router.get("/cases/{case_id}/enforcement", response_model=List[EnforcementActionOut])
def case_enforcement(
    case_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    case = load_case(db, case_id, user)
    rows = db.execute(
        select(EnforcementAction)
        .where(EnforcementAction.case_id == case.id)
        .order_by(EnforcementAction.id.desc())
    ).scalars().all()
    return [serialize_action(db, a) for a in rows]


@router.get("/cases/{case_id}/enforcement/timeline", response_model=List[TimelineStep])
def case_timeline(
    case_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    case = load_case(db, case_id, user)
    return [
        TimelineStep(**step)
        for step in enforcement_service.enforcement_timeline(db, case)
    ]


@router.post("/cases/{case_id}/file", response_model=EnforcementActionOut)
def file_enforcement(
    case_id: int,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
    enforcement_action_id: Optional[int] = None,
    _: None = CsrfProtected,
):
    """Submit an approved enforcement action.

    Returns 409 HUMAN_APPROVAL_REQUIRED when no matching approval exists.
    """
    if not user.has_permission(Permission.ENFORCEMENT_SUBMIT.value):
        raise PermissionDenied(
            "Your role cannot submit enforcement filings. Ask an administrator."
        )
    case = load_case(db, case_id, user)

    if enforcement_action_id:
        action = db.get(EnforcementAction, enforcement_action_id)
        if action is None or action.case_id != case.id:
            raise NotFound("Enforcement action not found for this case.")
    else:
        action = enforcement_service.open_action_for_case(db, case.id)
    if action is None:
        raise Conflict("There is no enforcement action ready to file on this case.")

    action = enforcement_service.submit_action(
        db, case=case, action=action, user=user, request=request
    )
    db.commit()
    db.refresh(action)
    return serialize_action(db, action)


@router.post("/cases/{case_id}/enforcement/response", response_model=EnforcementActionOut)
def record_response(
    case_id: int,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
    result: Optional[str] = Query(None, description="Force a specific outcome (demo)"),
    _: None = CsrfProtected,
):
    """DEMO: advance a submitted filing to a marketplace outcome.

    In production this endpoint is replaced by a marketplace webhook.
    """
    if not user.has_permission(Permission.ENFORCEMENT_SUBMIT.value):
        raise PermissionDenied()
    case = load_case(db, case_id, user)
    action = db.execute(
        select(EnforcementAction)
        .where(
            EnforcementAction.case_id == case.id,
            EnforcementAction.status.in_([
                EnforcementStatus.SUBMITTED.value,
                EnforcementStatus.UNDER_REVIEW.value,
            ]),
        )
        .order_by(EnforcementAction.id.desc())
    ).scalars().first()
    if action is None:
        raise Conflict("No submitted filing is awaiting a marketplace response.")

    action = enforcement_service.simulate_marketplace_response(
        db, case=case, action=action, user=user, request=request, forced_result=result
    )
    db.commit()
    db.refresh(action)
    return serialize_action(db, action)


@router.get("/enforcement/pipeline")
def pipeline_definition(user: User = Depends(get_current_user)):
    """The Draft -> Approved -> Submitted -> Review -> Resolved definition."""
    return {
        "stages": ENFORCEMENT_PIPELINE,
        "statuses": EnforcementStatus.values(),
        "actions": [
            {"value": k, "label": v} for k, v in ENFORCEMENT_ACTION_LABELS.items()
        ],
    }
