"""Audit trail (read-only).

There is no create/update/delete endpoint for audit records anywhere in the
application - rows are written only by `app.services.audit.record`.
"""
from __future__ import annotations

import datetime as dt
from typing import List, Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.auth import get_current_user
from app.constants import AuditAction, Permission
from app.database import get_db
from app.errors import PermissionDenied, VendorAccessDenied
from app.models import AuditLog, User
from app.schemas.common import Page
from app.schemas.system import AuditLogOut

router = APIRouter(prefix="/api/audit", tags=["audit"])


@router.get("", response_model=Page[AuditLogOut])
def list_audit(
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
    q: Optional[str] = None,
    action: Optional[List[str]] = Query(None),
    object_type: Optional[str] = None,
    object_id: Optional[str] = None,
    vendor_id: Optional[int] = None,
    user_id: Optional[int] = None,
    date_from: Optional[dt.date] = None,
    date_to: Optional[dt.date] = None,
    success: Optional[bool] = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
):
    if not user.has_permission(Permission.AUDIT_VIEW.value):
        raise PermissionDenied()

    stmt = select(AuditLog)

    # Tenant boundary: a vendor user sees only their own tenant's trail.
    if not user.is_global:
        stmt = stmt.where(AuditLog.vendor_id == user.vendor_id)
    elif vendor_id is not None:
        stmt = stmt.where(AuditLog.vendor_id == vendor_id)

    if vendor_id is not None and not user.is_global and vendor_id != user.vendor_id:
        raise VendorAccessDenied()

    if action:
        stmt = stmt.where(AuditLog.action.in_(action))
    if object_type:
        stmt = stmt.where(AuditLog.object_type == object_type)
    if object_id:
        stmt = stmt.where(AuditLog.object_id == str(object_id))
    if user_id is not None:
        stmt = stmt.where(AuditLog.user_id == user_id)
    if success is not None:
        stmt = stmt.where(AuditLog.success.is_(success))
    if date_from:
        stmt = stmt.where(AuditLog.timestamp >= dt.datetime.combine(date_from, dt.time.min))
    if date_to:
        stmt = stmt.where(AuditLog.timestamp <= dt.datetime.combine(date_to, dt.time.max))
    if q:
        like = f"%{q.strip()}%"
        stmt = stmt.where(or_(
            AuditLog.user_email.ilike(like),
            AuditLog.object_label.ilike(like),
            AuditLog.detail.ilike(like),
            AuditLog.correlation_id.ilike(like),
        ))

    total = db.execute(
        select(func.count()).select_from(stmt.with_only_columns(AuditLog.id).subquery())
    ).scalar_one()
    rows = db.execute(
        stmt.order_by(AuditLog.timestamp.desc(), AuditLog.id.desc())
        .offset((page - 1) * page_size).limit(page_size)
    ).scalars().all()
    return Page.build(
        [AuditLogOut.model_validate(r) for r in rows], total, page, page_size
    )


@router.get("/actions")
def audit_actions(user: User = Depends(get_current_user)):
    if not user.has_permission(Permission.AUDIT_VIEW.value):
        raise PermissionDenied()
    return {
        "actions": [
            {"value": a, "label": a.replace("_", " ").title()}
            for a in AuditAction.values()
        ]
    }
