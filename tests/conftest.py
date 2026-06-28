"""Shared test configuration.

Sets a hermetic, offline test environment *before* the application package is
imported. Extraction and the decision are now mandatory LLM stages, so instead of
disabling the LLM we stub the provider layer: an autouse fixture replaces
``call_tool`` everywhere it's used with a small, deterministic fake (a regex OCR
parser + a policy-mimicking decision), so tests never touch the network.
"""

from __future__ import annotations

import json
import os
import re
from typing import Any

os.environ.setdefault("AP_ENVIRONMENT", "test")
os.environ.setdefault("AP_API_KEY_PEPPER", "test-pepper-for-pytest-only")
os.environ.setdefault("AP_JWT_SECRET", "test-jwt-secret-for-pytest-only-0123456789")
os.environ.setdefault("AP_EMAIL_BACKEND", "console")
os.environ.setdefault("AP_LOG_JSON", "false")
os.environ.setdefault(
    "AP_DATABASE_URL",
    "postgresql+asyncpg://ap:ap_password@localhost:5432/ap_invoice_test",
)

# Dummy credentials so llm_available is true. No real calls are made — the
# autouse fixture below stubs the provider layer.
os.environ["AP_LLM_PROVIDER"] = "claude"
os.environ["AP_ANTHROPIC_API_KEY"] = "test-anthropic-key"

import pytest

from ap_invoice.core.config import get_settings

# Ensure the cached settings reflect the env we just set.
get_settings.cache_clear()


# --------------------------------------------------------------------------- #
# Fake LLM provider (offline, deterministic)
# --------------------------------------------------------------------------- #

_MONEY_RE = re.compile(r"\$?\s*([\d,]+(?:\.\d{1,2})?)")


def _money(text: str) -> float | None:
    m = _MONEY_RE.search(text)
    return float(m.group(1).replace(",", "")) if m else None


def _field(text: str, label: str) -> str | None:
    m = re.search(rf"{label}\s*:?\s*(.+)", text, re.IGNORECASE)
    return m.group(1).strip() if m else None


def _fake_ocr(content: list[dict[str, Any]]) -> dict[str, Any]:
    """Parse the simple, labelled invoice text used in the test suite."""
    text = "\n".join(p["text"] for p in content if p.get("type") == "text")
    # Strip the leading instruction line we add in _build_content.
    body = text.split("\n\n", 1)[-1] if "\n\n" in text else text
    lines = [ln for ln in body.splitlines() if ln.strip()]
    vendor = lines[0].strip() if lines else None

    number = _field(text, "Invoice Number") or _field(text, "Invoice No")
    po = _field(text, "PO Number") or _field(text, "Purchase Order")
    inv_date = _field(text, "Invoice Date")
    due = _field(text, "Due Date")
    terms = _field(text, "Payment Terms")
    total_line = _field(text, "Grand Total") or _field(text, "Total") or ""
    subtotal_line = _field(text, "Subtotal") or ""
    tax_line = _field(text, "Tax") or ""

    def _iso(value: str | None) -> str | None:
        if not value:
            return None
        m = re.search(r"\d{4}-\d{2}-\d{2}", value)
        return m.group(0) if m else None

    conf = dict.fromkeys(
        [
            "invoice_number",
            "vendor_name",
            "invoice_date",
            "due_date",
            "currency",
            "subtotal",
            "tax",
            "grand_total",
            "payment_terms",
            "line_items",
        ],
        0.95,
    )
    return {
        "invoice_number": number,
        "po_number": po,
        "vendor_name": vendor,
        "invoice_date": _iso(inv_date),
        "due_date": _iso(due),
        "currency": "USD",
        "line_items": [],
        "subtotal": _money(subtotal_line) if subtotal_line else None,
        "tax": _money(tax_line) if tax_line else None,
        "grand_total": _money(total_line) if total_line else None,
        "payment_terms": terms,
        "confidence": conf,
    }


def _norm_terms(value: str | None) -> str:
    return re.sub(r"[^a-z0-9]", "", value.lower()) if value else ""


def _fake_decision(content: list[dict[str, Any]]) -> dict[str, Any]:
    """Enforce the retrieved policy TEXT against the invoice (policy = source of truth).

    The decision engine only calls this when there IS policy on file and the
    invoice is not an exact duplicate (those are handled as code guardrails). To
    stay faithful to "policy is the source of truth", this reuses the same
    regex policy extraction the compiler uses, then checks the invoice against it.
    """
    from ap_invoice.services.policy_compiler import _compile_deterministic

    text = "\n".join(p["text"] for p in content if p.get("type") == "text")

    def _between(start: str, end: str) -> str:
        return text.split(start, 1)[-1].split(end, 1)[0] if start in text else ""

    policy_text = _between("<vendor_policy>", "</vendor_policy>")
    fields = json.loads(_between("<invoice_fields>", "</invoice_fields>") or "{}")
    rules = _compile_deterministic(policy_text)

    def _as_float(value: Any) -> float | None:
        try:
            return float(value)
        except (TypeError, ValueError):
            return None

    amount = _as_float(fields.get("grand_total"))
    currency = (fields.get("currency") or "").upper()
    terms = fields.get("payment_terms")
    has_po = bool(fields.get("has_purchase_order"))

    violations: list[str] = []
    for r in rules:
        if (
            r.rule_type == "max_invoice_amount"
            and amount is not None
            and r.amount
            and amount > r.amount
        ):
            violations.append(f"Amount {amount} exceeds policy cap {r.amount}.")
        elif r.rule_type == "requires_purchase_order" and not has_po:
            violations.append("Policy requires a purchase order; none present.")
        elif (
            r.rule_type == "currency" and currency and r.currency and currency != r.currency.upper()
        ):
            violations.append(f"Currency {currency} differs from policy currency {r.currency}.")
        elif r.rule_type == "allowed_payment_terms" and terms and r.payment_terms:
            allowed = {_norm_terms(t) for t in r.payment_terms}
            if _norm_terms(terms) not in allowed:
                violations.append(f"Payment terms '{terms}' not allowed by policy.")

    if violations:
        decision, summary, reasons = "flag", "Flagged: policy violation(s).", violations
    else:
        decision, summary, reasons = "auto_approve", "Auto-approved: complies with policy.", []

    return {
        "decision": decision,
        "confidence": 0.9,
        "summary": summary,
        "reasons": reasons,
        "checks": [],
    }


async def _fake_call_tool(
    *, tool_name: str, content: list[dict[str, Any]], **_: Any
) -> dict[str, Any]:
    if tool_name == "record_invoice_fields":
        return _fake_ocr(content)
    if tool_name == "record_invoice_decision":
        return _fake_decision(content)
    if tool_name == "record_policy_rules":
        return {"rules": []}
    raise AssertionError(f"Unexpected tool_name in tests: {tool_name}")


@pytest.fixture(autouse=True)
def _stub_llm(monkeypatch: pytest.MonkeyPatch) -> None:
    """Replace the provider layer everywhere ``call_tool`` is referenced."""
    monkeypatch.setattr("ap_invoice.services.extraction.ocr.call_tool", _fake_call_tool)
    monkeypatch.setattr("ap_invoice.services.llm_decision.call_tool", _fake_call_tool)
    # policy_compiler imports call_tool lazily from the package namespace.
    monkeypatch.setattr("ap_invoice.services.llm.call_tool", _fake_call_tool)
    monkeypatch.setattr("ap_invoice.services.llm.providers.call_tool", _fake_call_tool)
