"""Authentication endpoints."""
from __future__ import annotations

import datetime as dt

from fastapi import APIRouter, Depends, Request, Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth import (
    CsrfProtected,
    create_access_token,
    generate_csrf_token,
    get_current_user,
    hash_password,
    validate_password_strength,
    verify_password,
)
from app.config import settings
from app.constants import ROLE_LABELS, AuditAction, UserStatus
from app.database import get_db, utcnow
from app.errors import (
    AccountDisabled,
    AccountLocked,
    InvalidCredentials,
    RateLimited,
    ValidationFailed,
)
from app.logging_config import get_logger
from app.middleware.rate_limit import login_rate_limiter
from app.models import User
from app.schemas.identity import (
    LoginRequest,
    LoginResponse,
    MeResponse,
    PasswordChangeRequest,
)
from app.services import audit

router = APIRouter(prefix="/api/auth", tags=["auth"])
logger = get_logger(__name__)

#: A real hash of an unguessable value. Verifying against it when the account
#: does not exist keeps the timing of a failed sign-in roughly constant, so an
#: attacker cannot enumerate accounts from response time.
_DUMMY_HASH = hash_password(__import__("secrets").token_urlsafe(24))


def me_payload(user: User) -> MeResponse:
    return MeResponse(
        id=user.id,
        email=user.email,
        full_name=user.full_name,
        first_name=user.first_name,
        last_name=user.last_name,
        role_code=user.role_code,
        role_label=ROLE_LABELS.get(user.role_code, user.role_code),
        permissions=user.permissions,
        vendor_id=user.vendor_id,
        vendor_name=user.vendor.name if user.vendor else None,
        autonomy_tier=user.vendor.autonomy_tier if user.vendor else None,
        is_global=user.is_global,
        must_change_password=user.must_change_password,
        last_login_at=user.last_login_at,
    )


def _set_session_cookies(
    response: Response, token: str, csrf_token: str, expires: dt.datetime, remember: bool
) -> None:
    max_age = (
        settings.remember_me_expire_minutes * 60 if remember
        else settings.access_token_expire_minutes * 60
    )
    response.set_cookie(
        key=settings.cookie_name,
        value=token,
        max_age=max_age,
        httponly=True,                       # not readable by JavaScript
        secure=settings.cookie_secure,
        samesite=settings.cookie_samesite,
        path="/",
    )
    response.set_cookie(
        key=settings.csrf_cookie_name,
        value=csrf_token,
        max_age=max_age,
        httponly=False,                      # the SPA echoes this in X-CSRF-Token
        secure=settings.cookie_secure,
        samesite=settings.cookie_samesite,
        path="/",
    )


def _clear_session_cookies(response: Response) -> None:
    response.delete_cookie(settings.cookie_name, path="/")
    response.delete_cookie(settings.csrf_cookie_name, path="/")


@router.post("/login", response_model=LoginResponse)
def login(
    payload: LoginRequest,
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
):
    email = payload.email.lower().strip()
    ip = getattr(request.state, "client_ip", "unknown")

    # Throttle by IP+email so one attacker cannot lock out every account,
    # and one account cannot be brute-forced from many IPs cheaply.
    for key in (f"ip:{ip}", f"user:{email}"):
        allowed, retry_after = login_rate_limiter.hit(key)
        if not allowed:
            audit.record(
                db, action=AuditAction.LOGIN_FAILED.value, request=request,
                user_email=email, detail="Rate limited", success=False, commit=True,
            )
            raise RateLimited(
                f"Too many sign-in attempts. Try again in {retry_after} seconds.",
                headers={"Retry-After": str(retry_after)},
            )

    user = db.execute(select(User).where(User.email == email)).scalars().first()

    # Constant-ish work whether or not the user exists.
    if user is None:
        verify_password(payload.password, _DUMMY_HASH)
        audit.record(
            db, action=AuditAction.LOGIN_FAILED.value, request=request,
            user_email=email, detail="No such account", success=False, commit=True,
        )
        raise InvalidCredentials()

    if user.locked_until and user.locked_until > utcnow():
        audit.record(
            db, action=AuditAction.LOGIN_FAILED.value, user=user, request=request,
            detail="Account locked", success=False, commit=True,
        )
        raise AccountLocked()

    if not verify_password(payload.password, user.password_hash):
        user.failed_login_count = (user.failed_login_count or 0) + 1
        detail = "Incorrect password"
        if user.failed_login_count >= settings.account_lockout_attempts:
            user.locked_until = utcnow() + dt.timedelta(
                minutes=settings.account_lockout_minutes
            )
            user.failed_login_count = 0
            detail = "Incorrect password - account locked"
        db.add(user)
        audit.record(
            db, action=AuditAction.LOGIN_FAILED.value, user=user, request=request,
            detail=detail, success=False, commit=True,
        )
        raise InvalidCredentials()

    if user.status != UserStatus.ACTIVE.value:
        audit.record(
            db, action=AuditAction.LOGIN_FAILED.value, user=user, request=request,
            detail=f"Account status {user.status}", success=False, commit=True,
        )
        raise AccountDisabled()

    if user.is_vendor_user and (user.vendor is None or not user.vendor.is_active):
        audit.record(
            db, action=AuditAction.LOGIN_FAILED.value, user=user, request=request,
            detail="Vendor inactive or unassigned", success=False, commit=True,
        )
        raise AccountDisabled(
            "Your organization's access to Sentinel IP AI is currently suspended."
        )

    # ---- success ----
    user.failed_login_count = 0
    user.locked_until = None
    user.last_login_at = utcnow()
    user.last_login_ip = ip
    db.add(user)

    token, expires = create_access_token(
        user_id=user.id,
        email=user.email,
        role_code=user.role_code,
        vendor_id=user.vendor_id,
        token_version=user.token_version or 0,
        remember=payload.remember_me,
    )
    csrf_token = generate_csrf_token()
    _set_session_cookies(response, token, csrf_token, expires, payload.remember_me)

    login_rate_limiter.reset(f"user:{email}")
    audit.record(
        db, action=AuditAction.LOGIN.value, user=user, request=request,
        object_type="user", object_id=user.id, object_label=user.email,
        detail=f"Signed in{' with remember-me' if payload.remember_me else ''}",
        commit=True,
    )

    return LoginResponse(
        access_token=token,
        expires_at=expires,
        csrf_token=csrf_token,
        user=me_payload(user),
    )


@router.post("/logout")
def logout(
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
    _: None = CsrfProtected,
):
    # Invalidate every token already issued to this user.
    user.token_version = (user.token_version or 0) + 1
    db.add(user)
    audit.record(
        db, action=AuditAction.LOGOUT.value, user=user, request=request,
        object_type="user", object_id=user.id, object_label=user.email, commit=True,
    )
    _clear_session_cookies(response)
    return {"success": True, "message": "Signed out."}


@router.get("/me", response_model=MeResponse)
def me(user: User = Depends(get_current_user)):
    return me_payload(user)


@router.get("/csrf")
def csrf(request: Request, response: Response, user: User = Depends(get_current_user)):
    """Re-issue a CSRF token for a session whose cookie was lost."""
    token = request.cookies.get(settings.csrf_cookie_name) or generate_csrf_token()
    response.set_cookie(
        key=settings.csrf_cookie_name, value=token, httponly=False,
        secure=settings.cookie_secure, samesite=settings.cookie_samesite, path="/",
    )
    return {"success": True, "csrf_token": token}


@router.post("/change-password")
def change_password(
    payload: PasswordChangeRequest,
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
    _: None = CsrfProtected,
):
    if not verify_password(payload.current_password, user.password_hash):
        raise ValidationFailed("Your current password is incorrect.")
    if payload.new_password == payload.current_password:
        raise ValidationFailed("The new password must be different.")
    validate_password_strength(payload.new_password)

    user.password_hash = hash_password(payload.new_password)
    user.must_change_password = False
    user.token_version = (user.token_version or 0) + 1
    db.add(user)

    audit.record(
        db, action=AuditAction.USER_UPDATED.value, user=user, request=request,
        object_type="user", object_id=user.id, object_label=user.email,
        detail="Password changed", new_value={"password": "[REDACTED]"},
    )
    db.commit()

    token, expires = create_access_token(
        user_id=user.id, email=user.email, role_code=user.role_code,
        vendor_id=user.vendor_id, token_version=user.token_version,
    )
    csrf_token = generate_csrf_token()
    _set_session_cookies(response, token, csrf_token, expires, False)
    return {"success": True, "message": "Password updated.",
            "access_token": token, "csrf_token": csrf_token}
