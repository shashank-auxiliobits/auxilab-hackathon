"""Invoice and line-item schemas."""

from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from pydantic import Field, model_validator

from ap_invoice.core.enums import ApprovalDecision, ExtractionSource, InvoiceStatus
from ap_invoice.schemas.common import APIModel, InvoiceFileInput, ORMModel

# Hard upper bound on files per request (see schemas.processing for rationale).
_MAX_FILES_PER_REQUEST = 50


class LineItemIn(APIModel):
    line_number: int = Field(default=1, ge=1)
    description: str | None = None
    quantity: Decimal | None = Field(default=None, ge=0)
    unit_price: Decimal | None = Field(default=None, ge=0)
    line_total: Decimal | None = Field(default=None)


class LineItemRead(ORMModel):
    id: uuid.UUID
    line_number: int
    description: str | None
    quantity: Decimal | None
    unit_price: Decimal | None
    line_total: Decimal | None


class InvoiceCreate(APIModel):
    """Create an invoice from already-known fields (e.g. from a prior extraction)."""

    raw_vendor_name: str | None = Field(default=None, max_length=255)
    vendor_id: uuid.UUID | None = None
    invoice_number: str | None = Field(default=None, max_length=128)
    invoice_date: date | None = None
    due_date: date | None = None
    currency: str | None = Field(default=None, min_length=3, max_length=3)
    subtotal: Decimal | None = None
    tax: Decimal | None = None
    grand_total: Decimal | None = None
    payment_terms: str | None = Field(default=None, max_length=64)
    raw_text: str | None = None
    source: str | None = Field(default=None, max_length=64)
    idempotency_key: str | None = Field(
        default=None,
        max_length=128,
        description="Provide to make re-ingestion of the same document a no-op.",
    )
    line_items: list[LineItemIn] = Field(default_factory=list)
    extra_metadata: dict[str, Any] = Field(default_factory=dict)


class InvoiceIngest(APIModel):
    """Ingest an invoice (text and/or a file) for GLM OCR extraction + processing."""

    raw_text: str | None = Field(default=None, description="Raw invoice text, if available.")
    file_base64: str | None = Field(
        default=None,
        description="Base64-encoded invoice file (image or PDF). For a single file; "
        "for multi-page or multi-file invoices use `files` instead (or in addition).",
    )
    content_type: str | None = Field(
        default=None,
        max_length=128,
        description="MIME type of file_base64, e.g. 'image/png' or 'application/pdf'.",
    )
    files: list[InvoiceFileInput] = Field(
        default_factory=list,
        max_length=_MAX_FILES_PER_REQUEST,
        description="One or more invoice files (pages or attachments) extracted together "
        "as a single invoice. Combined with `file_base64` if both are provided.",
    )
    source: str | None = Field(default="api", max_length=64)
    idempotency_key: str | None = Field(default=None, max_length=128)
    extra_metadata: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _require_text_or_file(self) -> InvoiceIngest:
        has_text = bool(self.raw_text and self.raw_text.strip())
        if not has_text and not self.file_base64 and not self.files:
            raise ValueError("Provide raw_text, file_base64, or files.")
        return self


class InvoiceRead(ORMModel):
    id: uuid.UUID
    organization_id: uuid.UUID
    vendor_id: uuid.UUID | None
    raw_vendor_name: str | None
    invoice_number: str | None
    invoice_date: date | None
    due_date: date | None
    currency: str | None
    subtotal: Decimal | None
    tax: Decimal | None
    grand_total: Decimal | None
    payment_terms: str | None
    source: str | None
    idempotency_key: str | None
    fingerprint: str | None
    status: InvoiceStatus
    recommended_action: ApprovalDecision | None
    completeness_score: Decimal | None
    extraction_source: ExtractionSource | None
    extraction_confidence: dict[str, Any]
    extra_metadata: dict[str, Any]
    created_at: datetime
    updated_at: datetime


class InvoiceDetail(InvoiceRead):
    line_items: list[LineItemRead] = Field(default_factory=list)
