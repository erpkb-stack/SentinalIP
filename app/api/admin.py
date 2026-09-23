"""Administration: SLA policies, autonomy configuration, roles, marketplaces."""
from __future__ import annotations

from typing import List, Optional

from fastapi import APIRouter, Depends, Request
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.auth import CsrfProtected, get_current_user, require_permission
from app.config import settings
from app.constants import (
    AUTONOMY_TIERS,
    LEGAL_ACTIONS,
    ROLE_LABELS,
    TIER3_AUTOMATABLE_ACTIONS,
    AuditAction,
    Permission,
)
from app.database import get_db
from app.errors import Conflict, NotFound, PermissionDenied, ValidationFailed
from app.models import Marketplace, Role, SlaPolicy, User, Vendor
from app.schemas.identity import RoleOut
from app.schemas.system import (
    AutonomyTierOut,
    MarketplaceOut,
    SlaPolicyCreate,
    SlaPolicyOut,
)
from app.services import audit

router = APIRouter(prefix="/api/admin", tags=["administration"])


# --------------------------------------------------------------------------- #
# Marketplaces (readable by anyone signed in - needed by the Create Case form)
# --------------------------------------------------------------------------- #
@router.get("/marketplaces", response_model=List[MarketplaceOut])
def list_marketplaces(
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
    include_inactive: bool = False,
):
    stmt = select(Marketplace)
    if not include_inactive:
        stmt = stmt.where(Marketplace.is_active.is_(True))
    rows = db.execute(stmt.order_by(Marketplace.name)).scalars().all()
    return [MarketplaceOut.model_validate(m) for m in rows]


# --------------------------------------------------------------------------- #
# Roles & permissions
# --------------------------------------------------------------------------- #
@router.get("/roles", response_model=List[RoleOut])
def list_roles(
    db: Session = Depends(get_db),
    user: User = Depends(require_permission(Permission.USER_VIEW)),
):
    rows = db.execute(select(Role).order_by(Role.id)).scalars().all()
    return [RoleOut.model_validate(r) for r in rows]


@router.get("/permissions")
def permission_matrix(
    db: Session = Depends(get_db),
    user: User = Depends(require_permission(Permission.USER_VIEW)),
):
    roles = db.execute(select(Role).order_by(Role.id)).scalars().all()
    all_perms = sorted({p for r in roles for p in (r.permissions or [])})
    return {
        "permissions": [
            {"value": p, "label": p.replace(":", " · ").replace("_", " ").title()}
            for p in all_perms
        ],
        "roles": [
            {
                "code": r.code,
                "label": ROLE_LABELS.get(r.code, r.name),
                "description": r.description,
                "requires_vendor": r.requires_vendor,
                "granted": r.permissions or [],
            }
            for r in roles
        ],
    }


# --------------------------------------------------------------------------- #
# SLA policies
# --------------------------------------------------------------------------- #
def serialize_policy(db: Session, p: SlaPolicy) -> SlaPolicyOut:
    vendor_name = None
    if p.vendor_id:
        v = db.get(Vendor, p.vendor_id)
        vendor_name = v.name if v else None
    return SlaPolicyOut(
        id=p.id, name=p.name, description=p.description,
        response_hours=p.response_hours, resolution_hours=p.resolution_hours,
        priority=p.priority, at_risk_percent=p.at_risk_percent,
        vendor_id=p.vendor_id, vendor_name=vendor_name,
        is_default=p.is_default, is_active=p.is_active, created_at=p.created_at,
    )


@router.get("/sla-policies", response_model=List[SlaPolicyOut])
def list_policies(
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    stmt = select(SlaPolicy)
    if not user.is_global:
        stmt = stmt.where(
            (SlaPolicy.vendor_id == user.vendor_id) | (SlaPolicy.vendor_id.is_(None))
        )
    rows = db.execute(
        stmt.order_by(SlaPolicy.vendor_id.isnot(None), SlaPolicy.name)
    ).scalars().all()
    return [serialize_policy(db, p) for p in rows]


@router.post("/sla-policies", response_model=SlaPolicyOut, status_code=201)
def create_policy(
    payload: SlaPolicyCreate,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_permission(Permission.CONFIG_MANAGE)),
    _: None = CsrfProtected,
):
    if payload.vendor_id and db.get(Vendor, payload.vendor_id) is None:
        raise ValidationFailed("The selected vendor does not exist.")

    if payload.is_default:
        existing = db.execute(
            select(SlaPolicy).where(
                SlaPolicy.vendor_id.is_(payload.vendor_id)
                if payload.vendor_id is None
                else SlaPolicy.vendor_id == payload.vendor_id,
                SlaPolicy.is_default.is_(True),
            )
        ).scalars().all()
        for p in existing:
            p.is_default = False
            db.add(p)

    policy = SlaPolicy(**payload.model_dump(), created_by_id=user.id)
    db.add(policy)
    db.flush()
    audit.record(
        db, action=AuditAction.CONFIGURATION_CHANGED.value, user=user, request=request,
        object_type="sla_policy", object_id=policy.id, object_label=policy.name,
        vendor_id=policy.vendor_id,
        new_value=payload.model_dump(),
        detail="SLA policy created",
    )
    db.commit()
    db.refresh(policy)
    return serialize_policy(db, policy)


@router.put("/sla-policies/{policy_id}", response_model=SlaPolicyOut)
def update_policy(
    policy_id: int,
    payload: SlaPolicyCreate,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_permission(Permission.CONFIG_MANAGE)),
    _: None = CsrfProtected,
):
    policy = db.get(SlaPolicy, policy_id)
    if policy is None:
        raise NotFound("SLA policy not found.")

    before = {
        "response_hours": policy.response_hours,
        "resolution_hours": policy.resolution_hours,
        "at_risk_percent": policy.at_risk_percent,
        "is_default": policy.is_default,
        "is_active": policy.is_active,
    }
    for field, value in payload.model_dump().items():
        setattr(policy, field, value)
    db.add(policy)
    db.flush()

    after = {
        "response_hours": policy.response_hours,
        "resolution_hours": policy.resolution_hours,
        "at_risk_percent": policy.at_risk_percent,
        "is_default": policy.is_default,
        "is_active": policy.is_active,
    }
    prev, new = audit.diff(before, after)
    audit.record(
        db, action=AuditAction.CONFIGURATION_CHANGED.value, user=user, request=request,
        object_type="sla_policy", object_id=policy.id, object_label=policy.name,
        vendor_id=policy.vendor_id, previous_value=prev, new_value=new,
        detail="SLA policy updated",
    )
    db.commit()
    db.refresh(policy)
    return serialize_policy(db, policy)


# --------------------------------------------------------------------------- #
# AI autonomy
# --------------------------------------------------------------------------- #
@router.get("/autonomy", response_model=List[AutonomyTierOut])
def autonomy_tiers(
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    counts = dict(
        db.execute(
            select(Vendor.autonomy_tier, func.count(Vendor.id))
            .group_by(Vendor.autonomy_tier)
        ).all()
    )
    return [
        AutonomyTierOut(
            tier=tier,
            **meta,
            is_available=tier <= settings.max_autonomy_tier,
            vendor_count=int(counts.get(tier, 0)),
        )
        for tier, meta in sorted(AUTONOMY_TIERS.items())
    ]


@router.get("/autonomy/policy")
def autonomy_policy(user: User = Depends(get_current_user)):
    """What each tier may and may not do. Shown on the AI Autonomy screen."""
    return {
        "default_tier": settings.default_autonomy_tier,
        "max_enabled_tier": settings.max_autonomy_tier,
        "tiers": [
            {"tier": t, **meta} for t, meta in sorted(AUTONOMY_TIERS.items())
        ],
        "always_requires_human_approval": sorted(LEGAL_ACTIONS),
        "tier3_automatable": sorted(TIER3_AUTOMATABLE_ACTIONS),
        "statement": (
            "Tenant isolation and human approval are security boundaries, not UI "
            "features. No autonomy tier removes the human approval requirement for "
            "a legal or enforcement action."
        ),
    }


@router.put("/autonomy/vendors/{vendor_id}")
def set_vendor_autonomy(
    vendor_id: int,
    tier: int,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_permission(Permission.CONFIG_MANAGE)),
    _: None = CsrfProtected,
):
    if tier not in AUTONOMY_TIERS:
        raise ValidationFailed(f"tier must be one of {sorted(AUTONOMY_TIERS)}")
    if tier > settings.max_autonomy_tier:
        raise ValidationFailed(
            f"Tier {tier} is not enabled on this deployment. Tier 4 is reserved and "
            "requires explicit administrative configuration."
        )
    if tier >= 3 and not user.is_super_admin:
        raise PermissionDenied("Only a Super Admin can set Tier 3 or above.")

    vendor = db.get(Vendor, vendor_id)
    if vendor is None:
        raise NotFound("Vendor not found.")

    previous = vendor.autonomy_tier
    if previous == tier:
        raise Conflict(f"{vendor.name} is already at {AUTONOMY_TIERS[tier]['label']}.")
    vendor.autonomy_tier = tier
    db.add(vendor)

    audit.record(
        db, action=AuditAction.CONFIGURATION_CHANGED.value, user=user, request=request,
        object_type="vendor", object_id=vendor.id, object_label=vendor.name,
        vendor_id=vendor.id,
        previous_value={"autonomy_tier": previous},
        new_value={"autonomy_tier": tier, "label": AUTONOMY_TIERS[tier]["label"]},
        detail="Autonomy tier changed",
    )
    db.commit()
    return {
        "success": True,
        "vendor": vendor.name,
        "autonomy_tier": tier,
        "label": AUTONOMY_TIERS[tier]["label"],
        "message": f"{vendor.name} set to {AUTONOMY_TIERS[tier]['label']}.",
    }
