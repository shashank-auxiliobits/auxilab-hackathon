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

    LLM = "llm"
    DETERMINISTIC = "deterministic"
    HYBRID = "hybrid"
    MANUAL = "manual"
