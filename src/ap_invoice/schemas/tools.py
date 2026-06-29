"""Input/output schemas for the five MCP tools and the policy engine.

These are deliberately decoupled from the ORM: each tool operates on plain data
so it is pure, unit-testable, and reusable from the REST API, the MCP server,
and the agent orchestration layer alike.
"""

from __future__ import annotations

import datetime
from datetime import date
from decimal import Decimal
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from ap_invoice.core.enums import ApprovalDecision, CompletenessAction, ExtractionSource

# --------------------------------------------------------------------------- #
# Invoice Field Extractor
# --------------------------------------------------------------------------- #


class ExtractedLineItem(BaseModel):
    description: str | None = None
    quantity: Decimal | None = None
    unit_price: Decimal | None = None
    total: Decimal | None = None


class ExtractRequest(BaseModel):
    raw_text: str | None = Field(default=None, description="Raw invoice text, if available.")
    file_base64: str | None = Field(
        default=None, description="Base64-encoded invoice file (image or PDF) for vision OCR."
    )
    content_type: str | None = Field(
        default=None, description="MIME type of file_base64, e.g. 'image/png'."
    )


class ExtractedInvoice(BaseModel):
    invoice_number: str | None = None
    po_number: str | None = None
    vendor_name: str | None = None
    invoice_date: date | None = None
    due_date: date | None = None
    currency: str | None = None
    line_items: list[ExtractedLineItem] = Field(default_factory=list)
    subtotal: Decimal | None = None
    tax: Decimal | None = None
    grand_total: Decimal | None = None
    payment_terms: str | None = None
    # Per-field confidence in [0, 1]. Keys match the field names above.
    confidence: dict[str, float] = Field(default_factory=dict)
    source: ExtractionSource = ExtractionSource.OCR
    notes: list[str] = Field(default_factory=list)

    def as_fields(self) -> dict[str, Any]:
        """Flatten header fields into a dict for the completeness checker."""
        return {
            "invoice_number": self.invoice_number,
            "vendor_name": self.vendor_name,
            "invoice_date": self.invoice_date,
            "due_date": self.due_date,
            "currency": self.currency,
            "subtotal": self.subtotal,
            "tax": self.tax,
            "grand_total": self.grand_total,
            "payment_terms": self.payment_terms,
            "line_items": self.line_items,
        }


# --------------------------------------------------------------------------- #
# Payment Terms Calculator
# --------------------------------------------------------------------------- #


class PaymentTermsRequest(BaseModel):
    invoice_date: date
    payment_terms: str = Field(min_length=1, examples=["Net 30", "2/10 Net 30", "Due on Receipt"])
    amount: Decimal | None = Field(
        default=None, ge=0, description="Used to compute discount value."
    )
    as_of: date | None = Field(default=None, description="Reference date for 'days remaining'.")


class PaymentMilestone(BaseModel):
    label: str
    due_on: date
    days_remaining: int | None = None
    amount_due: Decimal | None = None


class PaymentTermsResult(BaseModel):
    raw_terms: str
    parsed: bool
    term_type: Literal["net", "discount", "due_on_receipt", "cod", "eom", "unknown"]
    net_days: int | None = None
    due_date: date | None = None
    discount_percent: Decimal | None = None
    discount_days: int | None = None
    discount_deadline: date | None = None
    discount_amount: Decimal | None = None
    amount_after_discount: Decimal | None = None
    days_until_due: int | None = None
    days_until_discount_deadline: int | None = None
    milestones: list[PaymentMilestone] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)


# --------------------------------------------------------------------------- #
# Invoice Completeness Checker
# --------------------------------------------------------------------------- #


class CompletenessRequest(BaseModel):
    fields: dict[str, Any] = Field(description="Extracted invoice fields keyed by name.")
    mandatory_fields: list[str] = Field(
        default_factory=lambda: [
            "invoice_number",
            "invoice_date",
            "vendor_name",
            "grand_total",
        ]
    )
    process_threshold: Decimal = Field(
        default=Decimal("100"), ge=0, le=100, description="Score at/above which to Process."
    )
    hold_threshold: Decimal = Field(
        default=Decimal("60"),
        ge=0,
        le=100,
        description="Score at/above which to Hold; below this, Return to Vendor.",
    )


class FieldStatus(BaseModel):
    field: str
    present: bool


class CompletenessResult(BaseModel):
    completeness_score: Decimal
    total_required: int
    present_count: int
    present_fields: list[str]
    missing_fields: list[str]
    field_statuses: list[FieldStatus]
    recommended_action: CompletenessAction
    notes: list[str] = Field(default_factory=list)


# --------------------------------------------------------------------------- #
# Vendor Name Normaliser
# --------------------------------------------------------------------------- #


class VendorMasterEntry(BaseModel):
    id: str | None = None
    canonical_name: str
    aliases: list[str] = Field(default_factory=list)


class VendorNormaliseRequest(BaseModel):
    raw_name: str = Field(min_length=1)
    vendor_master: list[VendorMasterEntry] = Field(default_factory=list)
    threshold: float = Field(default=85.0, ge=0, le=100, description="Fuzzy match cutoff (0-100).")
    suggestion_limit: int = Field(default=3, ge=0, le=20)


class VendorMatch(BaseModel):
    vendor_id: str | None = None
    canonical_name: str
    score: float
    match_type: Literal["exact", "alias", "fuzzy"]


class VendorNormaliseResult(BaseModel):
    raw_name: str
    normalized_query: str
    is_recognized: bool
    match: VendorMatch | None = None
    suggestions: list[VendorMatch] = Field(default_factory=list)
    recommend_onboarding: bool = False
    notes: list[str] = Field(default_factory=list)


# --------------------------------------------------------------------------- #
# Duplicate Invoice Detector
# --------------------------------------------------------------------------- #


class ExistingInvoice(BaseModel):
    id: str | None = None
    vendor_name: str | None = None
    invoice_number: str | None = None
    amount: Decimal | None = None
    # Annotated via the module to avoid shadowing by the field's own name.
    date: datetime.date | None = None


class DuplicateCheckRequest(BaseModel):
    vendor_name: str | None = None
    invoice_number: str | None = None
    amount: Decimal | None = None
    date: datetime.date | None = None
    candidates: list[ExistingInvoice] = Field(default_factory=list)
    amount_tolerance_pct: Decimal = Field(default=Decimal("5"), ge=0, le=100)
    vendor_fuzzy_threshold: float = Field(default=85.0, ge=0, le=100)
    lookback_days: int | None = Field(default=None, ge=0)


class DuplicateMatch(BaseModel):
    invoice_id: str | None = None
    invoice_number: str | None = None
    vendor_name: str | None = None
    amount: Decimal | None = None
    date: datetime.date | None = None
    match_type: Literal["exact", "near"]
    confidence: float
    reasons: list[str] = Field(default_factory=list)


class DuplicateCheckResult(BaseModel):
    is_duplicate: bool
    is_near_duplicate: bool
    highest_confidence: float
    matches: list[DuplicateMatch] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)


# --------------------------------------------------------------------------- #
# Policy / Approval Engine
# --------------------------------------------------------------------------- #


class PolicySnapshot(BaseModel):
    """A vendor policy flattened for the engine (decoupled from the ORM)."""

    payment_terms: str = "Net 30"
    currency: str = "USD"
    mandatory_fields: list[str] = Field(
        default_factory=lambda: ["invoice_number", "invoice_date", "vendor_name", "grand_total"]
    )
    min_completeness_score: Decimal = Decimal("100")
    auto_approve_max_amount: Decimal | None = None
    requires_review_above_amount: Decimal | None = None
    amount_tolerance_pct: Decimal = Decimal("5")
    duplicate_lookback_days: int = 90
    allow_early_payment_discount: bool = True


class PolicyRuleSnapshot(BaseModel):
    """An approved structured rule, flattened for the engine."""

    rule_type: str
    parameters: dict[str, Any] = Field(default_factory=dict)
    description: str | None = None


class PolicyLineItemContext(BaseModel):
    description: str | None = None
    unit_price: Decimal | None = None


class PolicyInvoiceContext(BaseModel):
    """Invoice facts the rule engine needs beyond the basic checks."""

    amount: Decimal | None = None
    currency: str | None = None
    payment_terms: str | None = None
    has_purchase_order: bool = False
    fields: dict[str, Any] = Field(default_factory=dict)
    line_items: list[PolicyLineItemContext] = Field(default_factory=list)


class PolicyCheck(BaseModel):
    name: str
    passed: bool
    severity: Literal["info", "warning", "critical"]
    message: str
    details: dict[str, Any] = Field(default_factory=dict)


class PolicyEvaluation(BaseModel):
    model_config = ConfigDict(use_enum_values=False)

    decision: ApprovalDecision
    confidence: float
    checks: list[PolicyCheck] = Field(default_factory=list)
    reasons: list[str] = Field(default_factory=list)
    summary: str
