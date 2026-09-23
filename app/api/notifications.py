"""In-app notifications for the header bell."""
from __future__ import annotations

from typing import List

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.auth import CsrfProtected, get_current_user
from app.database import get_db, utcnow
from app.errors import NotFound
from app.models import Notification, User
from app.schemas.system import NotificationOut

router = APIRouter(prefix="/api/notifications", tags=["notifications"])


@router.get("", response_model=List[NotificationOut])
def list_notifications(
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
    unread_only: bool = False,
    limit: int = Query(30, ge=1, le=100),
):
    stmt = select(Notification).where(Notification.user_id == user.id)
    if unread_only:
        stmt = stmt.where(Notification.is_read.is_(False))
    rows = db.execute(
        stmt.order_by(Notification.created_at.desc()).limit(limit)
    ).scalars().all()
    return [NotificationOut.model_validate(n) for n in rows]


@router.get("/count")
def unread_count(
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    total = db.execute(
        select(func.count(Notification.id)).where(
            Notification.user_id == user.id, Notification.is_read.is_(False)
        )
    ).scalar_one()
    return {"unread": int(total)}


@router.post("/{notification_id}/read")
def mark_read(
    notification_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
    _: None = CsrfProtected,
):
    n = db.execute(
        select(Notification).where(
            Notification.id == notification_id, Notification.user_id == user.id
        )
    ).scalars().first()
    if n is None:
        raise NotFound("Notification not found.")
    n.is_read = True
    n.read_at = utcnow()
    db.add(n)
    db.commit()
    return {"success": True}


@router.post("/read-all")
def mark_all_read(
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
    _: None = CsrfProtected,
):
    rows = db.execute(
        select(Notification).where(
            Notification.user_id == user.id, Notification.is_read.is_(False)
        )
    ).scalars().all()
    now = utcnow()
    for n in rows:
        n.is_read = True
        n.read_at = now
        db.add(n)
    db.commit()
    return {"success": True, "marked": len(rows)}
