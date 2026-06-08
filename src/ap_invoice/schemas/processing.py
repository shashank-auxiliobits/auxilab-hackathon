"""Schemas for the end-to-end processing pipeline and the audit trail."""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any

from pydantic import Field, model_validator

from ap_invoice.core.enums import ApprovalDecision, InvoiceStatus, ProcessingEventType
from ap_invoice.schemas.common import APIModel, ORMModel
from ap_invoice.schemas.tools import (
    CompletenessResult,
    DuplicateCheckResult,
    PaymentTermsResult,
    PolicyEvaluation,
    VendorNormaliseResult,
)


class ProcessRequest(APIModel):
    """Process an invoice end-to-end (GLM OCR extract → validate → LLM decide)."""

    raw_text: str | None = Field(default=None, description="Raw invoice text, if available.")
    file_base64: str | None = Field(
        default=None, description="Base64-encoded invoice file (image or PDF) for GLM OCR."
    )
    content_type: str | None = Field(
        default=None,
        max_length=128,
        description="MIME type of file_base64, e.g. 'image/png' or 'application/pdf'.",
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
        if not (self.raw_text and self.raw_text.strip()) and not self.file_base64:
            raise ValueError("Provide raw_text or file_base64.")
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
