"""Schemas for the end-to-end processing pipeline and the audit trail."""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any

from pydantic import Field

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
    """Process raw invoice text end-to-end (extract → validate → decide)."""

    raw_text: str = Field(min_length=1)
    source: str | None = Field(default="api", max_length=64)
    idempotency_key: str | None = Field(default=None, max_length=128)
    actor: str = Field(
        default="agent",
        max_length=255,
        description="Who initiated processing, recorded in the audit trail.",
    )
    engine: str | None = Field(default=None, description="Override the extraction engine.")
    extra_metadata: dict[str, Any] = Field(default_factory=dict)


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
