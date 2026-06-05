"""Deterministic, offline invoice field extractor.

Pure regex/heuristic parsing — no network, fully reproducible. Used as the
fallback path of the hybrid engine and as the default when no LLM key is set.
Confidence scores reflect how the value was found (explicit label > heuristic).
"""

from __future__ import annotations

import re
from decimal import Decimal

from ap_invoice.core.enums import ExtractionSource
from ap_invoice.schemas.tools import ExtractedInvoice, ExtractedLineItem
from ap_invoice.services._parsing import (
    detect_currency,
    find_money_amounts,
    parse_date,
    parse_money,
)

_INVOICE_NO_RE = re.compile(
    r"invoice\s*(?:no\.?|number|num|#)?\s*[:#-]?\s*([A-Za-z0-9][A-Za-z0-9\-/]{2,})",
    re.IGNORECASE,
)
_INVOICE_DATE_RE = re.compile(
    r"(?:invoice\s*date|date\s*of\s*issue|issue\s*date|date)\s*[:#-]?\s*"
    r"([A-Za-z0-9,/\.\- ]{6,30})",
    re.IGNORECASE,
)
_DUE_DATE_RE = re.compile(
    r"(?:due\s*date|payment\s*due|due\s*by)\s*[:#-]?\s*([A-Za-z0-9,/\.\- ]{6,30})",
    re.IGNORECASE,
)
_TERMS_RE = re.compile(
    r"(?:payment\s*terms|terms)\s*[:#-]?\s*"
    r"(net\s*\d+|due\s*(?:on|upon)\s*receipt|\d+/\d+\s*net\s*\d+|c\.?o\.?d\.?|\d+\s*eom)",
    re.IGNORECASE,
)
_INLINE_TERMS_RE = re.compile(
    r"\b(\d+/\d+\s*net\s*\d+|net\s*\d+|due\s*(?:on|upon)\s*receipt)\b", re.IGNORECASE
)
_SUBTOTAL_RE = re.compile(r"sub\s*-?\s*total\s*[:#-]?\s*([^\n]+)", re.IGNORECASE)
_TAX_RE = re.compile(
    r"\b(?:tax|vat|gst|sales\s*tax)\b\s*(?:\([^)]*\))?\s*[:#-]?\s*([^\n]+)", re.IGNORECASE
)
_TOTAL_RE = re.compile(
    r"(?:grand\s*total|total\s*due|amount\s*due|balance\s*due|total)\s*[:#-]?\s*([^\n]+)",
    re.IGNORECASE,
)
# Line item: description ... qty ... unit price ... total (amounts at the end).
_LINE_ITEM_RE = re.compile(
    r"^(?P<desc>.+?)\s+(?P<qty>\d+(?:\.\d+)?)\s+"
    r"(?P<price>[$€£₹]?\s*\d[\d,]*(?:\.\d+)?)\s+"
    r"(?P<total>[$€£₹]?\s*\d[\d,]*(?:\.\d+)?)\s*$"
)


def _first_amount(text: str) -> Decimal | None:
    amounts = find_money_amounts(text)
    return amounts[0] if amounts else None


def _extract_vendor(lines: list[str]) -> tuple[str | None, float]:
    """Heuristic vendor detection: explicit label first, else the first text line."""
    for i, line in enumerate(lines):
        m = re.match(
            r"\s*(?:from|vendor|supplier|bill\s*from|sold\s*by)\s*[:#-]\s*(.+)", line, re.IGNORECASE
        )
        if m and m.group(1).strip():
            return m.group(1).strip(), 0.8
        if re.match(
            r"\s*(?:from|vendor|supplier|bill\s*from)\s*$", line, re.IGNORECASE
        ) and i + 1 < len(lines):
            nxt = lines[i + 1].strip()
            if nxt:
                return nxt, 0.7
    for line in lines:
        stripped = line.strip()
        if stripped and not re.match(r"invoice|date|bill|#", stripped, re.IGNORECASE):
            return stripped, 0.4
    return None, 0.0


def _extract_line_items(lines: list[str]) -> list[ExtractedLineItem]:
    items: list[ExtractedLineItem] = []
    for line in lines:
        m = _LINE_ITEM_RE.match(line.strip())
        if not m:
            continue
        desc = m.group("desc").strip()
        if re.search(
            r"sub\s*-?\s*total|grand\s*total|amount\s*due|balance|tax|vat|gst", desc, re.IGNORECASE
        ):
            continue
        items.append(
            ExtractedLineItem(
                description=desc,
                quantity=parse_money(m.group("qty")),
                unit_price=parse_money(m.group("price")),
                total=parse_money(m.group("total")),
            )
        )
    return items


def extract_deterministic(raw_text: str) -> ExtractedInvoice:
    """Extract invoice fields from raw text using regex heuristics."""
    text = raw_text
    lines = raw_text.splitlines()
    confidence: dict[str, float] = {}
    notes: list[str] = []

    result = ExtractedInvoice(source=ExtractionSource.DETERMINISTIC)

    if m := _INVOICE_NO_RE.search(text):
        result.invoice_number = m.group(1).strip()
        confidence["invoice_number"] = 0.85

    if m := _INVOICE_DATE_RE.search(text):
        d = parse_date(m.group(1))
        if d:
            result.invoice_date = d
            confidence["invoice_date"] = 0.8

    if m := _DUE_DATE_RE.search(text):
        d = parse_date(m.group(1))
        if d:
            result.due_date = d
            confidence["due_date"] = 0.8

    vendor, vendor_conf = _extract_vendor(lines)
    if vendor:
        result.vendor_name = vendor
        confidence["vendor_name"] = vendor_conf

    if m := _TERMS_RE.search(text):
        result.payment_terms = m.group(1).strip()
        confidence["payment_terms"] = 0.85
    elif m := _INLINE_TERMS_RE.search(text):
        result.payment_terms = m.group(1).strip()
        confidence["payment_terms"] = 0.6

    currency = detect_currency(text)
    if currency:
        result.currency = currency
        confidence["currency"] = 0.7

    if m := _SUBTOTAL_RE.search(text):
        amt = _first_amount(m.group(1))
        if amt is not None:
            result.subtotal = amt
            confidence["subtotal"] = 0.8

    if m := _TAX_RE.search(text):
        amt = _first_amount(m.group(1))
        if amt is not None:
            result.tax = amt
            confidence["tax"] = 0.75

    # Grand total: prefer the strongest label; fall back to the largest amount.
    total_candidates = [match.group(1) for match in _TOTAL_RE.finditer(text)]
    if total_candidates:
        amt = _first_amount(total_candidates[-1])
        if amt is not None:
            result.grand_total = amt
            confidence["grand_total"] = 0.8

    result.line_items = _extract_line_items(lines)
    if result.line_items:
        confidence["line_items"] = 0.6

    if result.grand_total is None:
        all_amounts = find_money_amounts(text)
        if all_amounts:
            result.grand_total = max(all_amounts)
            confidence["grand_total"] = 0.4
            notes.append("Grand total inferred as the largest monetary amount found.")

    result.confidence = confidence
    result.notes = notes
    return result
