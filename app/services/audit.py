"""Append-only audit trail.

`record()` is the ONLY way rows enter `audit_logs`, and nothing anywhere
updates or deletes them.
"""
from __future__ import annotations

import datetime as dt
from typing import Any, Dict, Optional

from fastapi import Request
from sqlalchemy.orm import Session

from app.database import utcnow
from app.logging_config import get_correlation_id, get_logger
from app.models import AuditLog, User

logger = get_logger(__name__)

#: Never store these, even if they appear in a changed-field diff.
REDACTED_FIELDS = {
    "password", "password_hash", "new_password", "current_password",
    "temporary_password", "secret_key", "api_key", "token", "access_token",
    "openai_api_key", "gemini_api_key",
}


def scrub(value: Any) -> Any:
    """Recursively strip secrets out of anything headed for the audit table."""
    if isinstance(value, dict):
        return {
            k: ("[REDACTED]" if k.lower() in REDACTED_FIELDS else scrub(v))
            for k, v in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [scrub(v) for v in value]
    if isinstance(value, (dt.datetime, dt.date)):
        return value.isoformat()
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)


def record(
    db: Session,
    *,
    action: str,
    user: Optional[User] = None,
    request: Optional[Request] = None,
    object_type: Optional[str] = None,
    object_id: Optional[Any] = None,
    object_label: Optional[str] = None,
    previous_value: Optional[Dict[str, Any]] = None,
    new_value: Optional[Dict[str, Any]] = None,
    detail: Optional[str] = None,
    vendor_id: Optional[int] = None,
    user_email: Optional[str] = None,
    success: bool = True,
    commit: bool = False,
) -> AuditLog:
    """Write one audit row. Never raises into the caller's request path."""
    try:
        entry = AuditLog(
            timestamp=utcnow(),
            user_id=user.id if user else None,
            user_email=(user.email if user else user_email),
            role_code=(user.role_code if user else None),
            vendor_id=vendor_id if vendor_id is not None else (user.vendor_id if user else None),
            vendor_name=(user.vendor.name if user and user.vendor else None),
            action=action,
            object_type=object_type,
            object_id=str(object_id) if object_id is not None else None,
            object_label=(object_label[:512] if object_label else None),
            previous_value=scrub(previous_value) if previous_value else None,
            new_value=scrub(new_value) if new_value else None,
            detail=detail,
            ip_address=getattr(request.state, "client_ip", None) if request else None,
            user_agent=(request.headers.get("user-agent", "")[:512] if request else None),
            correlation_id=get_correlation_id(),
            success=success,
        )
        db.add(entry)
        db.flush()
        if commit:
            db.commit()
        return entry
    except Exception:  # pragma: no cover - auditing must never break the request
        logger.exception("Failed to write audit record for action=%s", action)
        db.rollback()
        raise


def diff(before: Dict[str, Any], after: Dict[str, Any]) -> tuple[dict, dict]:
    """Return only the fields that actually changed, as (previous, new)."""
    prev, new = {}, {}
    for key, new_val in after.items():
        old_val = before.get(key)
        if old_val != new_val:
            prev[key] = old_val
            new[key] = new_val
    return prev, new
