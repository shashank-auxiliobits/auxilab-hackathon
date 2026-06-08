"""Domain enumerations shared across the data, service, and API layers.

All enums subclass ``str`` so they serialise cleanly to JSON and persist as
human-readable values in PostgreSQL.
"""

from __future__ import annotations

from enum import StrEnum


class VendorStatus(StrEnum):
    """Lifecycle state of a vendor record."""

    ACTIVE = "active"
    ONBOARDING = "onboarding"  # detected on an invoice but not yet approved
    INACTIVE = "inactive"


class InvoiceStatus(StrEnum):
    """Lifecycle state of an invoice as it moves through the AP pipeline."""

    RECEIVED = "received"
    EXTRACTED = "extracted"
    NORMALIZED = "normalized"
    VALIDATED = "validated"
    APPROVED = "approved"
    HELD = "held"
    FLAGGED = "flagged"
    REJECTED = "rejected"
    PAID = "paid"


class CompletenessAction(StrEnum):
    """Recommended action emitted by the Invoice Completeness Checker."""

    PROCESS = "process"
    HOLD = "hold"
    RETURN_TO_VENDOR = "return_to_vendor"


class ApprovalDecision(StrEnum):
    """Decision emitted by the policy/approval engine."""

    AUTO_APPROVE = "auto_approve"
    HOLD = "hold"  # needs a human to review before approval
    FLAG = "flag"  # likely invalid / policy violation
    REJECT = "reject"  # hard policy failure


class ProcessingEventType(StrEnum):
    """Audit-trail event categories (append-only)."""

    INGESTED = "ingested"
    EXTRACTED = "extracted"
    VENDOR_MATCHED = "vendor_matched"
    DUPLICATE_CHECK = "duplicate_check"
    COMPLETENESS_CHECK = "completeness_check"
    PAYMENT_TERMS_CALCULATED = "payment_terms_calculated"
    POLICY_EVALUATED = "policy_evaluated"
    DECISION = "decision"
    STATUS_CHANGED = "status_changed"
    NOTE = "note"


class ExtractionSource(StrEnum):
    """Which engine produced an extraction result."""

    OCR = "ocr"  # GLM vision OCR (current extraction path)
    LLM = "llm"
    DETERMINISTIC = "deterministic"  # legacy; retained for previously-stored rows
    HYBRID = "hybrid"  # legacy; retained for previously-stored rows
    MANUAL = "manual"


class DocumentStatus(StrEnum):
    """Lifecycle of an uploaded vendor policy document."""

    UPLOADED = "uploaded"
    CHUNKED = "chunked"
    COMPILED = "compiled"
    FAILED = "failed"


class PolicyRuleType(StrEnum):
    """Kinds of structured, machine-enforceable rules compiled from a policy doc."""

    MAX_INVOICE_AMOUNT = "max_invoice_amount"  # params: {amount}
    REQUIRE_FIELD = "require_field"  # params: {field}
    ALLOWED_PAYMENT_TERMS = "allowed_payment_terms"  # params: {terms: [...]}
    LINE_ITEM_PRICE_CAP = "line_item_price_cap"  # params: {keyword, max_unit_price}
    REQUIRES_PURCHASE_ORDER = "requires_purchase_order"  # params: {}
    CURRENCY = "currency"  # params: {currency}
    CUSTOM = "custom"  # params: {description} — advisory, not auto-enforceable


class PolicyRuleStatus(StrEnum):
    """Approval state of a compiled rule (a human/vendor confirms before enforcement)."""

    PROPOSED = "proposed"
    APPROVED = "approved"
    REJECTED = "rejected"
