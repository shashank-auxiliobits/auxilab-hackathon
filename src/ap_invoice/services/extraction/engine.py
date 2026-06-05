"""Extraction engine orchestrator.

Selects the extraction strategy and merges results:

* ``deterministic`` — regex/heuristic only (offline, free, reproducible).
* ``llm`` — Anthropic API only; raises if unavailable.
* ``hybrid`` (default) — LLM when a key is configured, with a deterministic
  backfill that fills any field the LLM left empty and a guaranteed fallback to
  the deterministic extractor if the LLM call fails.
"""

from __future__ import annotations

from typing import Literal

from ap_invoice.core.config import get_settings
from ap_invoice.core.enums import ExtractionSource
from ap_invoice.core.logging import get_logger
from ap_invoice.schemas.tools import ExtractedInvoice
from ap_invoice.services.extraction.deterministic import extract_deterministic
from ap_invoice.services.extraction.llm import ExtractionUnavailable, extract_with_llm

logger = get_logger(__name__)

EngineName = Literal["hybrid", "llm", "deterministic"]

# Header fields backfilled from the deterministic pass when the LLM omits them.
_BACKFILL_FIELDS = (
    "invoice_number",
    "vendor_name",
    "invoice_date",
    "due_date",
    "currency",
    "subtotal",
    "tax",
    "grand_total",
    "payment_terms",
)


async def extract_invoice(
    raw_text: str, *, engine: EngineName | None = None, fast: bool = False
) -> ExtractedInvoice:
    """Extract structured invoice fields from raw text using the chosen engine."""
    settings = get_settings()
    chosen: EngineName = engine or settings.extractor_engine

    if chosen == "deterministic":
        return extract_deterministic(raw_text)

    if chosen == "llm":
        # Explicit LLM request: surface failures rather than silently degrading.
        return await extract_with_llm(raw_text, fast=fast)

    # --- hybrid ---
    if not settings.llm_available:
        result = extract_deterministic(raw_text)
        result.notes.append("LLM unavailable; used deterministic extraction.")
        return result

    try:
        llm_result = await extract_with_llm(raw_text, fast=fast)
    except ExtractionUnavailable as exc:
        logger.info("hybrid_fallback_deterministic", reason=str(exc))
        result = extract_deterministic(raw_text)
        result.notes.append(f"LLM extraction failed ({exc}); used deterministic fallback.")
        return result

    deterministic_result = extract_deterministic(raw_text)
    return _merge(llm_result, deterministic_result)


def _merge(primary: ExtractedInvoice, backup: ExtractedInvoice) -> ExtractedInvoice:
    """Fill empty primary (LLM) fields from the backup (deterministic) extraction."""
    merged = primary.model_copy(deep=True)
    backup_fields = backup.as_fields()

    for field in _BACKFILL_FIELDS:
        if getattr(merged, field) is None and backup_fields.get(field) is not None:
            setattr(merged, field, backup_fields[field])
            # Deterministic backfill is less certain than a direct LLM hit.
            merged.confidence[field] = round(backup.confidence.get(field, 0.4) * 0.8, 4)

    if not merged.line_items and backup.line_items:
        merged.line_items = backup.line_items
        merged.confidence["line_items"] = round(backup.confidence.get("line_items", 0.4) * 0.8, 4)

    merged.source = ExtractionSource.HYBRID
    return merged
