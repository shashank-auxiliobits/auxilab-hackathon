"""Unit tests for the rule-based policy engine."""

from __future__ import annotations

from decimal import Decimal

from ap_invoice.core.enums import ApprovalDecision
from ap_invoice.schemas.tools import (
    CompletenessRequest,
    DuplicateCheckResult,
    PolicySnapshot,
)
from ap_invoice.services.completeness import check_completeness
from ap_invoice.services.policy_engine import evaluate_policy

_CLEAN_COMPLETENESS = check_completeness(
    CompletenessRequest(
        fields={
            "invoice_number": "INV-1",
            "invoice_date": "2026-06-01",
            "vendor_name": "Acme",
            "grand_total": Decimal("100"),
        }
    )
)
_NO_DUP = DuplicateCheckResult(is_duplicate=False, is_near_duplicate=False, highest_confidence=0.0)


def test_auto_approve_when_clean_and_under_limit() -> None:
    ev = evaluate_policy(
        policy=PolicySnapshot(auto_approve_max_amount=Decimal("5000")),
        amount=Decimal("100"),
        completeness=_CLEAN_COMPLETENESS,
        duplicates=_NO_DUP,
        vendor_recognized=True,
    )
    assert ev.decision == ApprovalDecision.AUTO_APPROVE
    assert ev.confidence > 0.8


def test_reject_on_exact_duplicate() -> None:
    ev = evaluate_policy(
        policy=PolicySnapshot(auto_approve_max_amount=Decimal("5000")),
        amount=Decimal("100"),
        completeness=_CLEAN_COMPLETENESS,
        duplicates=DuplicateCheckResult(
            is_duplicate=True, is_near_duplicate=False, highest_confidence=0.95
        ),
        vendor_recognized=True,
    )
    assert ev.decision == ApprovalDecision.REJECT


def test_hold_above_review_threshold() -> None:
    ev = evaluate_policy(
        policy=PolicySnapshot(
            auto_approve_max_amount=Decimal("5000"),
            requires_review_above_amount=Decimal("10000"),
        ),
        amount=Decimal("25000"),
        completeness=_CLEAN_COMPLETENESS,
        duplicates=_NO_DUP,
        vendor_recognized=True,
    )
    assert ev.decision == ApprovalDecision.HOLD


def test_flag_on_incomplete() -> None:
    incomplete = check_completeness(CompletenessRequest(fields={"invoice_number": "INV-1"}))
    ev = evaluate_policy(
        policy=PolicySnapshot(auto_approve_max_amount=Decimal("5000")),
        amount=Decimal("100"),
        completeness=incomplete,
        duplicates=_NO_DUP,
        vendor_recognized=True,
    )
    assert ev.decision == ApprovalDecision.FLAG


def test_hold_when_vendor_unrecognised() -> None:
    ev = evaluate_policy(
        policy=PolicySnapshot(auto_approve_max_amount=Decimal("5000")),
        amount=Decimal("100"),
        completeness=_CLEAN_COMPLETENESS,
        duplicates=_NO_DUP,
        vendor_recognized=False,
    )
    assert ev.decision == ApprovalDecision.HOLD


def test_hold_when_no_auto_approve_limit() -> None:
    ev = evaluate_policy(
        policy=PolicySnapshot(),
        amount=Decimal("100"),
        completeness=_CLEAN_COMPLETENESS,
        duplicates=_NO_DUP,
        vendor_recognized=True,
    )
    assert ev.decision == ApprovalDecision.HOLD
