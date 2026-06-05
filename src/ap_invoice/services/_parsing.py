"""Shared parsing & normalization helpers used by the tool services."""

from __future__ import annotations

import re
from datetime import date
from decimal import Decimal, InvalidOperation

from dateutil import parser as date_parser

# Common legal/entity suffixes stripped when normalizing vendor names.
_VENDOR_SUFFIXES = {
    "inc",
    "incorporated",
    "corp",
    "corporation",
    "co",
    "company",
    "ltd",
    "limited",
    "llc",
    "llp",
    "lp",
    "plc",
    "gmbh",
    "ag",
    "sa",
    "nv",
    "bv",
    "pvt",
    "private",
    "pte",
    "srl",
    "spa",
    "oy",
    "ab",
    "as",
    "kg",
    "group",
    "holdings",
    "international",
    "intl",
}

_PUNCT_RE = re.compile(r"[^\w\s]")
_WS_RE = re.compile(r"\s+")
# Match a number possibly with thousands separators and a decimal part.
_MONEY_RE = re.compile(r"-?\(?\$?\s*\d{1,3}(?:[,\s]\d{3})*(?:\.\d+)?\)?|-?\$?\s*\d+(?:\.\d+)?")


def normalize_vendor_name(name: str, *, strip_suffixes: bool = True) -> str:
    """Lowercase, strip punctuation/whitespace, and optionally drop legal suffixes."""
    text = _PUNCT_RE.sub(" ", name.lower())
    text = _WS_RE.sub(" ", text).strip()
    if not strip_suffixes:
        return text
    tokens = [t for t in text.split(" ") if t and t not in _VENDOR_SUFFIXES]
    return " ".join(tokens) if tokens else text


def normalize_invoice_number(value: str) -> str:
    """Normalize an invoice number for comparison (uppercase alphanumerics only)."""
    return re.sub(r"[^A-Za-z0-9]", "", value).upper()


def parse_money(value: str | int | float | Decimal | None) -> Decimal | None:
    """Best-effort parse of a monetary value into a Decimal.

    Handles currency symbols, thousands separators, and accounting-style
    parentheses for negatives. Returns ``None`` if nothing parseable is found.
    """
    if value is None:
        return None
    if isinstance(value, Decimal):
        return value
    if isinstance(value, int | float):
        return Decimal(str(value))

    text = value.strip()
    if not text:
        return None
    negative = "(" in text and ")" in text
    cleaned = text.replace("(", "").replace(")", "")
    cleaned = cleaned.replace("$", "").replace("€", "").replace("£", "").replace("₹", "")
    cleaned = cleaned.replace(",", "").replace(" ", "")
    try:
        amount = Decimal(cleaned)
    except (InvalidOperation, ValueError):
        return None
    return -amount if negative else amount


def find_money_amounts(text: str) -> list[Decimal]:
    """Extract all monetary amounts appearing in a block of text, in order."""
    out: list[Decimal] = []
    for token in _MONEY_RE.findall(text):
        amount = parse_money(token)
        if amount is not None:
            out.append(amount)
    return out


def parse_date(value: str, *, dayfirst: bool = False) -> date | None:
    """Best-effort parse of a date string. Returns ``None`` on failure."""
    text = value.strip()
    if not text:
        return None
    try:
        return date_parser.parse(text, dayfirst=dayfirst, fuzzy=True).date()
    except (ValueError, OverflowError, TypeError):
        return None


CURRENCY_SYMBOLS = {"$": "USD", "€": "EUR", "£": "GBP", "₹": "INR", "¥": "JPY"}
KNOWN_CURRENCY_CODES = {
    "USD",
    "EUR",
    "GBP",
    "INR",
    "JPY",
    "CAD",
    "AUD",
    "CHF",
    "CNY",
    "SGD",
    "AED",
}


def detect_currency(text: str) -> str | None:
    """Detect a currency from an ISO code or symbol present in the text."""
    upper = text.upper()
    for code in KNOWN_CURRENCY_CODES:
        if re.search(rf"\b{code}\b", upper):
            return code
    for symbol, code in CURRENCY_SYMBOLS.items():
        if symbol in text:
            return code
    return None
