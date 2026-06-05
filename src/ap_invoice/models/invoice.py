"""Invoice and line-item models."""

from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal
from typing import TYPE_CHECKING, Any

from sqlalchemy import (
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

from ap_invoice.core.enums import ApprovalDecision, ExtractionSource, InvoiceStatus
from ap_invoice.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin, str_enum

if TYPE_CHECKING:
    from ap_invoice.models.audit import ProcessingEvent
    from ap_invoice.models.organization import Organization
    from ap_invoice.models.vendor import Vendor


class Invoice(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """An invoice as it flows through extraction, validation, and approval."""

    __tablename__ = "invoices"
    __table_args__ = (
        # Per-org idempotency guard for safe re-ingestion of the same document.
        UniqueConstraint("organization_id", "idempotency_key", name="uq_invoice_org_idempotency"),
    )

    organization_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    # Nullable until the Vendor Name Normaliser resolves a canonical vendor.
    vendor_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("vendors.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )

    # --- Extracted header fields ---
    raw_vendor_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    invoice_number: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)
    invoice_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    due_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    currency: Mapped[str | None] = mapped_column(String(3), nullable=True)
    subtotal: Mapped[Decimal | None] = mapped_column(Numeric(18, 2), nullable=True)
    tax: Mapped[Decimal | None] = mapped_column(Numeric(18, 2), nullable=True)
    grand_total: Mapped[Decimal | None] = mapped_column(Numeric(18, 2), nullable=True)
    payment_terms: Mapped[str | None] = mapped_column(String(64), nullable=True)

    # --- Provenance ---
    raw_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    source: Mapped[str | None] = mapped_column(String(64), nullable=True)
    idempotency_key: Mapped[str | None] = mapped_column(String(128), nullable=True)
    # Stable fingerprint (vendor + number + amount) used for duplicate detection.
    fingerprint: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)

    # --- Processing results ---
    status: Mapped[InvoiceStatus] = mapped_column(
        str_enum(InvoiceStatus, length=20),
        nullable=False,
        default=InvoiceStatus.RECEIVED,
        index=True,
    )
    recommended_action: Mapped[ApprovalDecision | None] = mapped_column(
        str_enum(ApprovalDecision, length=20), nullable=True
    )
    completeness_score: Mapped[Decimal | None] = mapped_column(Numeric(5, 2), nullable=True)
    extraction_source: Mapped[ExtractionSource | None] = mapped_column(
        str_enum(ExtractionSource, length=20), nullable=True
    )
    # Per-field confidence map, e.g. {"invoice_number": 0.98, "grand_total": 0.91}.
    extraction_confidence: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict
    )
    extra_metadata: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)

    organization: Mapped[Organization] = relationship(back_populates="invoices")
    vendor: Mapped[Vendor | None] = relationship(back_populates="invoices")
    line_items: Mapped[list[InvoiceLineItem]] = relationship(
        back_populates="invoice",
        cascade="all, delete-orphan",
        order_by="InvoiceLineItem.line_number",
    )
    events: Mapped[list[ProcessingEvent]] = relationship(
        back_populates="invoice",
        cascade="all, delete-orphan",
        order_by="ProcessingEvent.created_at",
    )


class InvoiceLineItem(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """A single billed line on an invoice."""

    __tablename__ = "invoice_line_items"

    invoice_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("invoices.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    line_number: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    quantity: Mapped[Decimal | None] = mapped_column(Numeric(18, 4), nullable=True)
    unit_price: Mapped[Decimal | None] = mapped_column(Numeric(18, 4), nullable=True)
    line_total: Mapped[Decimal | None] = mapped_column(Numeric(18, 2), nullable=True)

    invoice: Mapped[Invoice] = relationship(back_populates="line_items")
