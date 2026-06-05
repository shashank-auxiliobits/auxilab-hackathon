"""Unit tests for the Invoice Completeness Checker."""

from __future__ import annotations

from decimal import Decimal

from ap_invoice.core.enums import CompletenessAction
from ap_invoice.schemas.tools import CompletenessRequest
from ap_invoice.services.completeness import check_completeness


def test_all_present_process() -> None:
    r = check_completeness(
        CompletenessRequest(
            fields={
                "invoice_number": "INV-1",
                "invoice_date": "2026-06-01",
                "vendor_name": "Acme",
                "grand_total": Decimal("100"),
            }
        )
    )
    assert r.completeness_score == Decimal("100.00")
    assert r.missing_fields == []
    assert r.recommended_action == CompletenessAction.PROCESS


def test_one_missing_hold() -> None:
    r = check_completeness(
        CompletenessRequest(
            fields={"invoice_number": "INV-1", "invoice_date": "2026-06-01", "vendor_name": "Acme"}
        )
    )
    assert r.completeness_score == Decimal("75.00")
    assert r.missing_fields == ["grand_total"]
    assert r.recommended_action == CompletenessAction.HOLD


def test_mostly_missing_return() -> None:
    r = check_completeness(CompletenessRequest(fields={"invoice_number": "INV-1"}))
    assert r.completeness_score == Decimal("25.00")
    assert r.recommended_action == CompletenessAction.RETURN_TO_VENDOR


def test_empty_string_counts_missing() -> None:
    r = check_completeness(
        CompletenessRequest(
            fields={
                "invoice_number": "  ",
                "invoice_date": None,
                "vendor_name": "Acme",
                "grand_total": 0,
            }
        )
    )
    assert "invoice_number" in r.missing_fields
    assert "invoice_date" in r.missing_fields
    # 0 is a present value (not empty).
    assert "grand_total" not in r.missing_fields


def test_no_mandatory_fields_is_100() -> None:
    r = check_completeness(CompletenessRequest(fields={}, mandatory_fields=[]))
    assert r.completeness_score == Decimal("100.00")
    assert r.recommended_action == CompletenessAction.PROCESS
