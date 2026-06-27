"""Vision invoice extractor (mandatory extraction path).

Sends the invoice to the configured multimodal LLM (Claude or GPT) and gets back
structured fields with a per-field confidence score. Handles every input shape
the system accepts:

* raw text          → sent as a text prompt
* image (png/jpg…)  → sent as a vision image block
* PDF               → each page is rasterised to PNG with ``pypdfium2`` (pure
  Python, no system deps) and sent as image blocks

The call goes through :func:`ap_invoice.services.llm.call_tool` against the
provider in ``AP_LLM_PROVIDER``. Extraction is mandatory: if the provider is not
configured (or errors), :class:`ExtractionUnavailable` is raised — there is no
offline fallback.
"""

from __future__ import annotations

import base64
from datetime import date

from pydantic import BaseModel, Field, ValidationError

from ap_invoice.core.config import get_settings
from ap_invoice.core.enums import ExtractionSource
from ap_invoice.core.logging import get_logger
from ap_invoice.schemas.tools import ExtractedInvoice, ExtractedLineItem
from ap_invoice.services._parsing import parse_money
from ap_invoice.services.llm import LLMUnavailable, call_tool

logger = get_logger(__name__)

# Cap how many PDF pages we rasterise/send, to bound cost and tokens.
_MAX_PDF_PAGES = 8
_PDF_RENDER_SCALE = 2.0  # ~144 DPI, enough for reliable OCR.

_SYSTEM_PROMPT = (
    "You are an expert accounts-payable invoice OCR + parser. Read the invoice "
    "(text and/or images) and extract the requested fields exactly as they "
    "appear, including any purchase order (PO) number if one is shown. "
    "Pay special attention to identifying the vendor (seller) name, which may "
    "appear at the very beginning of the raw text (even inline before the 'Invoice Number' "
    "label). Do not invent values: if a field is absent, return null for it and "
    "0.0 for its confidence. For every field, return a confidence score in [0, 1] "
    "reflecting how certain you are the value is correct. Dates must be ISO-8601 "
    "(YYYY-MM-DD). Amounts must be plain numbers without currency symbols or "
    "thousands separators."
)

_TOOL_NAME = "record_invoice_fields"


class ExtractionUnavailable(RuntimeError):
    """Raised when vision extraction cannot run (no key, API error, bad input)."""


class _LLMLineItem(BaseModel):
    description: str | None = None
    quantity: float | None = None
    unit_price: float | None = None
    total: float | None = None


class _FieldConfidence(BaseModel):
    invoice_number: float = 0.0
    po_number: float = 0.0
    vendor_name: float = 0.0
    invoice_date: float = 0.0
    due_date: float = 0.0
    currency: float = 0.0
    subtotal: float = 0.0
    tax: float = 0.0
    grand_total: float = 0.0
    payment_terms: float = 0.0
    line_items: float = 0.0
    notes: float = 0.0


class _LLMExtraction(BaseModel):
    """Schema the model is constrained to return."""

    invoice_number: str | None = Field(default=None, description="The invoice number or ID.")
    po_number: str | None = Field(default=None, description="Purchase order number, if present.")
    vendor_name: str | None = Field(default=None, description="The name of the vendor/seller issuing the invoice (e.g. AWS, Microsoft, Acme).")
    invoice_date: date | None = None
    due_date: date | None = None
    currency: str | None = Field(default=None, description="ISO-4217 code, e.g. USD")
    line_items: list[_LLMLineItem] = Field(default_factory=list)
    subtotal: float | None = None
    tax: float | None = None
    grand_total: float | None = None
    payment_terms: str | None = None
    notes: str | None = Field(default=None, description="Any notes, memo, project codes, or additional text on the invoice.")
    confidence: _FieldConfidence = Field(default_factory=_FieldConfidence)


def _pdf_to_image_parts(file_bytes: bytes) -> list[dict[str, str]]:
    """Rasterise PDF pages to PNG image content parts."""
    from io import BytesIO

    import pypdfium2 as pdfium  # type: ignore[import-untyped]

    pdf = pdfium.PdfDocument(file_bytes)
    try:
        parts: list[dict[str, str]] = []
        for i in range(min(len(pdf), _MAX_PDF_PAGES)):
            bitmap = pdf[i].render(scale=_PDF_RENDER_SCALE)
            buf = BytesIO()
            bitmap.to_pil().save(buf, format="PNG")
            parts.append(
                {
                    "type": "image",
                    "media_type": "image/png",
                    "data": base64.b64encode(buf.getvalue()).decode(),
                }
            )
        return parts
    finally:
        pdf.close()


def _build_content(
    raw_text: str | None, file_bytes: bytes | None, content_type: str | None
) -> list[dict[str, str]]:
    """Turn the available input into neutral content parts for the LLM layer."""
    instruction = {"type": "text", "text": "Extract the invoice fields from this invoice."}

    if file_bytes:
        ctype = (content_type or "").lower()
        if "pdf" in ctype:
            image_parts = _pdf_to_image_parts(file_bytes)
            if not image_parts:
                raise ExtractionUnavailable("PDF contained no rasterisable pages.")
            return [instruction, *image_parts]
        if ctype.startswith("image/"):
            return [
                instruction,
                {
                    "type": "image",
                    "media_type": ctype,
                    "data": base64.b64encode(file_bytes).decode(),
                },
            ]
        raise ExtractionUnavailable(f"Unsupported content type for OCR: {content_type!r}.")

    if raw_text and raw_text.strip():
        prompt = f"Extract the invoice fields from this text:\n\n{raw_text}"
        return [{"type": "text", "text": prompt}]

    raise ExtractionUnavailable("No invoice text or file provided for extraction.")


async def extract_with_vision(
    *,
    raw_text: str | None = None,
    file_bytes: bytes | None = None,
    content_type: str | None = None,
) -> ExtractedInvoice:
    """Extract invoice fields via the configured LLM. Raises :class:`ExtractionUnavailable`."""
    settings = get_settings()
    if not settings.llm_available:
        raise ExtractionUnavailable(
            f"LLM provider '{settings.llm_provider}' is not configured."
        )

    content = _build_content(raw_text, file_bytes, content_type)

    try:
        tool_input = await call_tool(
            provider=settings.llm_provider,
            system=_SYSTEM_PROMPT,
            content=content,
            tool_name=_TOOL_NAME,
            tool_description="Record the structured fields extracted from the invoice.",
            tool_schema=_LLMExtraction.model_json_schema(),
        )
    except LLMUnavailable as exc:
        logger.warning("vision_extraction_failed", error=str(exc))
        raise ExtractionUnavailable(str(exc)) from exc

    try:
        parsed = _LLMExtraction.model_validate(tool_input)
    except ValidationError as exc:
        raise ExtractionUnavailable(f"Invalid extraction output: {exc}") from exc

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
        po_number=parsed.po_number,
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
        source=ExtractionSource.OCR,
        notes=[parsed.notes] if parsed.notes else [],
    )
