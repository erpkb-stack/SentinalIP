"""User management (Super Admin / Admin only)."""
from __future__ import annotations

from typing import List, Optional

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.auth import CsrfProtected, require_permission
from app.auth.security import (
    generate_temporary_password,
    hash_password,
    validate_password_strength,
)
from app.constants import (
    GLOBAL_ROLES,
    ROLE_LABELS,
    VENDOR_SCOPED_ROLES,
    AuditAction,
    Permission,
    RoleCode,
    UserStatus,
    VendorStatus,
)
from app.database import get_db, utcnow
from app.errors import Conflict, NotFound, PermissionDenied, ValidationFailed
from app.models import Role, User, UserVendorAssignment, Vendor
from app.schemas.common import Page
from app.schemas.identity import (
    RoleOut,
    UserCreate,
    UserCreatedResponse,
    UserOut,
    UserUpdate,
)
from app.services import audit

router = APIRouter(prefix="/api/users", tags=["users"])


def serialize(user: User) -> UserOut:
    return UserOut(
        id=user.id,
        first_name=user.first_name,
        last_name=user.last_name,
        full_name=user.full_name,
        email=user.email,
        role_code=user.role_code,
        role_label=ROLE_LABELS.get(user.role_code, user.role_code),
        vendor_id=user.vendor_id,
        vendor_name=user.vendor.name if user.vendor else None,
        status=user.status,
        title=user.title,
        phone=user.phone,
        last_login_at=user.last_login_at,
        must_change_password=user.must_change_password,
        created_at=user.created_at,
    )


def _assert_may_manage_role(actor: User, role_code: str) -> None:
    """Privilege-escalation guard: nobody may mint a role above their own."""
    if role_code == RoleCode.SUPER_ADMIN.value and not actor.is_super_admin:
        raise PermissionDenied("Only a Super Admin can create or modify a Super Admin.")
    if role_code == RoleCode.ADMIN.value and not actor.is_super_admin:
        raise PermissionDenied("Only a Super Admin can create or modify an Admin.")


def _validate_vendor_pairing(db, role_code: str, vendor_id: Optional[int]) -> Optional[Vendor]:
    """Enforce the role <-> vendor invariant at the API layer too.

    The schema already checks it; this guards the path where a schema is
    bypassed (internal calls, future endpoints) and resolves the vendor row.
    """
    if role_code in GLOBAL_ROLES and vendor_id is not None:
        raise ValidationFailed(
            f"{ROLE_LABELS.get(role_code, role_code)} is a platform-wide role and "
            "must not be tied to a vendor."
        )
    if role_code in VENDOR_SCOPED_ROLES and vendor_id is None:
        raise ValidationFailed(
            "A Vendor User must be assigned to a vendor.",
            details={"fields": [{"field": "vendor_id",
                                 "message": "Select a vendor."}]},
        )
    if vendor_id is None:
        return None

    vendor = db.get(Vendor, vendor_id)
    if vendor is None:
        raise ValidationFailed("The selected vendor does not exist.")
    if vendor.status != VendorStatus.ACTIVE.value:
        raise ValidationFailed(
            f"{vendor.name} is {vendor.status.lower()}; users cannot be assigned to it."
        )
    return vendor


def _assert_tenant_scope(actor: User, vendor_id: Optional[int]) -> None:
    """A vendor-scoped Admin may only manage accounts inside their own vendor."""
    if actor.is_global:
        return
    if vendor_id is None or vendor_id != actor.vendor_id:
        raise PermissionDenied(
            "You can only manage users inside your own organization."
        )


@router.get("/roles", response_model=List[RoleOut])
def list_roles(
    db: Session = Depends(get_db),
    user: User = Depends(require_permission(Permission.USER_VIEW)),
):
    roles = db.execute(select(Role).order_by(Role.id)).scalars().all()
    # An Admin cannot create roles above their own, so don't offer them.
    if not user.is_super_admin:
        roles = [r for r in roles if r.code == RoleCode.VENDOR_USER.value]
    return [RoleOut.model_validate(r) for r in roles]


@router.get("", response_model=Page[UserOut])
def list_users(
    db: Session = Depends(get_db),
    user: User = Depends(require_permission(Permission.USER_VIEW)),
    q: Optional[str] = None,
    role_code: Optional[str] = None,
    vendor_id: Optional[int] = None,
    status: Optional[str] = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(25, ge=1, le=200),
):
    stmt = select(User).join(User.role)
    # A vendor-scoped Admin sees only their own organization's accounts.
    if not user.is_global:
        stmt = stmt.where(User.vendor_id == user.vendor_id)
    if q:
        like = f"%{q.strip()}%"
        stmt = stmt.where(
            or_(User.email.ilike(like), User.first_name.ilike(like),
                User.last_name.ilike(like))
        )
    if role_code:
        stmt = stmt.where(Role.code == role_code)
    if vendor_id is not None:
        stmt = stmt.where(User.vendor_id == vendor_id)
    if status:
        stmt = stmt.where(User.status == status)

    total = db.execute(select(func.count()).select_from(stmt.subquery())).scalar_one()
    rows = db.execute(
        stmt.order_by(User.last_name, User.first_name)
        .offset((page - 1) * page_size).limit(page_size)
    ).scalars().all()
    return Page.build([serialize(u) for u in rows], total, page, page_size)


@router.post("", response_model=UserCreatedResponse, status_code=201)
def create_user(
    payload: UserCreate,
    request: Request,
    db: Session = Depends(get_db),
    actor: User = Depends(require_permission(Permission.USER_CREATE)),
    _: None = CsrfProtected,
):
    _assert_may_manage_role(actor, payload.role_code)
    vendor = _validate_vendor_pairing(db, payload.role_code, payload.vendor_id)
    _assert_tenant_scope(actor, vendor.id if vendor else None)

    email = payload.email.lower().strip()
    if db.execute(select(User).where(User.email == email)).scalars().first():
        raise Conflict("An account with that email address already exists.")

    role = db.execute(
        select(Role).where(Role.code == payload.role_code)
    ).scalars().first()
    if role is None:
        raise ValidationFailed("The selected role does not exist.")

    generated: Optional[str] = None
    if payload.password:
        validate_password_strength(payload.password)
        raw_password = payload.password
    else:
        raw_password = generate_temporary_password()
        generated = raw_password

    user = User(
        email=email,
        password_hash=hash_password(raw_password),
        first_name=payload.first_name.strip(),
        last_name=payload.last_name.strip(),
        title=payload.title,
        phone=payload.phone,
        role_id=role.id,
        vendor_id=vendor.id if vendor else None,
        status=payload.status,
        must_change_password=payload.must_change_password,
        created_by_id=actor.id,
    )
    db.add(user)
    db.flush()

    if vendor:
        db.add(UserVendorAssignment(
            user_id=user.id, vendor_id=vendor.id, is_primary=True,
            assigned_by_id=actor.id,
        ))

    audit.record(
        db, action=AuditAction.USER_CREATED.value, user=actor, request=request,
        object_type="user", object_id=user.id, object_label=user.email,
        vendor_id=user.vendor_id,
        new_value={
            "email": user.email, "role": payload.role_code,
            "vendor_id": user.vendor_id,
            "vendor_name": vendor.name if vendor else None,
            "status": user.status, "password": "[REDACTED]",
        },
    )
    if vendor:
        audit.record(
            db, action=AuditAction.VENDOR_ASSIGNMENT_CHANGED.value,
            user=actor, request=request,
            object_type="user", object_id=user.id, object_label=user.email,
            vendor_id=vendor.id,
            previous_value={"vendor_id": None},
            new_value={"vendor_id": vendor.id, "vendor_name": vendor.name},
        )
    db.commit()
    db.refresh(user)

    return UserCreatedResponse(
        user=serialize(user),
        temporary_password=generated,
        message=(
            "User created. Share the temporary password over a secure channel - "
            "it is shown only once."
            if generated else "User created."
        ),
    )


@router.get("/{user_id}", response_model=UserOut)
def get_user(
    user_id: int,
    db: Session = Depends(get_db),
    actor: User = Depends(require_permission(Permission.USER_VIEW)),
):
    user = db.get(User, user_id)
    if user is None:
        raise NotFound("User not found.")
    if not actor.is_global and user.vendor_id != actor.vendor_id:
        raise NotFound("User not found.")
    return serialize(user)


@router.put("/{user_id}", response_model=UserOut)
def update_user(
    user_id: int,
    payload: UserUpdate,
    request: Request,
    db: Session = Depends(get_db),
    actor: User = Depends(require_permission(Permission.USER_UPDATE)),
    _: None = CsrfProtected,
):
    user = db.get(User, user_id)
    if user is None:
        raise NotFound("User not found.")
    if not actor.is_global and user.vendor_id != actor.vendor_id:
        raise NotFound("User not found.")

    _assert_may_manage_role(actor, user.role_code)   # may I touch this account at all?
    data = payload.model_dump(exclude_unset=True)

    before = {
        "email": user.email, "role": user.role_code, "vendor_id": user.vendor_id,
        "status": user.status, "first_name": user.first_name,
        "last_name": user.last_name,
    }

    new_role_code = data.get("role_code", user.role_code)
    if "role_code" in data:
        _assert_may_manage_role(actor, new_role_code)
        role = db.execute(select(Role).where(Role.code == new_role_code)).scalars().first()
        if role is None:
            raise ValidationFailed("The selected role does not exist.")
        user.role_id = role.id

    if "role_code" in data or "vendor_id" in data:
        new_vendor_id = data.get("vendor_id", user.vendor_id)
        vendor = _validate_vendor_pairing(db, new_role_code, new_vendor_id)
        _assert_tenant_scope(actor, vendor.id if vendor else None)
        old_vendor_id = user.vendor_id
        user.vendor_id = vendor.id if vendor else None
        if old_vendor_id != user.vendor_id:
            if user.vendor_id:
                existing = db.execute(
                    select(UserVendorAssignment).where(
                        UserVendorAssignment.user_id == user.id,
                        UserVendorAssignment.vendor_id == user.vendor_id,
                    )
                ).scalars().first()
                if existing is None:
                    db.add(UserVendorAssignment(
                        user_id=user.id, vendor_id=user.vendor_id,
                        is_primary=True, assigned_by_id=actor.id,
                    ))
            audit.record(
                db, action=AuditAction.VENDOR_ASSIGNMENT_CHANGED.value,
                user=actor, request=request,
                object_type="user", object_id=user.id, object_label=user.email,
                vendor_id=user.vendor_id,
                previous_value={"vendor_id": old_vendor_id},
                new_value={"vendor_id": user.vendor_id},
            )

    if "password" in data and data["password"]:
        validate_password_strength(data["password"])
        user.password_hash = hash_password(data["password"])
        user.must_change_password = True
        user.token_version = (user.token_version or 0) + 1

    if "email" in data and data["email"]:
        new_email = data["email"].lower().strip()
        if new_email != user.email:
            clash = db.execute(
                select(User).where(User.email == new_email, User.id != user.id)
            ).scalars().first()
            if clash:
                raise Conflict("An account with that email address already exists.")
            user.email = new_email

    for field in ("first_name", "last_name", "title", "phone"):
        if field in data and data[field] is not None:
            setattr(user, field, data[field])

    if "status" in data and data["status"]:
        if user.id == actor.id and data["status"] != UserStatus.ACTIVE.value:
            raise ValidationFailed("You cannot disable your own account.")
        user.status = data["status"]
        if user.status != UserStatus.ACTIVE.value:
            user.token_version = (user.token_version or 0) + 1

    db.add(user)
    db.flush()

    after = {
        "email": user.email, "role": user.role_code, "vendor_id": user.vendor_id,
        "status": user.status, "first_name": user.first_name,
        "last_name": user.last_name,
    }
    prev, new = audit.diff(before, after)
    action = (
        AuditAction.USER_DISABLED.value
        if after["status"] != before["status"] and after["status"] != UserStatus.ACTIVE.value
        else AuditAction.USER_UPDATED.value
    )
    audit.record(
        db, action=action, user=actor, request=request,
        object_type="user", object_id=user.id, object_label=user.email,
        vendor_id=user.vendor_id, previous_value=prev, new_value=new,
    )
    db.commit()
    db.refresh(user)
    return serialize(user)


@router.delete("/{user_id}")
def delete_user(
    user_id: int,
    request: Request,
    db: Session = Depends(get_db),
    actor: User = Depends(require_permission(Permission.USER_DELETE)),
    _: None = CsrfProtected,
):
    """Hard delete is Super-Admin-only and refused for users with history.

    Accounts that have touched a case are disabled instead, so the audit trail
    keeps its referents.
    """
    user = db.get(User, user_id)
    if user is None:
        raise NotFound("User not found.")
    if user.id == actor.id:
        raise ValidationFailed("You cannot delete your own account.")
    _assert_may_manage_role(actor, user.role_code)

    from app.models import Approval, Case

    has_history = db.execute(
        select(func.count(Case.id)).where(
            or_(Case.created_by_id == user.id, Case.assigned_to_id == user.id)
        )
    ).scalar_one() or db.execute(
        select(func.count(Approval.id)).where(Approval.decided_by_id == user.id)
    ).scalar_one()

    if has_history:
        previous = user.status
        user.status = UserStatus.DISABLED.value
        user.token_version = (user.token_version or 0) + 1
        db.add(user)
        audit.record(
            db, action=AuditAction.USER_DISABLED.value, user=actor, request=request,
            object_type="user", object_id=user.id, object_label=user.email,
            vendor_id=user.vendor_id,
            previous_value={"status": previous},
            new_value={"status": user.status},
            detail="Delete refused - account has case history; disabled instead.",
        )
        db.commit()
        return {
            "success": True,
            "message": f"{user.email} has case history and was disabled rather than "
                       "deleted, so the audit trail stays intact.",
            "disabled_instead": True,
        }

    email, uid, vendor_id = user.email, user.id, user.vendor_id
    db.execute(
        UserVendorAssignment.__table__.delete().where(
            UserVendorAssignment.user_id == uid
        )
    )
    db.delete(user)
    audit.record(
        db, action=AuditAction.USER_DELETED.value, user=actor, request=request,
        object_type="user", object_id=uid, object_label=email, vendor_id=vendor_id,
        previous_value={"email": email},
    )
    db.commit()
    return {"success": True, "message": f"{email} deleted."}
