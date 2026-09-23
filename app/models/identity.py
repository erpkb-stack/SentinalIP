"""Roles, users, vendors and vendor assignments."""
from __future__ import annotations

import datetime as dt
from typing import List, Optional

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.constants import RoleCode, UserStatus, VendorStatus
from app.database import Base, utcnow


class Role(Base):
    __tablename__ = "roles"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    code: Mapped[str] = mapped_column(String(64), unique=True, nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    description: Mapped[Optional[str]] = mapped_column(String(512))
    permissions: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    is_system: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    requires_vendor: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=utcnow, nullable=False)

    users: Mapped[List["User"]] = relationship(back_populates="role")

    def __repr__(self) -> str:  # pragma: no cover
        return f"<Role {self.code}>"


class Vendor(Base):
    """A tenant. Every business record hangs off exactly one vendor."""

    __tablename__ = "vendors"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(191), nullable=False, unique=True, index=True)
    legal_name: Mapped[Optional[str]] = mapped_column(String(255))
    contact_name: Mapped[Optional[str]] = mapped_column(String(191))
    contact_email: Mapped[Optional[str]] = mapped_column(String(191), index=True)
    phone: Mapped[Optional[str]] = mapped_column(String(64))
    country: Mapped[Optional[str]] = mapped_column(String(96))
    website: Mapped[Optional[str]] = mapped_column(String(512))
    industry: Mapped[Optional[str]] = mapped_column(String(128))

    # vendors <-> sla_policies is a cycle (a policy may be vendor-specific), so
    # this constraint is added with ALTER after both tables exist.
    sla_policy_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("sla_policies.id", ondelete="SET NULL", use_alter=True), index=True
    )
    autonomy_tier: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    status: Mapped[str] = mapped_column(
        String(32), default=VendorStatus.ACTIVE.value, nullable=False, index=True
    )
    notes: Mapped[Optional[str]] = mapped_column(Text)

    # vendors <-> users is a cycle (a user belongs to a vendor; a vendor records
    # the user who created it), so this constraint is added with ALTER.
    created_by_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL", use_alter=True)
    )
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=utcnow, nullable=False)
    updated_at: Mapped[dt.datetime] = mapped_column(
        DateTime, default=utcnow, onupdate=utcnow, nullable=False
    )

    users: Mapped[List["User"]] = relationship(
        back_populates="vendor", foreign_keys="User.vendor_id"
    )
    sla_policy: Mapped[Optional["SlaPolicy"]] = relationship(  # noqa: F821
        "SlaPolicy", foreign_keys=[sla_policy_id]
    )

    @property
    def is_active(self) -> bool:
        return self.status == VendorStatus.ACTIVE.value

    def __repr__(self) -> str:  # pragma: no cover
        return f"<Vendor {self.id} {self.name}>"


class User(Base):
    __tablename__ = "users"
    __table_args__ = (
        Index("ix_users_vendor_status", "vendor_id", "status"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    email: Mapped[str] = mapped_column(String(191), unique=True, nullable=False, index=True)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    first_name: Mapped[str] = mapped_column(String(96), nullable=False)
    last_name: Mapped[str] = mapped_column(String(96), nullable=False)
    phone: Mapped[Optional[str]] = mapped_column(String(64))
    title: Mapped[Optional[str]] = mapped_column(String(128))

    role_id: Mapped[int] = mapped_column(ForeignKey("roles.id"), nullable=False, index=True)
    #: NULL for global roles (Super Admin / Admin). REQUIRED for Vendor User.
    vendor_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("vendors.id", ondelete="RESTRICT"), index=True
    )

    status: Mapped[str] = mapped_column(
        String(32), default=UserStatus.ACTIVE.value, nullable=False, index=True
    )
    must_change_password: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    last_login_at: Mapped[Optional[dt.datetime]] = mapped_column(DateTime)
    last_login_ip: Mapped[Optional[str]] = mapped_column(String(64))
    failed_login_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    locked_until: Mapped[Optional[dt.datetime]] = mapped_column(DateTime)
    #: Bumped on logout / password change so old JWTs stop validating.
    token_version: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    created_by_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=utcnow, nullable=False)
    updated_at: Mapped[dt.datetime] = mapped_column(
        DateTime, default=utcnow, onupdate=utcnow, nullable=False
    )

    role: Mapped["Role"] = relationship(back_populates="users", lazy="joined")
    vendor: Mapped[Optional["Vendor"]] = relationship(
        back_populates="users", foreign_keys=[vendor_id], lazy="joined"
    )

    # ---------------- convenience ----------------
    @property
    def full_name(self) -> str:
        return f"{self.first_name} {self.last_name}".strip()

    @property
    def role_code(self) -> str:
        return self.role.code if self.role else ""

    @property
    def is_super_admin(self) -> bool:
        return self.role_code == RoleCode.SUPER_ADMIN.value

    @property
    def is_admin(self) -> bool:
        return self.role_code == RoleCode.ADMIN.value

    @property
    def is_vendor_user(self) -> bool:
        return self.role_code == RoleCode.VENDOR_USER.value

    @property
    def is_global(self) -> bool:
        """True when the user is not confined to a single tenant.

        A Super Admin is always global. An Admin is global only when they have
        no vendor - an Admin attached to a vendor is that tenant's
        administrator and is scoped exactly like a vendor user.
        """
        if self.role_code == RoleCode.SUPER_ADMIN.value:
            return True
        if self.role_code == RoleCode.ADMIN.value:
            return self.vendor_id is None
        return False

    @property
    def is_active(self) -> bool:
        return self.status == UserStatus.ACTIVE.value

    @property
    def permissions(self) -> list:
        return list(self.role.permissions or []) if self.role else []

    def has_permission(self, perm: str) -> bool:
        return perm in self.permissions

    def __repr__(self) -> str:  # pragma: no cover
        return f"<User {self.id} {self.email} {self.role_code}>"


class UserVendorAssignment(Base):
    """History of user<->vendor assignment.

    A Vendor User belongs to exactly one vendor in this version; this table is
    the append-friendly record of how that came to be (and the extension point
    for multi-vendor access later).
    """

    __tablename__ = "user_vendor_assignments"
    __table_args__ = (
        UniqueConstraint("user_id", "vendor_id", name="uq_user_vendor"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    vendor_id: Mapped[int] = mapped_column(
        ForeignKey("vendors.id", ondelete="CASCADE"), nullable=False, index=True
    )
    is_primary: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    assigned_by_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )
    assigned_at: Mapped[dt.datetime] = mapped_column(DateTime, default=utcnow, nullable=False)
    revoked_at: Mapped[Optional[dt.datetime]] = mapped_column(DateTime)

    user: Mapped["User"] = relationship(foreign_keys=[user_id])
    vendor: Mapped["Vendor"] = relationship()
