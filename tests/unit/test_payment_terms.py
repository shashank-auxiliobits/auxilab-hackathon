"""Unit tests for the Payment Terms Calculator."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from ap_invoice.schemas.tools import PaymentTermsRequest
from ap_invoice.services.payment_terms import calculate_payment_terms


def test_net_30() -> None:
    r = calculate_payment_terms(
        PaymentTermsRequest(invoice_date=date(2026, 6, 1), payment_terms="Net 30")
    )
    assert r.parsed and r.term_type == "net"
    assert r.net_days == 30
    assert r.due_date == date(2026, 7, 1)


def test_discount_2_10_net_30() -> None:
    r = calculate_payment_terms(
        PaymentTermsRequest(
            invoice_date=date(2026, 6, 1),
            payment_terms="2/10 Net 30",
            amount=Decimal("1000"),
            as_of=date(2026, 6, 5),
        )
    )
    assert r.term_type == "discount"
    assert r.discount_percent == Decimal("2")
    assert r.discount_deadline == date(2026, 6, 11)
    assert r.due_date == date(2026, 7, 1)
    assert r.discount_amount == Decimal("20.00")
    assert r.amount_after_discount == Decimal("980.00")
    assert r.days_until_discount_deadline == 6
    assert len(r.milestones) == 2


def test_due_on_receipt() -> None:
    r = calculate_payment_terms(
        PaymentTermsRequest(invoice_date=date(2026, 6, 1), payment_terms="Due on Receipt")
    )
    assert r.term_type == "due_on_receipt"
    assert r.due_date == date(2026, 6, 1)


def test_cod() -> None:
    r = calculate_payment_terms(
        PaymentTermsRequest(invoice_date=date(2026, 6, 1), payment_terms="COD")
    )
    assert r.term_type == "cod"


def test_eom() -> None:
    r = calculate_payment_terms(
        PaymentTermsRequest(invoice_date=date(2026, 2, 10), payment_terms="Net 15 EOM")
    )
    assert r.term_type == "eom"
    # End of Feb 2026 (28th) + 15 days.
    assert r.due_date == date(2026, 3, 15)


def test_unparseable() -> None:
    r = calculate_payment_terms(
        PaymentTermsRequest(invoice_date=date(2026, 6, 1), payment_terms="whenever you can")
    )
    assert not r.parsed
    assert r.term_type == "unknown"
    assert r.due_date is None


def test_past_due_note() -> None:
    r = calculate_payment_terms(
        PaymentTermsRequest(
            invoice_date=date(2026, 1, 1), payment_terms="Net 30", as_of=date(2026, 6, 1)
        )
    )
    assert r.days_until_due is not None and r.days_until_due < 0
    assert any("past" in n.lower() for n in r.notes)


@pytest.mark.parametrize(
    ("terms", "expected_type"),
    [
        ("net30", "net"),
        ("N45", "net"),
        ("1.5/15 Net 45", "discount"),
        ("Due Upon Receipt", "due_on_receipt"),
    ],
)
def test_term_variants(terms: str, expected_type: str) -> None:
    r = calculate_payment_terms(
        PaymentTermsRequest(invoice_date=date(2026, 6, 1), payment_terms=terms)
    )
    assert r.term_type == expected_type
