"""Schemas for vendor policy documents, compiled rules, and status transitions."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Literal

from pydantic import Field

from ap_invoice.core.enums import (
    DocumentStatus,
    InvoiceStatus,
    PolicyRuleStatus,
    PolicyRuleType,
)
from ap_invoice.schemas.common import APIModel, ORMModel


class DocumentUpload(APIModel):
    filename: str = Field(min_length=1, max_length=255)
    content_type: str | None = Field(default=None, max_length=100)
    text: str = Field(
        min_length=1,
        max_length=1_000_000,
        description="Extracted text of the policy document (max ~1 MB of characters).",
    )
    compile: bool = Field(default=True, description="Compile structured rules immediately.")
    engine: Literal["llm", "deterministic"] | None = None
    replace: bool = Field(
        default=False,
        description="Replace the vendor's existing policy: delete all prior documents, "
        "chunks, and compiled rules before storing this one, so decisions use the "
        "updated policy only.",
    )


class VendorDocumentRead(ORMModel):
    id: uuid.UUID
    vendor_id: uuid.UUID
    filename: str
    content_type: str | None
    status: DocumentStatus
    created_at: datetime


class PolicyRuleRead(ORMModel):
    id: uuid.UUID
    vendor_id: uuid.UUID
    document_id: uuid.UUID | None
    rule_type: PolicyRuleType
    parameters: dict[str, Any]
    description: str | None
    source_quote: str | None
    confidence: float | None
    status: PolicyRuleStatus
    created_at: datetime


class DocumentCompileResult(APIModel):
    document_id: uuid.UUID
    status: DocumentStatus
    rules: list[PolicyRuleRead]


class PolicySearchHit(APIModel):
    text: str
    score: float
    document_id: uuid.UUID


class StatusTransitionRequest(APIModel):
    status: InvoiceStatus
    actor: str = Field(default="agent", max_length=255)
    note: str | None = Field(default=None, max_length=2000)
