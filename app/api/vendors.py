"""Vendor (tenant) management."""
from __future__ import annotations

from typing import List, Optional

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.auth import CsrfProtected, require_permission
from app.config import settings
from app.constants import (
    AUTONOMY_TIERS,
    OPEN_CASE_STATUSES,
    AuditAction,
    Permission,
    RoleCode,
    UserStatus,
    VendorStatus,
)
from app.database import get_db
from app.errors import Conflict, NotFound, PermissionDenied, ValidationFailed
from app.models import Case, SlaPolicy, User, Vendor
from app.schemas.common import Page
from app.schemas.identity import VendorCreate, VendorOption, VendorOut, VendorUpdate
from app.services import audit

router = APIRouter(prefix="/api/vendors", tags=["vendors"])


def _counts(db: Session, vendor_ids: List[int]) -> tuple[dict, dict]:
    if not vendor_ids:
        return {}, {}
    users = dict(
        db.execute(
            select(User.vendor_id, func.count(User.id))
            .where(User.vendor_id.in_(vendor_ids))
            .group_by(User.vendor_id)
        ).all()
    )
    cases = dict(
        db.execute(
            select(Case.vendor_id, func.count(Case.id))
            .where(Case.vendor_id.in_(vendor_ids), Case.status.in_(OPEN_CASE_STATUSES))
            .group_by(Case.vendor_id)
        ).all()
    )
    return users, cases


def serialize(vendor: Vendor, user_count: int = 0, open_cases: int = 0) -> VendorOut:
    return VendorOut(
        id=vendor.id,
        name=vendor.name,
        legal_name=vendor.legal_name,
        contact_name=vendor.contact_name,
        contact_email=vendor.contact_email,
        phone=vendor.phone,
        country=vendor.country,
        website=vendor.website,
        industry=vendor.industry,
        sla_policy_id=vendor.sla_policy_id,
        sla_policy_name=vendor.sla_policy.name if vendor.sla_policy else None,
        autonomy_tier=vendor.autonomy_tier,
        autonomy_label=AUTONOMY_TIERS.get(vendor.autonomy_tier, {}).get("label"),
        status=vendor.status,
        user_count=user_count,
        open_case_count=open_cases,
        created_at=vendor.created_at,
    )


@router.get("", response_model=Page[VendorOut])
def list_vendors(
    db: Session = Depends(get_db),
    user: User = Depends(require_permission(Permission.VENDOR_VIEW)),
    q: Optional[str] = None,
    status: Optional[str] = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(25, ge=1, le=200),
):
    stmt = select(Vendor)
    # A vendor-scoped Admin sees only their own organization.
    if not user.is_global:
        stmt = stmt.where(Vendor.id == user.vendor_id)
    if q:
        like = f"%{q.strip()}%"
        stmt = stmt.where(
            or_(Vendor.name.ilike(like), Vendor.legal_name.ilike(like),
                Vendor.contact_email.ilike(like))
        )
    if status:
        stmt = stmt.where(Vendor.status == status)

    total = db.execute(
        select(func.count()).select_from(stmt.subquery())
    ).scalar_one()

    rows = db.execute(
        stmt.order_by(Vendor.name).offset((page - 1) * page_size).limit(page_size)
    ).scalars().all()

    user_counts, case_counts = _counts(db, [v.id for v in rows])
    items = [
        serialize(v, user_counts.get(v.id, 0), case_counts.get(v.id, 0)) for v in rows
    ]
    return Page.build(items, total, page, page_size)


@router.get("/options", response_model=List[VendorOption])
def vendor_options(
    db: Session = Depends(get_db),
    user: User = Depends(require_permission(Permission.VENDOR_VIEW)),
    include_inactive: bool = False,
):
    """Feeds the Vendor dropdown on the Create User / Create Case forms."""
    stmt = select(Vendor)
    if not user.is_global:
        stmt = stmt.where(Vendor.id == user.vendor_id)
    if not include_inactive:
        stmt = stmt.where(Vendor.status == VendorStatus.ACTIVE.value)
    rows = db.execute(stmt.order_by(Vendor.name)).scalars().all()
    return [
        VendorOption(id=v.id, name=v.name, status=v.status,
                     autonomy_tier=v.autonomy_tier)
        for v in rows
    ]


@router.post("", response_model=VendorOut, status_code=201)
def create_vendor(
    payload: VendorCreate,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_permission(Permission.VENDOR_CREATE)),
    _: None = CsrfProtected,
):
    if not user.is_global:
        raise PermissionDenied(
            "Only a platform administrator can create vendors."
        )
    exists = db.execute(
        select(Vendor).where(func.lower(Vendor.name) == payload.name.strip().lower())
    ).scalars().first()
    if exists:
        raise Conflict("A vendor with that name already exists.")

    tier = payload.autonomy_tier
    if tier > settings.max_autonomy_tier:
        raise ValidationFailed(
            f"Autonomy Tier {tier} is not enabled on this deployment "
            f"(maximum {settings.max_autonomy_tier}). Tier 4 is reserved."
        )
    if tier >= 3 and not user.is_super_admin:
        raise PermissionDenied("Only a Super Admin can set Tier 3 or above.")

    if payload.sla_policy_id and not db.get(SlaPolicy, payload.sla_policy_id):
        raise ValidationFailed("The selected SLA policy does not exist.")

    vendor = Vendor(
        name=payload.name.strip(),
        legal_name=payload.legal_name,
        contact_name=payload.contact_name,
        contact_email=payload.contact_email,
        phone=payload.phone,
        country=payload.country,
        website=payload.website,
        industry=payload.industry,
        sla_policy_id=payload.sla_policy_id,
        autonomy_tier=tier,
        status=payload.status,
        notes=payload.notes,
        created_by_id=user.id,
    )
    db.add(vendor)
    db.flush()

    audit.record(
        db, action=AuditAction.VENDOR_CREATED.value, user=user, request=request,
        object_type="vendor", object_id=vendor.id, object_label=vendor.name,
        vendor_id=vendor.id,
        new_value={"name": vendor.name, "status": vendor.status,
                   "autonomy_tier": vendor.autonomy_tier,
                   "contact_email": vendor.contact_email},
    )
    db.commit()
    db.refresh(vendor)
    return serialize(vendor)


@router.get("/{vendor_id}", response_model=VendorOut)
def get_vendor(
    vendor_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(require_permission(Permission.VENDOR_VIEW)),
):
    vendor = db.get(Vendor, vendor_id)
    if vendor is None:
        raise NotFound("Vendor not found.")
    if not user.is_global and vendor.id != user.vendor_id:
        raise NotFound("Vendor not found.")
    users, cases = _counts(db, [vendor.id])
    return serialize(vendor, users.get(vendor.id, 0), cases.get(vendor.id, 0))


@router.put("/{vendor_id}", response_model=VendorOut)
def update_vendor(
    vendor_id: int,
    payload: VendorUpdate,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_permission(Permission.VENDOR_UPDATE)),
    _: None = CsrfProtected,
):
    vendor = db.get(Vendor, vendor_id)
    if vendor is None:
        raise NotFound("Vendor not found.")
    if not user.is_global and vendor.id != user.vendor_id:
        raise NotFound("Vendor not found.")

    before = {
        "name": vendor.name, "status": vendor.status,
        "autonomy_tier": vendor.autonomy_tier,
        "sla_policy_id": vendor.sla_policy_id,
        "contact_email": vendor.contact_email,
    }
    data = payload.model_dump(exclude_unset=True)

    if "autonomy_tier" in data and data["autonomy_tier"] is not None:
        tier = data["autonomy_tier"]
        if tier > settings.max_autonomy_tier:
            raise ValidationFailed(
                f"Autonomy Tier {tier} is not enabled on this deployment. "
                "Tier 4 is reserved and requires explicit administrative configuration."
            )
        if tier >= 3 and not user.is_super_admin:
            raise PermissionDenied("Only a Super Admin can set Tier 3 or above.")

    if data.get("name"):
        clash = db.execute(
            select(Vendor).where(
                func.lower(Vendor.name) == data["name"].strip().lower(),
                Vendor.id != vendor.id,
            )
        ).scalars().first()
        if clash:
            raise Conflict("A vendor with that name already exists.")

    for field, value in data.items():
        setattr(vendor, field, value)
    db.add(vendor)
    db.flush()

    after = {
        "name": vendor.name, "status": vendor.status,
        "autonomy_tier": vendor.autonomy_tier,
        "sla_policy_id": vendor.sla_policy_id,
        "contact_email": vendor.contact_email,
    }
    prev, new = audit.diff(before, after)
    action = (
        AuditAction.VENDOR_DISABLED.value
        if after["status"] != before["status"] and after["status"] != VendorStatus.ACTIVE.value
        else AuditAction.VENDOR_UPDATED.value
    )
    audit.record(
        db, action=action, user=user, request=request,
        object_type="vendor", object_id=vendor.id, object_label=vendor.name,
        vendor_id=vendor.id, previous_value=prev, new_value=new,
    )
    db.commit()
    db.refresh(vendor)
    users, cases = _counts(db, [vendor.id])
    return serialize(vendor, users.get(vendor.id, 0), cases.get(vendor.id, 0))


@router.delete("/{vendor_id}")
def disable_vendor(
    vendor_id: int,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_permission(Permission.VENDOR_DISABLE)),
    _: None = CsrfProtected,
):
    """Vendors are disabled, never destroyed - their cases and audit trail must survive."""
    if not user.is_global:
        raise PermissionDenied("Only a platform administrator can disable a vendor.")
    vendor = db.get(Vendor, vendor_id)
    if vendor is None:
        raise NotFound("Vendor not found.")
    if vendor.status == VendorStatus.INACTIVE.value:
        raise Conflict("This vendor is already inactive.")

    previous = vendor.status
    vendor.status = VendorStatus.INACTIVE.value
    db.add(vendor)

    affected = db.execute(
        select(func.count(User.id)).where(
            User.vendor_id == vendor.id, User.status == UserStatus.ACTIVE.value
        )
    ).scalar_one()

    audit.record(
        db, action=AuditAction.VENDOR_DISABLED.value, user=user, request=request,
        object_type="vendor", object_id=vendor.id, object_label=vendor.name,
        vendor_id=vendor.id,
        previous_value={"status": previous},
        new_value={"status": vendor.status},
        detail=f"{affected} active user(s) lost access.",
    )
    db.commit()
    return {
        "success": True,
        "message": f"{vendor.name} disabled. {affected} user(s) can no longer sign in.",
    }
