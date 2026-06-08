"""Unit tests for the deterministic policy-compiler fallback."""

from __future__ import annotations

from ap_invoice.services.policy_compiler import _compile_deterministic

POLICY = """
ACME SUPPLY CO — VENDOR POLICY

Payment terms are Net 30. Early payment discount of 2/10 Net 30 may apply.
Invoices must not exceed $50,000 without prior written approval.
A valid purchase order is required for all invoices.
All invoices must be issued in USD.
"""


def test_extracts_payment_terms() -> None:
    rules = _compile_deterministic(POLICY)
    terms = next((r for r in rules if r.rule_type == "allowed_payment_terms"), None)
    assert terms is not None
    assert any("net 30" in t.lower() for t in (terms.payment_terms or []))


def test_extracts_max_amount() -> None:
    rules = _compile_deterministic(POLICY)
    cap = next((r for r in rules if r.rule_type == "max_invoice_amount"), None)
    assert cap is not None
    assert cap.amount == 50000.0


def test_extracts_requires_po() -> None:
    rules = _compile_deterministic(POLICY)
    assert any(r.rule_type == "requires_purchase_order" for r in rules)


def test_extracts_currency() -> None:
    rules = _compile_deterministic(POLICY)
    cur = next((r for r in rules if r.rule_type == "currency"), None)
    assert cur is not None
    assert cur.currency == "USD"


def test_empty_policy_yields_no_rules() -> None:
    assert _compile_deterministic("This document contains no enforceable terms.") == []
