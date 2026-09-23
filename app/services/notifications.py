"""In-app notifications."""
from __future__ import annotations

from typing import Iterable, List, Optional, Sequence

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.constants import (
    NotificationSeverity,
    NotificationType,
    RoleCode,
    UserStatus,
)
from app.models import Case, Notification, User


def _recipients_for_vendor(
    db: Session, vendor_id: int, include_global_admins: bool = True
) -> List[User]:
    stmt = select(User).where(
        User.status == UserStatus.ACTIVE.value, User.vendor_id == vendor_id
    )
    users = list(db.execute(stmt).scalars().all())
    if include_global_admins:
        stmt = (
            select(User)
            .join(User.role)
            .where(User.status == UserStatus.ACTIVE.value)
            .where(User.vendor_id.is_(None))
        )
        users += [u for u in db.execute(stmt).scalars().all() if u.is_global]
    # de-duplicate while preserving order
    seen, unique = set(), []
    for u in users:
        if u.id not in seen:
            seen.add(u.id)
            unique.append(u)
    return unique


def notify(
    db: Session,
    *,
    users: Sequence[User],
    notification_type: str,
    title: str,
    message: str = "",
    severity: str = NotificationSeverity.INFO.value,
    case: Optional[Case] = None,
    link: Optional[str] = None,
    commit: bool = False,
) -> List[Notification]:
    created = []
    for user in users:
        n = Notification(
            user_id=user.id,
            vendor_id=case.vendor_id if case else user.vendor_id,
            case_id=case.id if case else None,
            notification_type=notification_type,
            severity=severity,
            title=title[:255],
            message=message,
            link=link or (f"/cases/{case.id}" if case else None),
        )
        db.add(n)
        created.append(n)
    db.flush()
    if commit:
        db.commit()
    return created


# --------------------------------------------------------------------------- #
# Convenience emitters
# --------------------------------------------------------------------------- #
def case_created(db: Session, case: Case) -> None:
    notify(
        db,
        users=_recipients_for_vendor(db, case.vendor_id),
        notification_type=NotificationType.NEW_CASE.value,
        title=f"New case {case.case_number}",
        message=case.title,
        severity=NotificationSeverity.INFO.value,
        case=case,
    )


def analysis_complete(db: Session, case: Case) -> None:
    notify(
        db,
        users=_recipients_for_vendor(db, case.vendor_id),
        notification_type=NotificationType.AI_ANALYSIS_COMPLETE.value,
        title=f"AI analysis complete - {case.case_number}",
        message=(
            f"Confidence {int(case.ai_confidence or 0)}% "
            f"({(case.risk_level or 'LOW').title()})."
        ),
        severity=NotificationSeverity.SUCCESS.value,
        case=case,
    )


def approval_required(db: Session, case: Case, action_label: str) -> None:
    notify(
        db,
        users=_recipients_for_vendor(db, case.vendor_id),
        notification_type=NotificationType.APPROVAL_REQUIRED.value,
        title=f"Approval required - {case.case_number}",
        message=f"AI recommends: {action_label}. A human decision is required.",
        severity=NotificationSeverity.WARNING.value,
        case=case,
    )


def sla_approaching(db: Session, case: Case, remaining_label: str) -> None:
    notify(
        db,
        users=_recipients_for_vendor(db, case.vendor_id),
        notification_type=NotificationType.SLA_APPROACHING.value,
        title=f"SLA at risk - {case.case_number}",
        message=f"{remaining_label} until the SLA deadline.",
        severity=NotificationSeverity.WARNING.value,
        case=case,
    )


def sla_breached(db: Session, case: Case) -> None:
    notify(
        db,
        users=_recipients_for_vendor(db, case.vendor_id),
        notification_type=NotificationType.SLA_BREACHED.value,
        title=f"SLA breached - {case.case_number}",
        message="This case has passed its SLA deadline.",
        severity=NotificationSeverity.ERROR.value,
        case=case,
    )


def enforcement_response(db: Session, case: Case, response: str) -> None:
    notify(
        db,
        users=_recipients_for_vendor(db, case.vendor_id),
        notification_type=NotificationType.ENFORCEMENT_RESPONSE.value,
        title=f"Marketplace response - {case.case_number}",
        message=response,
        severity=NotificationSeverity.INFO.value,
        case=case,
    )


def case_escalated(db: Session, case: Case, note: str = "") -> None:
    notify(
        db,
        users=_recipients_for_vendor(db, case.vendor_id),
        notification_type=NotificationType.CASE_ESCALATED.value,
        title=f"Case escalated - {case.case_number}",
        message=note or "This case has been escalated.",
        severity=NotificationSeverity.ERROR.value,
        case=case,
    )
