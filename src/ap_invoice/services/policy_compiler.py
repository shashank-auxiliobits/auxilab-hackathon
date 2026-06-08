"""Policy compiler: turn a free-form vendor policy document into structured rules.

This is where the LLM/RAG "intelligence" lives — at *onboarding* time, not at
decision time. It reads the attached document and extracts typed, enforceable
:class:`PolicyRule` rows (status ``proposed``). A human/vendor approves them,
and the deterministic policy engine enforces only the approved structured rules.
Decisions never feed the raw document to an LLM, which keeps them reproducible
and safe from prompt injection.

If no Anthropic key is configured, a deterministic regex fallback extracts the
common, unambiguous rules (payment terms, amount caps, PO requirement, currency)
so the pipeline works fully offline.
"""

from __future__ import annotations

import re
from typing import Any, Literal

from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from ap_invoice.core.config import get_settings
from ap_invoice.core.enums import DocumentStatus, PolicyRuleStatus, PolicyRuleType
from ap_invoice.core.logging import get_logger
from ap_invoice.models.policy_document import PolicyRule, VendorDocument
from ap_invoice.services._parsing import detect_currency, parse_money

logger = get_logger(__name__)

_RULE_TYPES = (
    "max_invoice_amount",
    "require_field",
    "allowed_payment_terms",
    "line_item_price_cap",
    "requires_purchase_order",
    "currency",
    "custom",
)

_SYSTEM_PROMPT = (
    "You convert a vendor's accounts-payable policy document into a list of "
    "structured, machine-enforceable rules. Only extract rules that are clearly "
    "stated. For each rule choose the most specific rule_type and fill the "
    "relevant fields; quote the exact source clause in source_quote. If a rule "
    "cannot be expressed by the structured types, use 'custom' with a clear "
    "description. Do NOT invent rules that are not in the document."
)


class _LLMRule(BaseModel):
    rule_type: Literal[
        "max_invoice_amount",
        "require_field",
        "allowed_payment_terms",
        "line_item_price_cap",
        "requires_purchase_order",
        "currency",
        "custom",
    ]
    amount: float | None = Field(default=None, description="For max_invoice_amount / caps.")
    field: str | None = Field(default=None, description="For require_field, e.g. 'po_number'.")
    payment_terms: list[str] | None = Field(default=None, description="For allowed_payment_terms.")
    keyword: str | None = Field(default=None, description="For line_item_price_cap line match.")
    max_unit_price: float | None = Field(default=None, description="For line_item_price_cap.")
    currency: str | None = None
    description: str
    source_quote: str | None = None
    confidence: float = 0.5


class _LLMRules(BaseModel):
    rules: list[_LLMRule] = Field(default_factory=list)


def _params_for(rule: _LLMRule) -> dict[str, Any]:
    """Map a flat LLM rule into the typed parameters dict for its rule_type."""
    match rule.rule_type:
        case "max_invoice_amount":
            return {"amount": rule.amount}
        case "require_field":
            return {"field": rule.field}
        case "allowed_payment_terms":
            return {"terms": rule.payment_terms or []}
        case "line_item_price_cap":
            return {"keyword": rule.keyword, "max_unit_price": rule.max_unit_price}
        case "currency":
            return {"currency": (rule.currency or "").upper()}
        case _:
            return {}


_TOOL_NAME = "record_policy_rules"


async def _compile_with_llm(text: str) -> list[_LLMRule]:
    from pydantic import ValidationError

    from ap_invoice.services.llm import call_tool

    settings = get_settings()
    tool_input = await call_tool(
        provider=settings.llm_provider,
        system=_SYSTEM_PROMPT,
        content=[{"type": "text", "text": f"Vendor policy document:\n\n{text}"}],
        tool_name=_TOOL_NAME,
        tool_description="Record the structured, enforceable rules extracted from the policy.",
        tool_schema=_LLMRules.model_json_schema(),
        max_tokens=settings.policy_compiler_max_tokens,
    )
    try:
        return _LLMRules.model_validate(tool_input).rules
    except ValidationError:
        return []


# --- deterministic fallback ------------------------------------------------- #

_TERMS_RE = re.compile(r"\b(\d+/\d+\s*net\s*\d+|net\s*\d+|due\s+on\s+receipt|cod)\b", re.IGNORECASE)
_MAX_AMOUNT_RE = re.compile(
    r"(?:invoices?\s+(?:over|above|exceeding)|maximum\s+invoice(?:\s+amount)?(?:\s+of)?|"
    r"not\s+to\s+exceed|must\s+not\s+exceed)\s*\$?\s*([\d,]+(?:\.\d{1,2})?)",
    re.IGNORECASE,
)
_PO_RE = re.compile(
    r"purchase\s+order\s+(?:is\s+)?required|must\s+reference\s+a\s+(?:valid\s+)?p\.?o\.?|"
    r"\bp\.?o\.?\s+(?:number\s+)?(?:is\s+)?(?:required|mandatory)",
    re.IGNORECASE,
)


def _compile_deterministic(text: str) -> list[_LLMRule]:
    rules: list[_LLMRule] = []

    terms = sorted({m.group(1).strip() for m in _TERMS_RE.finditer(text)})
    if terms:
        rules.append(
            _LLMRule(
                rule_type="allowed_payment_terms",
                payment_terms=terms,
                description=f"Allowed payment terms: {', '.join(terms)}.",
                source_quote=terms[0],
                confidence=0.5,
            )
        )

    if m := _MAX_AMOUNT_RE.search(text):
        amount = parse_money(m.group(1))
        if amount is not None:
            rules.append(
                _LLMRule(
                    rule_type="max_invoice_amount",
                    amount=float(amount),
                    description=f"Invoice amount must not exceed {amount}.",
                    source_quote=m.group(0),
                    confidence=0.5,
                )
            )

    if _PO_RE.search(text):
        rules.append(
            _LLMRule(
                rule_type="requires_purchase_order",
                description="A purchase order is required.",
                source_quote="purchase order required",
                confidence=0.5,
            )
        )

    if currency := detect_currency(text):
        rules.append(
            _LLMRule(
                rule_type="currency",
                currency=currency,
                description=f"Invoices must be in {currency}.",
                source_quote=currency,
                confidence=0.4,
            )
        )

    return rules


async def compile_document(
    db: AsyncSession,
    document: VendorDocument,
    *,
    engine: Literal["llm", "deterministic"] | None = None,
) -> list[PolicyRule]:
    """Compile a document into proposed PolicyRule rows and persist them."""
    settings = get_settings()
    chosen = engine or ("llm" if settings.llm_available else "deterministic")

    llm_rules: list[_LLMRule]
    if chosen == "llm" and settings.llm_available:
        try:
            llm_rules = await _compile_with_llm(document.text)
        except Exception as exc:
            # Degrade to deterministic extraction on any LLM error.
            logger.warning("policy_compile_llm_failed", error=str(exc))
            llm_rules = _compile_deterministic(document.text)
    else:
        llm_rules = _compile_deterministic(document.text)

    rules: list[PolicyRule] = []
    for r in llm_rules:
        if r.rule_type not in _RULE_TYPES:
            continue
        rules.append(
            PolicyRule(
                organization_id=document.organization_id,
                vendor_id=document.vendor_id,
                document_id=document.id,
                rule_type=PolicyRuleType(r.rule_type),
                parameters=_params_for(r),
                description=r.description,
                source_quote=r.source_quote,
                confidence=r.confidence,
                status=PolicyRuleStatus.PROPOSED,
            )
        )
    db.add_all(rules)
    document.status = DocumentStatus.COMPILED
    await db.flush()
    return rules
