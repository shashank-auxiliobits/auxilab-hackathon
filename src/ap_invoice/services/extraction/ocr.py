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
from collections.abc import Sequence
from datetime import date

from pydantic import BaseModel, Field, ValidationError

from ap_invoice.core.config import get_settings
from ap_invoice.core.enums import ExtractionSource
from ap_invoice.core.logging import get_logger
from ap_invoice.schemas.tools import ExtractedInvoice, ExtractedLineItem
from ap_invoice.services._parsing import parse_money
from ap_invoice.services.extraction.files import InputFile, InvalidFileError
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
    vendor_name: str | None = Field(
        default=None,
        description="The vendor/seller name issuing the invoice (e.g. AWS, Microsoft, Acme).",
    )
    invoice_date: date | None = None
    due_date: date | None = None
    currency: str | None = Field(default=None, description="ISO-4217 code, e.g. USD")
    line_items: list[_LLMLineItem] = Field(default_factory=list)
    subtotal: float | None = None
    tax: float | None = None
    grand_total: float | None = None
    payment_terms: str | None = None
    notes: str | None = Field(
        default=None,
        description="Any notes, memo, project codes, or additional text on the invoice.",
    )
    confidence: _FieldConfidence = Field(default_factory=_FieldConfidence)


def _pdf_to_image_parts(file_bytes: bytes, max_pages: int) -> list[dict[str, str]]:
    """Rasterise up to ``max_pages`` PDF pages to PNG image content parts."""
    from io import BytesIO

    import pypdfium2 as pdfium  # type: ignore[import-untyped]

    try:
        pdf = pdfium.PdfDocument(file_bytes)
    except pdfium.PdfiumError as exc:
        raise InvalidFileError(f"Could not open PDF: {exc}") from exc
    try:
        parts: list[dict[str, str]] = []
        for i in range(min(len(pdf), max_pages)):
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


def _file_to_image_parts(file: InputFile, budget: int) -> list[dict[str, str]]:
    """Turn one decoded file into image content parts, sending at most ``budget`` images."""
    ctype = (file.content_type or "").lower()
    if "pdf" in ctype:
        parts = _pdf_to_image_parts(file.data, max_pages=min(_MAX_PDF_PAGES, budget))
        if not parts:
            raise InvalidFileError("PDF contained no rasterisable pages.")
        return parts
    if ctype.startswith("image/"):
        return [
            {
                "type": "image",
                "media_type": ctype,
                "data": base64.b64encode(file.data).decode(),
            }
        ]
    raise InvalidFileError(
        f"Unsupported file type for OCR: {file.content_type!r}. "
        "Provide an image (image/png, image/jpeg, …) or application/pdf."
    )


def _build_content(raw_text: str | None, files: Sequence[InputFile]) -> list[dict[str, str]]:
    """Turn the available text and/or files into neutral content parts for the LLM.

    Text and files combine: when both are supplied they are sent together so the
    model sees every page/attachment of one logical invoice. PDF pages and images
    are capped collectively at ``AP_MAX_EXTRACTION_IMAGES`` to bound cost.
    """
    content: list[dict[str, str]] = [
        {"type": "text", "text": "Extract the invoice fields from this invoice."}
    ]

    if raw_text and raw_text.strip():
        content.append({"type": "text", "text": f"Invoice text:\n\n{raw_text}"})

    budget = get_settings().max_extraction_images
    for file in files:
        if budget <= 0:
            logger.warning("extraction_image_cap_reached", cap=get_settings().max_extraction_images)
            break
        parts = _file_to_image_parts(file, budget)
        content.extend(parts)
        budget -= len(parts)

    # Only the instruction line means no usable input was supplied.
    if len(content) == 1:
        raise InvalidFileError("No invoice text or files provided for extraction.")

    return content


async def extract_with_vision(
    *,
    raw_text: str | None = None,
    files: Sequence[InputFile] | None = None,
    file_bytes: bytes | None = None,
    content_type: str | None = None,
) -> ExtractedInvoice:
    """Extract invoice fields via the configured LLM from text and/or one or more files.

    Raises :class:`ExtractionUnavailable` if the provider is unavailable, or
    :class:`~ap_invoice.services.extraction.files.InvalidFileError` for bad input.
    """
    settings = get_settings()
    if not settings.llm_available:
        raise ExtractionUnavailable(f"LLM provider '{settings.llm_provider}' is not configured.")

    all_files = list(files or [])
    if file_bytes is not None:
        # Back-compat: a single raw bytes file is treated as the first file.
        all_files.insert(0, InputFile(data=file_bytes, content_type=content_type))
    content = _build_content(raw_text, all_files)

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
