"""Unit tests for the deterministic invoice extractor."""

from __future__ import annotations

import asyncio
from datetime import date
from decimal import Decimal

from ap_invoice.core.enums import ExtractionSource
from ap_invoice.services.extraction import extract_invoice
from ap_invoice.services.extraction.deterministic import extract_deterministic

SAMPLE = """ACME WIDGETS LLC
Invoice Number: INV-2026-042
Invoice Date: 2026-06-01
Due Date: 2026-07-01
Payment Terms: 2/10 Net 30
Widget A    2    50.00    100.00
Subtotal: $100.00
Tax (10%): $10.00
Grand Total: $110.00
"""


def test_deterministic_extraction() -> None:
    r = extract_deterministic(SAMPLE)
    assert r.invoice_number == "INV-2026-042"
    assert r.invoice_date == date(2026, 6, 1)
    assert r.due_date == date(2026, 7, 1)
    assert r.grand_total == Decimal("110.00")
    assert r.subtotal == Decimal("100.00")
    assert r.tax == Decimal("10.00")
    assert r.payment_terms is not None
    assert r.source == ExtractionSource.DETERMINISTIC
    assert "invoice_number" in r.confidence
    assert len(r.line_items) == 1
    assert r.line_items[0].quantity == Decimal("2")


def test_engine_router_deterministic() -> None:
    r = asyncio.run(extract_invoice(SAMPLE, engine="deterministic"))
    assert r.invoice_number == "INV-2026-042"


def test_hybrid_without_llm_falls_back() -> None:
    # No API key configured in the test env → hybrid degrades to deterministic.
    r = asyncio.run(extract_invoice(SAMPLE, engine="hybrid"))
    assert r.source == ExtractionSource.DETERMINISTIC
    assert r.grand_total == Decimal("110.00")


def test_grand_total_inferred_when_unlabelled() -> None:
    r = extract_deterministic("Some vendor\nAmount 5\nBig number 999.99\n")
    assert r.grand_total == Decimal("999.99")
