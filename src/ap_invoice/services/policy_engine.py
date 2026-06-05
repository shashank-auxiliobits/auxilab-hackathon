"""Deterministic, rule-based policy / approval engine.

Combines the outputs of the individual tools (completeness, duplicate detection,
payment-terms parsing, vendor recognition) plus the vendor's amount thresholds
into a single, explainable decision: Auto-Approve / Hold / Flag / Reject.

The engine is intentionally deterministic and side-effect-free so that every
decision is reproducible and fully auditable — the AI agent orchestrates the
tools and acts on this verdict, but the verdict itself is rule-based.
"""

from __future__ import annotations

from decimal import Decimal

from ap_invoice.core.enums import ApprovalDecision, CompletenessAction
from ap_invoice.schemas.tools import (
    CompletenessResult,
    DuplicateCheckResult,
    PaymentTermsResult,
    PolicyCheck,
    PolicyEvaluation,
    PolicySnapshot,
)

# Severity ordering for picking the dominant decision driver.
_SEVERITY_RANK = {"info": 0, "warning": 1, "critical": 2}


def evaluate_policy(
    *,
    policy: PolicySnapshot,
    amount: Decimal | None = None,
    completeness: CompletenessResult | None = None,
    duplicates: DuplicateCheckResult | None = None,
    payment_terms: PaymentTermsResult | None = None,
    vendor_recognized: bool | None = None,
) -> PolicyEvaluation:
    """Evaluate an invoice against a vendor policy and return an auditable verdict."""
    checks: list[PolicyCheck] = []

    _check_duplicates(checks, duplicates)
    _check_completeness(checks, completeness, policy)
    _check_vendor(checks, vendor_recognized)
    _check_payment_terms(checks, payment_terms)
    _check_amount(checks, amount, policy)

    decision, reasons, summary = _decide(checks, amount, policy)
    confidence = _confidence(checks, decision)

    return PolicyEvaluation(
        decision=decision,
        confidence=confidence,
        checks=checks,
        reasons=reasons,
        summary=summary,
    )


# --------------------------------------------------------------------------- #
# Individual checks
# --------------------------------------------------------------------------- #


def _check_duplicates(checks: list[PolicyCheck], dup: DuplicateCheckResult | None) -> None:
    if dup is None:
        return
    if dup.is_duplicate:
        checks.append(
            PolicyCheck(
                name="duplicate",
                passed=False,
                severity="critical",
                message="Exact duplicate of an already-processed invoice.",
                details={"highest_confidence": dup.highest_confidence, "matches": len(dup.matches)},
            )
        )
    elif dup.is_near_duplicate:
        checks.append(
            PolicyCheck(
                name="duplicate",
                passed=False,
                severity="warning",
                message="Possible near-duplicate; review before processing.",
                details={"highest_confidence": dup.highest_confidence, "matches": len(dup.matches)},
            )
        )
    else:
        checks.append(
            PolicyCheck(
                name="duplicate", passed=True, severity="info", message="No duplicates found."
            )
        )


def _check_completeness(
    checks: list[PolicyCheck], comp: CompletenessResult | None, policy: PolicySnapshot
) -> None:
    if comp is None:
        return
    below_min = comp.completeness_score < policy.min_completeness_score
    if comp.recommended_action == CompletenessAction.RETURN_TO_VENDOR:
        checks.append(
            PolicyCheck(
                name="completeness",
                passed=False,
                severity="critical",
                message=f"Invoice incomplete ({comp.completeness_score}%); "
                f"missing: {', '.join(comp.missing_fields) or 'n/a'}.",
                details={"score": float(comp.completeness_score), "missing": comp.missing_fields},
            )
        )
    elif comp.recommended_action == CompletenessAction.HOLD or below_min:
        checks.append(
            PolicyCheck(
                name="completeness",
                passed=False,
                severity="warning",
                message=f"Completeness {comp.completeness_score}% below policy minimum "
                f"{policy.min_completeness_score}%.",
                details={"score": float(comp.completeness_score), "missing": comp.missing_fields},
            )
        )
    else:
        checks.append(
            PolicyCheck(
                name="completeness",
                passed=True,
                severity="info",
                message=f"All mandatory fields present ({comp.completeness_score}%).",
            )
        )


def _check_vendor(checks: list[PolicyCheck], vendor_recognized: bool | None) -> None:
    if vendor_recognized is None:
        return
    if vendor_recognized:
        checks.append(
            PolicyCheck(name="vendor", passed=True, severity="info", message="Vendor recognised.")
        )
    else:
        checks.append(
            PolicyCheck(
                name="vendor",
                passed=False,
                severity="warning",
                message="Vendor not recognised; onboarding required before approval.",
            )
        )


def _check_payment_terms(checks: list[PolicyCheck], terms: PaymentTermsResult | None) -> None:
    if terms is None:
        return
    if not terms.parsed:
        checks.append(
            PolicyCheck(
                name="payment_terms",
                passed=False,
                severity="warning",
                message=f"Could not interpret payment terms ('{terms.raw_terms}').",
            )
        )
        return
    if terms.days_until_due is not None and terms.days_until_due < 0:
        checks.append(
            PolicyCheck(
                name="payment_terms",
                passed=True,
                severity="warning",
                message="Invoice is already past its due date.",
                details={"days_overdue": -terms.days_until_due},
            )
        )
    else:
        checks.append(
            PolicyCheck(
                name="payment_terms",
                passed=True,
                severity="info",
                message=f"Payment terms parsed ({terms.term_type}).",
            )
        )


def _check_amount(
    checks: list[PolicyCheck], amount: Decimal | None, policy: PolicySnapshot
) -> None:
    if amount is None:
        checks.append(
            PolicyCheck(
                name="amount",
                passed=False,
                severity="warning",
                message="Invoice amount missing; cannot apply thresholds.",
            )
        )
        return
    if (
        policy.requires_review_above_amount is not None
        and amount > policy.requires_review_above_amount
    ):
        checks.append(
            PolicyCheck(
                name="amount",
                passed=False,
                severity="warning",
                message=f"Amount {amount} exceeds the manual-review threshold "
                f"{policy.requires_review_above_amount}.",
                details={"amount": float(amount)},
            )
        )
    else:
        checks.append(
            PolicyCheck(
                name="amount",
                passed=True,
                severity="info",
                message=f"Amount {amount} within policy thresholds.",
                details={"amount": float(amount)},
            )
        )


# --------------------------------------------------------------------------- #
# Decision
# --------------------------------------------------------------------------- #


def _decide(
    checks: list[PolicyCheck], amount: Decimal | None, policy: PolicySnapshot
) -> tuple[ApprovalDecision, list[str], str]:
    criticals = [c for c in checks if not c.passed and c.severity == "critical"]
    warnings = [c for c in checks if not c.passed and c.severity == "warning"]
    reasons = [c.message for c in checks if not c.passed]

    if criticals:
        # An exact duplicate is a hard reject; other critical failures are flagged.
        is_dup = any(c.name == "duplicate" for c in criticals)
        decision = ApprovalDecision.REJECT if is_dup else ApprovalDecision.FLAG
        summary = (
            "Rejected: exact duplicate invoice."
            if is_dup
            else f"Flagged: {len(criticals)} critical policy violation(s)."
        )
        return decision, reasons, summary

    if warnings:
        return (
            ApprovalDecision.HOLD,
            reasons,
            f"Hold for review: {len(warnings)} item(s) need attention.",
        )

    # All checks passed — decide auto-approve vs hold based on amount thresholds.
    if amount is not None and policy.auto_approve_max_amount is not None:
        if amount <= policy.auto_approve_max_amount:
            return (
                ApprovalDecision.AUTO_APPROVE,
                [],
                f"Auto-approved: clean invoice within auto-approve limit "
                f"({policy.auto_approve_max_amount}).",
            )
        return (
            ApprovalDecision.HOLD,
            [f"Amount {amount} exceeds auto-approve limit {policy.auto_approve_max_amount}."],
            "Hold: clean but above the auto-approve limit.",
        )

    # No auto-approve threshold configured → conservative manual review.
    return (
        ApprovalDecision.HOLD,
        ["No auto-approve threshold configured for this vendor."],
        "Hold: clean invoice, but vendor has no auto-approve limit set.",
    )


def _confidence(checks: list[PolicyCheck], decision: ApprovalDecision) -> float:
    if not checks:
        return 0.5
    worst = max((_SEVERITY_RANK[c.severity] for c in checks if not c.passed), default=0)
    passed = sum(1 for c in checks if c.passed)
    base = passed / len(checks)
    # Clear-cut decisions (auto-approve / hard reject) are reported with high confidence.
    if decision in (ApprovalDecision.AUTO_APPROVE, ApprovalDecision.REJECT):
        return round(min(1.0, 0.85 + 0.15 * base), 4)
    penalty = 0.15 * worst
    return round(max(0.3, base - penalty + 0.4), 4)
