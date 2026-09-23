"""Authentication, authorization and tenant-scoping dependencies.

Design rules enforced here:

1. No business endpoint is reachable without a valid session.
2. Role checks and vendor (tenant) checks are BOTH applied server-side.
3. Tenant scoping is applied to the *query*, not to the response - a vendor
   user's query can never load another vendor's row in the first place.
"""
from __future__ import annotations

import datetime as dt
from typing import Callable, Optional, Sequence

from fastapi import Depends, Request
from sqlalchemy import Select, select
from sqlalchemy.orm import Session

from app.auth.security import csrf_tokens_match, decode_access_token
from app.config import settings
from app.constants import Permission, RoleCode, UserStatus
from app.database import get_db
from app.errors import (
    AccountDisabled,
    CsrfFailed,
    NotAuthenticated,
    PermissionDenied,
    SessionExpired,
    VendorAccessDenied,
)
from app.models import User

SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}


# --------------------------------------------------------------------------- #
# Token extraction
# --------------------------------------------------------------------------- #
def _token_from_request(request: Request) -> Optional[str]:
    auth = request.headers.get("Authorization") or ""
    if auth.lower().startswith("bearer "):
        return auth[7:].strip() or None
    return request.cookies.get(settings.cookie_name)


def get_current_user(
    request: Request,
    db: Session = Depends(get_db),
) -> User:
    token = _token_from_request(request)
    if not token:
        raise NotAuthenticated()

    payload = decode_access_token(token)

    try:
        user_id = int(payload.get("sub"))
    except (TypeError, ValueError):
        raise SessionExpired()

    user = db.get(User, user_id)
    if user is None:
        raise SessionExpired("Your session is no longer valid.")

    # Token invalidated by logout / password change / forced sign-out.
    if int(payload.get("tv", 0)) != int(user.token_version or 0):
        raise SessionExpired("Your session has been ended. Please sign in again.")

    if user.status != UserStatus.ACTIVE.value:
        raise AccountDisabled()

    if user.locked_until and user.locked_until > dt.datetime.utcnow():
        raise AccountDisabled("This account is temporarily locked.")

    # A vendor user must have a vendor, and any tenant-scoped account loses
    # access the moment its vendor is suspended.
    if user.is_vendor_user and user.vendor_id is None:
        raise AccountDisabled(
            "This account has no vendor assignment. Contact your administrator."
        )
    if user.vendor_id is not None and user.vendor and not user.vendor.is_active:
        raise AccountDisabled(
            "Your organization's access to Sentinel IP AI is currently suspended."
        )

    request.state.user = user
    return user


def get_optional_user(request: Request, db: Session = Depends(get_db)) -> Optional[User]:
    """Never raises - used by page routes that render a public shell."""
    try:
        return get_current_user(request, db)
    except Exception:
        return None


# --------------------------------------------------------------------------- #
# CSRF
# --------------------------------------------------------------------------- #
def verify_csrf(request: Request) -> None:
    """Double-submit CSRF check for cookie-authenticated mutations.

    Requests authenticated with an `Authorization: Bearer` header are not
    cookie-driven and therefore not CSRF-exposed, so they are exempt.
    """
    if request.method in SAFE_METHODS:
        return
    auth = request.headers.get("Authorization") or ""
    if auth.lower().startswith("bearer "):
        return
    if not request.cookies.get(settings.cookie_name):
        return  # unauthenticated; auth dependency will reject it
    cookie_token = request.cookies.get(settings.csrf_cookie_name)
    header_token = request.headers.get("X-CSRF-Token")
    if not csrf_tokens_match(cookie_token, header_token):
        raise CsrfFailed()


CsrfProtected = Depends(verify_csrf)


# --------------------------------------------------------------------------- #
# Role / permission guards
# --------------------------------------------------------------------------- #
def require_roles(*roles: str) -> Callable:
    allowed = {r.value if hasattr(r, "value") else str(r) for r in roles}

    def _dep(user: User = Depends(get_current_user)) -> User:
        if user.role_code not in allowed:
            raise PermissionDenied(
                "Your role does not have access to this area of Sentinel IP AI."
            )
        return user

    return _dep


def require_permission(*permissions: str) -> Callable:
    needed = [p.value if hasattr(p, "value") else str(p) for p in permissions]

    def _dep(user: User = Depends(get_current_user)) -> User:
        if not all(user.has_permission(p) for p in needed):
            raise PermissionDenied()
        return user

    return _dep


require_super_admin = require_roles(RoleCode.SUPER_ADMIN)
require_admin = require_roles(RoleCode.SUPER_ADMIN, RoleCode.ADMIN)
CurrentUser = Depends(get_current_user)


# --------------------------------------------------------------------------- #
# Tenant scoping - the multi-tenant security boundary
# --------------------------------------------------------------------------- #
def visible_vendor_ids(user: User) -> Optional[Sequence[int]]:
    """`None` means "all vendors"; otherwise the explicit allow-list."""
    if user.is_global:
        return None
    return [user.vendor_id] if user.vendor_id is not None else []


def scope_to_vendor(stmt: Select, model, user: User) -> Select:
    """Apply the tenant filter to a SELECT.

    Called on EVERY query that touches vendor-owned data. Global roles get an
    unfiltered statement; a vendor user gets `model.vendor_id == user.vendor_id`
    welded on before the query is ever executed.
    """
    if user.is_global:
        return stmt
    if user.vendor_id is None:
        # Defensive: a vendor-scoped user with no vendor sees nothing at all.
        return stmt.where(model.vendor_id.is_(None) & (model.vendor_id == -1))
    return stmt.where(model.vendor_id == user.vendor_id)


def assert_vendor_access(user: User, vendor_id: Optional[int]) -> None:
    """Raise 403 unless `user` may act inside `vendor_id`."""
    if user.is_global:
        return
    if vendor_id is None or user.vendor_id != vendor_id:
        raise VendorAccessDenied()


def get_scoped_or_404(db: Session, model, object_id, user: User):
    """Load a vendor-owned row with the tenant filter applied in the query.

    A vendor user asking for another vendor's id gets a 404-shaped response
    that leaks nothing about whether the record exists. Callers that prefer an
    explicit 403 use `assert_vendor_access` instead.
    """
    from app.errors import NotFound

    stmt = select(model).where(model.id == object_id)
    stmt = scope_to_vendor(stmt, model, user)
    obj = db.execute(stmt).scalars().first()
    if obj is None:
        raise NotFound()
    return obj


__all__ = [
    "get_current_user",
    "get_optional_user",
    "require_roles",
    "require_permission",
    "require_admin",
    "require_super_admin",
    "verify_csrf",
    "CsrfProtected",
    "CurrentUser",
    "scope_to_vendor",
    "assert_vendor_access",
    "get_scoped_or_404",
    "visible_vendor_ids",
    "Permission",
]
