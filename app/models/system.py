"""Audit trail and notifications."""
from __future__ import annotations

import datetime as dt
from typing import Optional

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.constants import NotificationSeverity
from app.database import Base, utcnow


class AuditLog(Base):
    """Append-only audit record.

    Nothing in the application updates or deletes rows in this table; the API
    exposes read + create only. In production, enforce it at the database level
    too (see README -> "Hardening the audit trail").
    """

    __tablename__ = "audit_logs"
    __table_args__ = (
        Index("ix_audit_vendor_ts", "vendor_id", "timestamp"),
        Index("ix_audit_object", "object_type", "object_id"),
        Index("ix_audit_action_ts", "action", "timestamp"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    timestamp: Mapped[dt.datetime] = mapped_column(
        DateTime, default=utcnow, nullable=False, index=True
    )

    user_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), index=True
    )
    #: Denormalised so the record survives user deletion.
    user_email: Mapped[Optional[str]] = mapped_column(String(191), index=True)
    role_code: Mapped[Optional[str]] = mapped_column(String(64))
    vendor_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("vendors.id", ondelete="SET NULL"), index=True
    )
    vendor_name: Mapped[Optional[str]] = mapped_column(String(191))

    action: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    object_type: Mapped[Optional[str]] = mapped_column(String(64))
    object_id: Mapped[Optional[str]] = mapped_column(String(64))
    object_label: Mapped[Optional[str]] = mapped_column(String(512))

    previous_value: Mapped[Optional[dict]] = mapped_column(JSON)
    new_value: Mapped[Optional[dict]] = mapped_column(JSON)
    detail: Mapped[Optional[str]] = mapped_column(Text)

    ip_address: Mapped[Optional[str]] = mapped_column(String(64))
    user_agent: Mapped[Optional[str]] = mapped_column(String(512))
    correlation_id: Mapped[Optional[str]] = mapped_column(String(64), index=True)
    success: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    def __repr__(self) -> str:  # pragma: no cover
        return f"<AuditLog {self.action} {self.object_type}:{self.object_id}>"


class Notification(Base):
    __tablename__ = "notifications"
    __table_args__ = (
        Index("ix_notifications_user_read", "user_id", "is_read"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    vendor_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("vendors.id", ondelete="CASCADE"), index=True
    )
    case_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("cases.id", ondelete="CASCADE"), index=True
    )

    notification_type: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    severity: Mapped[str] = mapped_column(
        String(32), default=NotificationSeverity.INFO.value, nullable=False
    )
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    message: Mapped[Optional[str]] = mapped_column(Text)
    link: Mapped[Optional[str]] = mapped_column(String(512))

    is_read: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False, index=True)
    read_at: Mapped[Optional[dt.datetime]] = mapped_column(DateTime)
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime, default=utcnow, nullable=False, index=True
    )
