"""Unit tests for the Duplicate Invoice Detector."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from ap_invoice.schemas.tools import DuplicateCheckRequest, ExistingInvoice
from ap_invoice.services.duplicate_detector import detect_duplicates


def _candidates() -> list[ExistingInvoice]:
    return [
        ExistingInvoice(
            id="e1",
            vendor_name="ACME Incorporated",
            invoice_number="INV100",
            amount=Decimal("1020"),
            date=date(2026, 5, 30),
        )
    ]


def test_exact_duplicate() -> None:
    r = detect_duplicates(
        DuplicateCheckRequest(
            vendor_name="Acme Inc",
            invoice_number="INV-100",
            amount=Decimal("1000"),
            date=date(2026, 6, 1),
            candidates=_candidates(),
        )
    )
    assert r.is_duplicate
    assert r.matches[0].match_type == "exact"
    assert r.highest_confidence > 0.9


def test_near_duplicate_amount_within_tolerance() -> None:
    r = detect_duplicates(
        DuplicateCheckRequest(
            vendor_name="Acme Inc",
            invoice_number="DIFFERENT-1",
            amount=Decimal("1000"),
            date=date(2026, 6, 1),
            candidates=_candidates(),
            amount_tolerance_pct=Decimal("5"),
        )
    )
    assert not r.is_duplicate
    assert r.is_near_duplicate
    assert r.matches[0].match_type == "near"


def test_amount_outside_tolerance_no_match() -> None:
    r = detect_duplicates(
        DuplicateCheckRequest(
            vendor_name="Acme Inc",
            invoice_number="DIFFERENT-1",
            amount=Decimal("2000"),
            date=date(2026, 6, 1),
            candidates=_candidates(),
            amount_tolerance_pct=Decimal("5"),
        )
    )
    assert not r.is_duplicate
    assert not r.is_near_duplicate


def test_lookback_window_excludes_old() -> None:
    old = [
        ExistingInvoice(
            id="e2",
            vendor_name="Acme Inc",
            invoice_number="INV-100",
            amount=Decimal("1000"),
            date=date(2025, 1, 1),
        )
    ]
    r = detect_duplicates(
        DuplicateCheckRequest(
            vendor_name="Acme Inc",
            invoice_number="INV-100",
            amount=Decimal("1000"),
            date=date(2026, 6, 1),
            candidates=old,
            lookback_days=90,
        )
    )
    assert not r.is_duplicate
    assert any("older" in n.lower() for n in r.notes)


def test_no_candidates() -> None:
    r = detect_duplicates(
        DuplicateCheckRequest(
            vendor_name="Acme", invoice_number="X", amount=Decimal("1"), candidates=[]
        )
    )
    assert not r.is_duplicate and not r.is_near_duplicate
    assert r.highest_confidence == 0.0
