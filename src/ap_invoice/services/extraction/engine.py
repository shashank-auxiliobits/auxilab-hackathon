"""Extraction engine entry point.

Extraction is mandatory and always runs through the configured vision LLM
(Claude or GPT) — there is no deterministic/regex fallback. The same call
handles raw text, scanned images, PDFs, and photos (see
:mod:`ap_invoice.services.extraction.ocr`). If the provider is not configured or
the call fails, :class:`ExtractionUnavailable` is raised so the caller fails
loudly rather than silently degrading.
"""

from __future__ import annotations

from ap_invoice.schemas.tools import ExtractedInvoice
from ap_invoice.services.extraction.ocr import ExtractionUnavailable, extract_with_vision

__all__ = ["ExtractionUnavailable", "extract_invoice"]


async def extract_invoice(
    raw_text: str | None = None,
    *,
    file_bytes: bytes | None = None,
    content_type: str | None = None,
) -> ExtractedInvoice:
    """Extract structured invoice fields from text and/or a file via the configured LLM."""
    return await extract_with_vision(
        raw_text=raw_text, file_bytes=file_bytes, content_type=content_type
    )
