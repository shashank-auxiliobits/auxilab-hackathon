"""Unit tests for the GLM OCR extraction path.

The GLM provider call is stubbed by the autouse ``_stub_llm`` fixture in
``tests/conftest.py`` (a regex OCR parser), so these exercise the real
``extract_invoice`` / ``extract_with_vision`` plumbing without any network call.
"""

from __future__ import annotations

import asyncio
import base64
from datetime import date
from decimal import Decimal
from io import BytesIO

import pytest

from ap_invoice.core.enums import ExtractionSource
from ap_invoice.services.extraction import InvalidFileError, extract_invoice
from ap_invoice.services.extraction.ocr import _pdf_to_image_parts, extract_with_vision

SAMPLE = """ACME WIDGETS LLC
Invoice Number: INV-2026-042
Invoice Date: 2026-06-01
Due Date: 2026-07-01
Payment Terms: 2/10 Net 30
Subtotal: $100.00
Tax (10%): $10.00
Grand Total: $110.00
"""


def test_ocr_extraction_from_text() -> None:
    r = asyncio.run(extract_invoice(SAMPLE))
    assert r.invoice_number == "INV-2026-042"
    assert r.invoice_date == date(2026, 6, 1)
    assert r.due_date == date(2026, 7, 1)
    assert r.grand_total == Decimal("110.00")
    assert r.source == ExtractionSource.OCR
    assert r.confidence["invoice_number"] > 0


def test_extract_with_vision_text_path() -> None:
    r = asyncio.run(extract_with_vision(raw_text=SAMPLE))
    assert r.vendor_name == "ACME WIDGETS LLC"
    assert r.source == ExtractionSource.OCR


def test_no_input_raises() -> None:
    with pytest.raises(InvalidFileError):
        asyncio.run(extract_invoice())


def test_unsupported_content_type_raises() -> None:
    with pytest.raises(InvalidFileError):
        asyncio.run(extract_invoice(file_bytes=b"x", content_type="application/zip"))


def _one_page_pdf() -> bytes:
    import pypdfium2 as pdfium

    pdf = pdfium.PdfDocument.new()
    pdf.new_page(200, 200)
    buf = BytesIO()
    pdf.save(buf)
    return buf.getvalue()


def test_pdf_rasterises_to_image_parts() -> None:
    parts = _pdf_to_image_parts(_one_page_pdf(), max_pages=8)
    assert parts and parts[0]["media_type"] == "image/png"
    # The base64 payload decodes to a PNG (starts with the PNG magic bytes).
    assert base64.b64decode(parts[0]["data"]).startswith(b"\x89PNG")


def test_pdf_extraction_path_runs() -> None:
    r = asyncio.run(extract_invoice(file_bytes=_one_page_pdf(), content_type="application/pdf"))
    assert r.source == ExtractionSource.OCR
