"""Vendor and vendor-policy models."""

from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal
from typing import TYPE_CHECKING, Any

from sqlalchemy import (
    Boolean,
    Date,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ap_invoice.core.enums import VendorStatus
from ap_invoice.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin, str_enum

if TYPE_CHECKING:
    from ap_invoice.models.invoice import Invoice
    from ap_invoice.models.organization import Organization
    from ap_invoice.models.policy_document import PolicyRule, VendorDocument


class Vendor(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """A supplier within an organization's vendor master."""

    __tablename__ = "vendors"
    __table_args__ = (
        UniqueConstraint("organization_id", "canonical_name", name="uq_vendor_org_canonical"),
    )

    organization_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    canonical_name: Mapped[str] = mapped_column(String(255), nullable=False)
    display_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    # Known alternate spellings used by the Vendor Name Normaliser.
    aliases: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    tax_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    email: Mapped[str | None] = mapped_column(String(320), nullable=True)
    status: Mapped[VendorStatus] = mapped_column(
        str_enum(VendorStatus, length=20), nullable=False, default=VendorStatus.ONBOARDING
    )
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    organization: Mapped[Organization] = relationship(back_populates="vendors")
    policies: Mapped[list[VendorPolicy]] = relationship(
        back_populates="vendor",
        cascade="all, delete-orphan",
        order_by="desc(VendorPolicy.version)",
    )
    invoices: Mapped[list[Invoice]] = relationship(back_populates="vendor")
    documents: Mapped[list[VendorDocument]] = relationship(
        back_populates="vendor", cascade="all, delete-orphan"
    )
    policy_rules: Mapped[list[PolicyRule]] = relationship(
        back_populates="vendor", cascade="all, delete-orphan"
    )

    @property
    def active_policy(self) -> VendorPolicy | None:
        """Return the current active policy, if any."""
        for policy in self.policies:
            if policy.is_active:
                return policy
        return None


class VendorPolicy(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Versioned set of rules governing how a vendor's invoices are processed.

    Policies are versioned (never updated in place) so every historical decision
    can be reproduced against the exact policy that was active at the time.
    """

    __tablename__ = "vendor_policies"
    __table_args__ = (UniqueConstraint("vendor_id", "version", name="uq_vendor_policy_version"),)

    vendor_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("vendors.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    # --- Payment terms ---
    payment_terms: Mapped[str] = mapped_column(String(64), nullable=False, default="Net 30")
    currency: Mapped[str] = mapped_column(String(3), nullable=False, default="USD")
    allow_early_payment_discount: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True
    )

    # --- Validation rules ---
    # Field names that MUST be present for an invoice to be processed.
    mandatory_fields: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    min_completeness_score: Mapped[Decimal] = mapped_column(
        Numeric(5, 2), nullable=False, default=Decimal("100.00")
    )

    # --- Amount / approval thresholds ---
    # Invoices at or below this amount auto-approve when otherwise clean.
    auto_approve_max_amount: Mapped[Decimal | None] = mapped_column(Numeric(18, 2), nullable=True)
    # Invoices above this amount always require human review (never auto-approve).
    requires_review_above_amount: Mapped[Decimal | None] = mapped_column(
        Numeric(18, 2), nullable=True
    )
    # Percentage tolerance used for near-duplicate amount matching.
    amount_tolerance_pct: Mapped[Decimal] = mapped_column(
        Numeric(5, 2), nullable=False, default=Decimal("5.00")
    )
    duplicate_lookback_days: Mapped[int] = mapped_column(Integer, nullable=False, default=90)

    # --- Freeform contractual terms & conditions (structured JSON) ---
    terms_and_conditions: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict
    )

    effective_from: Mapped[date | None] = mapped_column(Date, nullable=True)
    effective_to: Mapped[date | None] = mapped_column(Date, nullable=True)

    vendor: Mapped[Vendor] = relationship(back_populates="policies")
