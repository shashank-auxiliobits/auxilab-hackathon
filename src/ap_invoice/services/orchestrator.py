"""End-to-end invoice processing pipeline.

Runs an already-extracted invoice through vendor normalisation, completeness,
duplicate detection, payment-terms parsing, and the rule-based policy engine,
then sets the invoice's status and recommended action — writing an immutable
:class:`ProcessingEvent` for every step so the decision is fully auditable.

This is the deterministic backbone the AI agent orchestrates: the agent calls
it (via REST or MCP), reads the explained verdict, and acts.
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ap_invoice.core.enums import (
    ApprovalDecision,
    InvoiceStatus,
    ProcessingEventType,
)
from ap_invoice.models.audit import ProcessingEvent
from ap_invoice.models.invoice import Invoice
from ap_invoice.models.organization import Organization
from ap_invoice.models.vendor import Vendor, VendorPolicy
from ap_invoice.schemas.processing import ProcessResult
from ap_invoice.schemas.tools import (
    CompletenessRequest,
    DuplicateCheckRequest,
    ExistingInvoice,
    PaymentTermsRequest,
    PolicySnapshot,
    VendorMasterEntry,
    VendorNormaliseRequest,
)
from ap_invoice.services.completeness import check_completeness
from ap_invoice.services.duplicate_detector import detect_duplicates
from ap_invoice.services.payment_terms import calculate_payment_terms
from ap_invoice.services.policy_engine import evaluate_policy
from ap_invoice.services.vendor_normaliser import normalise_vendor

_DUP_CANDIDATE_LIMIT = 1000

# How a policy decision maps to the persisted invoice status.
_DECISION_STATUS = {
    ApprovalDecision.AUTO_APPROVE: InvoiceStatus.APPROVED,
    ApprovalDecision.HOLD: InvoiceStatus.HELD,
    ApprovalDecision.FLAG: InvoiceStatus.FLAGGED,
    ApprovalDecision.REJECT: InvoiceStatus.REJECTED,
}


def _policy_snapshot(policy: VendorPolicy | None) -> PolicySnapshot:
    if policy is None:
        return PolicySnapshot()
    return PolicySnapshot(
        payment_terms=policy.payment_terms,
        currency=policy.currency,
        mandatory_fields=policy.mandatory_fields,
        min_completeness_score=policy.min_completeness_score,
        auto_approve_max_amount=policy.auto_approve_max_amount,
        requires_review_above_amount=policy.requires_review_above_amount,
        amount_tolerance_pct=policy.amount_tolerance_pct,
        duplicate_lookback_days=policy.duplicate_lookback_days,
        allow_early_payment_discount=policy.allow_early_payment_discount,
    )


def _invoice_fields(invoice: Invoice, vendor_name: str | None) -> dict[str, Any]:
    return {
        "invoice_number": invoice.invoice_number,
        "invoice_date": invoice.invoice_date,
        "due_date": invoice.due_date,
        "vendor_name": vendor_name or invoice.raw_vendor_name,
        "currency": invoice.currency,
        "subtotal": invoice.subtotal,
        "tax": invoice.tax,
        "grand_total": invoice.grand_total,
        "payment_terms": invoice.payment_terms,
    }


def _event(
    org_id: uuid.UUID,
    invoice_id: uuid.UUID,
    actor: str,
    event_type: ProcessingEventType,
    message: str,
    *,
    tool_name: str | None = None,
    decision: str | None = None,
    details: dict[str, Any] | None = None,
) -> ProcessingEvent:
    return ProcessingEvent(
        organization_id=org_id,
        invoice_id=invoice_id,
        event_type=event_type,
        actor=actor,
        tool_name=tool_name,
        decision=decision,
        message=message,
        details=details or {},
    )


async def _active_policy(db: AsyncSession, vendor_id: uuid.UUID) -> VendorPolicy | None:
    return (
        await db.execute(
            select(VendorPolicy).where(
                VendorPolicy.vendor_id == vendor_id, VendorPolicy.is_active.is_(True)
            )
        )
    ).scalar_one_or_none()


async def process_invoice(
    db: AsyncSession,
    org: Organization,
    invoice: Invoice,
    *,
    actor: str = "agent",
) -> ProcessResult:
    """Run the full policy pipeline on ``invoice`` and persist the verdict + audit trail."""
    events: list[ProcessingEvent] = []

    # --- 1. Vendor normalisation -------------------------------------------------
    vendor_rows = (
        (await db.execute(select(Vendor).where(Vendor.organization_id == org.id))).scalars().all()
    )
    vendor_result = (
        normalise_vendor(
            VendorNormaliseRequest(
                raw_name=invoice.raw_vendor_name or "",
                vendor_master=[
                    VendorMasterEntry(
                        id=str(v.id), canonical_name=v.canonical_name, aliases=v.aliases
                    )
                    for v in vendor_rows
                ],
            )
        )
        if invoice.raw_vendor_name
        else None
    )

    canonical_vendor_name: str | None = None
    if vendor_result and vendor_result.match and vendor_result.match.vendor_id:
        invoice.vendor_id = uuid.UUID(vendor_result.match.vendor_id)
        canonical_vendor_name = vendor_result.match.canonical_name
    events.append(
        _event(
            org.id,
            invoice.id,
            actor,
            ProcessingEventType.VENDOR_MATCHED,
            (
                f"Matched vendor '{canonical_vendor_name}'."
                if canonical_vendor_name
                else "Vendor not recognised; flagged for onboarding."
            ),
            tool_name="vendor_normaliser",
            details=vendor_result.model_dump(mode="json") if vendor_result else {},
        )
    )

    # --- 2. Resolve the governing policy ----------------------------------------
    policy_model = await _active_policy(db, invoice.vendor_id) if invoice.vendor_id else None
    policy = _policy_snapshot(policy_model)

    # --- 3. Completeness ---------------------------------------------------------
    completeness = check_completeness(
        CompletenessRequest(
            fields=_invoice_fields(invoice, canonical_vendor_name),
            mandatory_fields=policy.mandatory_fields,
            process_threshold=policy.min_completeness_score,
        )
    )
    invoice.completeness_score = completeness.completeness_score
    events.append(
        _event(
            org.id,
            invoice.id,
            actor,
            ProcessingEventType.COMPLETENESS_CHECK,
            f"Completeness {completeness.completeness_score}% "
            f"→ {completeness.recommended_action.value}.",
            tool_name="completeness_checker",
            details=completeness.model_dump(mode="json"),
        )
    )

    # --- 4. Duplicate detection --------------------------------------------------
    candidate_rows = (
        (
            await db.execute(
                select(Invoice)
                .where(Invoice.organization_id == org.id, Invoice.id != invoice.id)
                .order_by(Invoice.created_at.desc())
                .limit(_DUP_CANDIDATE_LIMIT)
            )
        )
        .scalars()
        .all()
    )
    duplicates = detect_duplicates(
        DuplicateCheckRequest(
            vendor_name=canonical_vendor_name or invoice.raw_vendor_name,
            invoice_number=invoice.invoice_number,
            amount=invoice.grand_total,
            date=invoice.invoice_date,
            amount_tolerance_pct=policy.amount_tolerance_pct,
            lookback_days=policy.duplicate_lookback_days,
            candidates=[
                ExistingInvoice(
                    id=str(i.id),
                    vendor_name=i.raw_vendor_name,
                    invoice_number=i.invoice_number,
                    amount=i.grand_total,
                    date=i.invoice_date,
                )
                for i in candidate_rows
            ],
        )
    )
    events.append(
        _event(
            org.id,
            invoice.id,
            actor,
            ProcessingEventType.DUPLICATE_CHECK,
            duplicates.notes[0] if duplicates.notes else "Duplicate check complete.",
            tool_name="duplicate_detector",
            details=duplicates.model_dump(mode="json"),
        )
    )

    # --- 5. Payment terms --------------------------------------------------------
    payment_terms = None
    terms_str = invoice.payment_terms or policy.payment_terms
    if invoice.invoice_date and terms_str:
        payment_terms = calculate_payment_terms(
            PaymentTermsRequest(
                invoice_date=invoice.invoice_date,
                payment_terms=terms_str,
                amount=invoice.grand_total,
            )
        )
        if payment_terms.due_date and invoice.due_date is None:
            invoice.due_date = payment_terms.due_date
        events.append(
            _event(
                org.id,
                invoice.id,
                actor,
                ProcessingEventType.PAYMENT_TERMS_CALCULATED,
                f"Payment terms '{terms_str}' → due {payment_terms.due_date}.",
                tool_name="payment_terms_calculator",
                details=payment_terms.model_dump(mode="json"),
            )
        )

    # --- 6. Policy evaluation ----------------------------------------------------
    evaluation = evaluate_policy(
        policy=policy,
        amount=invoice.grand_total,
        completeness=completeness,
        duplicates=duplicates,
        payment_terms=payment_terms,
        vendor_recognized=(vendor_result.is_recognized if vendor_result else False),
    )
    events.append(
        _event(
            org.id,
            invoice.id,
            actor,
            ProcessingEventType.POLICY_EVALUATED,
            evaluation.summary,
            tool_name="policy_engine",
            decision=evaluation.decision.value,
            details=evaluation.model_dump(mode="json"),
        )
    )

    # --- 7. Persist decision + status -------------------------------------------
    previous_status = invoice.status
    invoice.recommended_action = evaluation.decision
    invoice.status = _DECISION_STATUS[evaluation.decision]
    events.append(
        _event(
            org.id,
            invoice.id,
            actor,
            ProcessingEventType.DECISION,
            evaluation.summary,
            decision=evaluation.decision.value,
            details={"reasons": evaluation.reasons},
        )
    )
    events.append(
        _event(
            org.id,
            invoice.id,
            actor,
            ProcessingEventType.STATUS_CHANGED,
            f"Status {previous_status.value} → {invoice.status.value}.",
            details={"from": previous_status.value, "to": invoice.status.value},
        )
    )

    db.add_all(events)
    await db.flush()

    return ProcessResult(
        invoice_id=invoice.id,
        status=invoice.status,
        decision=evaluation.decision,
        confidence=evaluation.confidence,
        completeness_score=invoice.completeness_score,
        summary=evaluation.summary,
        reasons=evaluation.reasons,
        vendor=vendor_result,
        completeness=completeness,
        duplicates=duplicates,
        payment_terms=payment_terms,
        policy=evaluation,
    )
