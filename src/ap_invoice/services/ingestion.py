"""Helpers for turning extracted invoice data into persisted ORM rows.

Shared by the REST API and the MCP server so ingestion behaves identically on
both surfaces (fingerprinting, idempotency, line-item mapping).
"""

from __future__ import annotations

import hashlib
import uuid
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ap_invoice.core.enums import InvoiceStatus
from ap_invoice.models.invoice import Invoice, InvoiceLineItem
from ap_invoice.schemas.tools import ExtractedInvoice
from ap_invoice.services._parsing import normalize_invoice_number, normalize_vendor_name


def compute_fingerprint(
    vendor_name: str | None, invoice_number: str | None, amount: Decimal | None
) -> str | None:
    """Stable fingerprint for duplicate detection (vendor + number + amount)."""
    if not invoice_number and not vendor_name:
        return None
    parts = [
        normalize_vendor_name(vendor_name) if vendor_name else "",
        normalize_invoice_number(invoice_number) if invoice_number else "",
        f"{amount:.2f}" if amount is not None else "",
    ]
    return hashlib.sha256("|".join(parts).encode()).hexdigest()[:32]


async def find_by_idempotency(
    db: AsyncSession, org_id: uuid.UUID, key: str | None
) -> Invoice | None:
    """Return an existing invoice for (org, idempotency_key), if any."""
    if not key:
        return None
    return (
        await db.execute(
            select(Invoice).where(Invoice.organization_id == org_id, Invoice.idempotency_key == key)
        )
    ).scalar_one_or_none()


def invoice_from_extracted(
    org_id: uuid.UUID,
    extracted: ExtractedInvoice,
    *,
    raw_text: str,
    source: str | None,
    idempotency_key: str | None,
    extra_metadata: dict[str, Any] | None = None,
) -> Invoice:
    """Build a persisted-ready Invoice (with line items) from an extraction."""
    invoice = Invoice(
        organization_id=org_id,
        raw_vendor_name=extracted.vendor_name,
        invoice_number=extracted.invoice_number,
        invoice_date=extracted.invoice_date,
        due_date=extracted.due_date,
        currency=extracted.currency,
        subtotal=extracted.subtotal,
        tax=extracted.tax,
        grand_total=extracted.grand_total,
        payment_terms=extracted.payment_terms,
        raw_text=raw_text,
        source=source,
        idempotency_key=idempotency_key,
        extra_metadata=extra_metadata or {},
        status=InvoiceStatus.EXTRACTED,
        extraction_source=extracted.source,
        extraction_confidence=extracted.confidence,
        fingerprint=compute_fingerprint(
            extracted.vendor_name, extracted.invoice_number, extracted.grand_total
        ),
    )
    for idx, li in enumerate(extracted.line_items, start=1):
        invoice.line_items.append(
            InvoiceLineItem(
                line_number=idx,
                description=li.description,
                quantity=li.quantity,
                unit_price=li.unit_price,
                line_total=li.total,
            )
        )
    return invoice
