"""Schemas for the end-to-end processing pipeline and the audit trail."""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any

from pydantic import Field, model_validator

from ap_invoice.core.enums import ApprovalDecision, InvoiceStatus, ProcessingEventType
from ap_invoice.schemas.common import APIModel, InvoiceFileInput, ORMModel
from ap_invoice.schemas.tools import (
    CompletenessResult,
    DuplicateCheckResult,
    PaymentTermsResult,
    PolicyEvaluation,
    VendorNormaliseResult,
)

# Hard upper bound on files per request — a cheap guard against absurd payloads.
# The operationally-tuned limit is enforced at decode time (see services.extraction.files).
_MAX_FILES_PER_REQUEST = 50


class ProcessRequest(APIModel):
    """Process an invoice end-to-end (vision OCR extract → validate → LLM decide)."""

    raw_text: str | None = Field(
        default=None, max_length=200_000, description="Raw invoice text, if available."
    )
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
    actor: str = Field(
        default="agent",
        max_length=255,
        description="Who initiated processing, recorded in the audit trail.",
    )
    auto_onboard: bool = Field(
        default=True,
        description="Auto-create an unrecognised vendor (as 'onboarding') so processing "
        "doesn't halt; the invoice still holds for review until the vendor is trusted.",
    )
    extra_metadata: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _require_text_or_file(self) -> ProcessRequest:
        has_text = bool(self.raw_text and self.raw_text.strip())
        if not has_text and not self.file_base64 and not self.files:
            raise ValueError("Provide raw_text, file_base64, or files.")
        return self


class ProcessResult(APIModel):
    """The full, auditable outcome of processing one invoice."""

    invoice_id: uuid.UUID
    status: InvoiceStatus
    decision: ApprovalDecision
    confidence: float
    completeness_score: Decimal | None = None
    summary: str
    reasons: list[str] = Field(default_factory=list)
    vendor: VendorNormaliseResult | None = None
    completeness: CompletenessResult | None = None
    duplicates: DuplicateCheckResult | None = None
    payment_terms: PaymentTermsResult | None = None
    policy: PolicyEvaluation


class InvoiceStats(APIModel):
    """Aggregated invoice counts for an organization."""

    total_invoices: int
    total_amount: Decimal
    by_status: dict[str, int] = Field(
        description="Count of invoices per status (approved, held, flagged, rejected, ...)."
    )


class ProcessingEventRead(ORMModel):
    id: uuid.UUID
    invoice_id: uuid.UUID | None
    event_type: ProcessingEventType
    actor: str
    tool_name: str | None
    decision: str | None
    message: str | None
    details: dict[str, Any]
    created_at: datetime
