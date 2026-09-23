"""Reference data: marketplaces, products, suspect listings and SLA policies."""
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
    Numeric,
    String,
    Text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.constants import Priority
from app.database import Base, utcnow


class Marketplace(Base):
    __tablename__ = "marketplaces"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    code: Mapped[str] = mapped_column(String(64), unique=True, nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(191), nullable=False)
    region: Mapped[Optional[str]] = mapped_column(String(96))
    website: Mapped[Optional[str]] = mapped_column(String(512))
    complaint_portal_url: Mapped[Optional[str]] = mapped_column(String(512))
    avg_response_hours: Mapped[int] = mapped_column(Integer, default=72, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=utcnow, nullable=False)

    def __repr__(self) -> str:  # pragma: no cover
        return f"<Marketplace {self.code}>"


class SlaPolicy(Base):
    __tablename__ = "sla_policies"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(191), nullable=False, index=True)
    description: Mapped[Optional[str]] = mapped_column(String(512))
    #: Hours from case creation until the enforcement decision is due.
    response_hours: Mapped[int] = mapped_column(Integer, default=48, nullable=False)
    #: Hours allowed for the marketplace to respond after filing.
    resolution_hours: Mapped[int] = mapped_column(Integer, default=168, nullable=False)
    priority: Mapped[str] = mapped_column(
        String(32), default=Priority.MEDIUM.value, nullable=False
    )
    #: % of the window elapsed before a case is flagged "At Risk".
    at_risk_percent: Mapped[int] = mapped_column(Integer, default=75, nullable=False)
    #: NULL => global policy usable by any vendor.
    vendor_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("vendors.id", ondelete="CASCADE"), index=True
    )
    is_default: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_by_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=utcnow, nullable=False)
    updated_at: Mapped[dt.datetime] = mapped_column(
        DateTime, default=utcnow, onupdate=utcnow, nullable=False
    )


class Product(Base):
    """A protected product in a vendor's catalog."""

    __tablename__ = "products"
    __table_args__ = (
        Index("ix_products_vendor_sku", "vendor_id", "sku"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    vendor_id: Mapped[int] = mapped_column(
        ForeignKey("vendors.id", ondelete="CASCADE"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    sku: Mapped[Optional[str]] = mapped_column(String(128), index=True)
    brand: Mapped[Optional[str]] = mapped_column(String(191), index=True)
    trademark: Mapped[Optional[str]] = mapped_column(String(191))
    trademark_registration: Mapped[Optional[str]] = mapped_column(String(128))
    product_url: Mapped[Optional[str]] = mapped_column(String(512))
    image_url: Mapped[Optional[str]] = mapped_column(String(512))
    description: Mapped[Optional[str]] = mapped_column(Text)
    msrp: Mapped[Optional[float]] = mapped_column(Numeric(12, 2))
    currency: Mapped[str] = mapped_column(String(8), default="USD", nullable=False)
    created_by_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=utcnow, nullable=False)
    updated_at: Mapped[dt.datetime] = mapped_column(
        DateTime, default=utcnow, onupdate=utcnow, nullable=False
    )

    vendor: Mapped["Vendor"] = relationship()  # noqa: F821


class Listing(Base):
    """A suspected infringing marketplace listing."""

    __tablename__ = "listings"
    __table_args__ = (
        Index("ix_listings_vendor_marketplace", "vendor_id", "marketplace_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    vendor_id: Mapped[int] = mapped_column(
        ForeignKey("vendors.id", ondelete="CASCADE"), nullable=False, index=True
    )
    # listings <-> cases is a cycle (a case points at its primary listing),
    # so this constraint is added with ALTER after both tables exist.
    case_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("cases.id", ondelete="CASCADE", use_alter=True), index=True
    )
    marketplace_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("marketplaces.id", ondelete="SET NULL"), index=True
    )
    title: Mapped[Optional[str]] = mapped_column(String(512))
    url: Mapped[str] = mapped_column(String(1024), nullable=False)
    seller_name: Mapped[Optional[str]] = mapped_column(String(191), index=True)
    seller_url: Mapped[Optional[str]] = mapped_column(String(1024))
    seller_country: Mapped[Optional[str]] = mapped_column(String(96))
    price: Mapped[Optional[float]] = mapped_column(Numeric(12, 2))
    currency: Mapped[str] = mapped_column(String(8), default="USD", nullable=False)
    listing_date: Mapped[Optional[dt.datetime]] = mapped_column(DateTime)
    image_url: Mapped[Optional[str]] = mapped_column(String(1024))
    #: Raw capture from Scout (seller ratings, shipping origin, etc.)
    captured_data: Mapped[Optional[dict]] = mapped_column(JSON)
    captured_at: Mapped[dt.datetime] = mapped_column(DateTime, default=utcnow, nullable=False)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=utcnow, nullable=False)

    marketplace: Mapped[Optional["Marketplace"]] = relationship(lazy="joined")
