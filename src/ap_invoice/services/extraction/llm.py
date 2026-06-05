"""LLM-backed invoice field extractor using the Anthropic API.

Uses structured outputs (``messages.parse``) so the model returns a validated
object with a confidence score per field. Falls back are handled by the engine
layer; this module raises :class:`ExtractionUnavailable` when the LLM cannot be
used so the caller can degrade gracefully.
"""

from __future__ import annotations

from datetime import date

import anthropic
from pydantic import BaseModel, Field

from ap_invoice.core.config import get_settings
from ap_invoice.core.enums import ExtractionSource
from ap_invoice.core.logging import get_logger
from ap_invoice.schemas.tools import ExtractedInvoice, ExtractedLineItem
from ap_invoice.services._parsing import parse_money

logger = get_logger(__name__)

_SYSTEM_PROMPT = (
    "You are an expert accounts-payable invoice parser. Extract the requested "
    "fields from the raw invoice text exactly as they appear. Do not invent "
    "values: if a field is absent, return null for it and 0.0 for its confidence. "
    "For every field, return a confidence score in [0, 1] reflecting how certain "
    "you are that the extracted value is correct. Dates must be ISO-8601 "
    "(YYYY-MM-DD). Amounts must be plain numbers without currency symbols or "
    "thousands separators."
)


class ExtractionUnavailable(RuntimeError):
    """Raised when the LLM extractor cannot run (no key, API error, etc.)."""


class _LLMLineItem(BaseModel):
    description: str | None = None
    quantity: float | None = None
    unit_price: float | None = None
    total: float | None = None


class _FieldConfidence(BaseModel):
    invoice_number: float = 0.0
    vendor_name: float = 0.0
    invoice_date: float = 0.0
    due_date: float = 0.0
    currency: float = 0.0
    subtotal: float = 0.0
    tax: float = 0.0
    grand_total: float = 0.0
    payment_terms: float = 0.0
    line_items: float = 0.0


class _LLMExtraction(BaseModel):
    """Schema the model is constrained to return."""

    invoice_number: str | None = None
    vendor_name: str | None = None
    invoice_date: date | None = None
    due_date: date | None = None
    currency: str | None = Field(default=None, description="ISO-4217 code, e.g. USD")
    line_items: list[_LLMLineItem] = Field(default_factory=list)
    subtotal: float | None = None
    tax: float | None = None
    grand_total: float | None = None
    payment_terms: str | None = None
    confidence: _FieldConfidence = Field(default_factory=_FieldConfidence)


async def extract_with_llm(raw_text: str, *, fast: bool = False) -> ExtractedInvoice:
    """Extract invoice fields via the Anthropic API. Raises ExtractionUnavailable."""
    settings = get_settings()
    if not settings.anthropic_api_key:
        raise ExtractionUnavailable("No Anthropic API key configured.")

    model = settings.extractor_fast_model if fast else settings.extractor_model
    client = anthropic.AsyncAnthropic(
        api_key=settings.anthropic_api_key,
        timeout=settings.extractor_timeout_seconds,
    )

    try:
        response = await client.messages.parse(
            model=model,
            max_tokens=settings.extractor_max_tokens,
            system=_SYSTEM_PROMPT,
            output_format=_LLMExtraction,
            messages=[
                {
                    "role": "user",
                    "content": f"Extract the invoice fields from this text:\n\n{raw_text}",
                }
            ],
        )
    except anthropic.APIError as exc:
        logger.warning("llm_extraction_failed", error=str(exc), model=model)
        raise ExtractionUnavailable(str(exc)) from exc
    finally:
        await client.close()

    parsed = response.parsed_output
    if parsed is None:
        raise ExtractionUnavailable("Model returned no parseable output.")

    return _to_extracted_invoice(parsed)


def _to_extracted_invoice(parsed: _LLMExtraction) -> ExtractedInvoice:
    confidence = {k: round(v, 4) for k, v in parsed.confidence.model_dump().items()}
    line_items = [
        ExtractedLineItem(
            description=li.description,
            quantity=parse_money(li.quantity),
            unit_price=parse_money(li.unit_price),
            total=parse_money(li.total),
        )
        for li in parsed.line_items
    ]

    return ExtractedInvoice(
        invoice_number=parsed.invoice_number,
        vendor_name=parsed.vendor_name,
        invoice_date=parsed.invoice_date,
        due_date=parsed.due_date,
        currency=parsed.currency.upper() if parsed.currency else None,
        line_items=line_items,
        subtotal=parse_money(parsed.subtotal),
        tax=parse_money(parsed.tax),
        grand_total=parse_money(parsed.grand_total),
        payment_terms=parsed.payment_terms,
        confidence=confidence,
        source=ExtractionSource.LLM,
        notes=[],
    )
