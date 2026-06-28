"""Unit tests for multi-file invoice handling.

Covers the decode/validation layer (``services.extraction.files``) and the
content-building that turns multiple files into LLM content parts
(``services.extraction.ocr._build_content``), including the size/count/image caps.
"""

from __future__ import annotations

import base64

import pytest

from ap_invoice.core.config import get_settings
from ap_invoice.services.extraction.files import (
    FileSpec,
    InputFile,
    InvalidFileError,
    _sniff_content_type,
    collect_specs,
    decode_files,
)
from ap_invoice.services.extraction.ocr import _build_content

# Minimal byte blobs carrying the right magic numbers for sniffing.
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 16
JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 16
PDF = b"%PDF-1.4\n" + b"\x00" * 16


def _b64(data: bytes) -> str:
    return base64.b64encode(data).decode()


# --------------------------------------------------------------------------- #
# content-type sniffing
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("data", "expected"),
    [
        (PNG, "image/png"),
        (JPEG, "image/jpeg"),
        (PDF, "application/pdf"),
        (b"GIF89a....", "image/gif"),
        (b"RIFF\x00\x00\x00\x00WEBP", "image/webp"),
        (b"not a known format", None),
    ],
)
def test_sniff_content_type(data: bytes, expected: str | None) -> None:
    assert _sniff_content_type(data) == expected


# --------------------------------------------------------------------------- #
# collect_specs — merging legacy single file + files list
# --------------------------------------------------------------------------- #


def test_collect_specs_merges_legacy_and_list() -> None:
    specs = collect_specs(
        _b64(PNG),
        "image/png",
        [{"file_base64": _b64(JPEG), "content_type": "image/jpeg", "filename": "p2.jpg"}],
    )
    assert len(specs) == 2
    assert specs[0].content_type == "image/png"
    assert specs[1].filename == "p2.jpg"


def test_collect_specs_accepts_attribute_objects() -> None:
    class _Obj:
        file_base64 = _b64(PDF)
        content_type = "application/pdf"
        filename = None

    specs = collect_specs(None, None, [_Obj()])
    assert len(specs) == 1 and specs[0].content_type == "application/pdf"


def test_collect_specs_rejects_entry_without_base64() -> None:
    with pytest.raises(InvalidFileError):
        collect_specs(None, None, [{"content_type": "image/png"}])


# --------------------------------------------------------------------------- #
# decode_files — validation + caps
# --------------------------------------------------------------------------- #


def test_decode_files_happy_path_sniffs_missing_type() -> None:
    files = decode_files([FileSpec(file_base64=_b64(PNG)), FileSpec(file_base64=_b64(PDF))])
    assert [f.content_type for f in files] == ["image/png", "application/pdf"]
    assert files[0].data == PNG


def test_decode_files_keeps_explicit_type() -> None:
    files = decode_files([FileSpec(file_base64=_b64(PNG), content_type="image/custom")])
    assert files[0].content_type == "image/custom"


def test_decode_files_rejects_bad_base64() -> None:
    with pytest.raises(InvalidFileError, match="not valid base64"):
        decode_files([FileSpec(file_base64="!!!not-base64!!!")])


def test_decode_files_rejects_empty() -> None:
    with pytest.raises(InvalidFileError, match="empty"):
        decode_files([FileSpec(file_base64=_b64(b""))])


def test_decode_files_enforces_size_cap(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(get_settings(), "max_file_bytes", 4)
    with pytest.raises(InvalidFileError, match="exceeds"):
        decode_files([FileSpec(file_base64=_b64(b"way too many bytes"))])


def test_decode_files_enforces_count_cap(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(get_settings(), "max_files_per_invoice", 2)
    specs = [FileSpec(file_base64=_b64(PNG)) for _ in range(3)]
    with pytest.raises(InvalidFileError, match="Too many files"):
        decode_files(specs)


# --------------------------------------------------------------------------- #
# _build_content — multiple files become multiple content parts
# --------------------------------------------------------------------------- #


def test_build_content_combines_text_and_multiple_images() -> None:
    files = [
        InputFile(data=PNG, content_type="image/png"),
        InputFile(data=JPEG, content_type="image/jpeg"),
    ]
    content = _build_content("Invoice text here", files)
    texts = [p for p in content if p["type"] == "text"]
    images = [p for p in content if p["type"] == "image"]
    assert any("Invoice text here" in t["text"] for t in texts)
    assert len(images) == 2
    assert {i["media_type"] for i in images} == {"image/png", "image/jpeg"}


def test_build_content_caps_total_images(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(get_settings(), "max_extraction_images", 2)
    files = [InputFile(data=PNG, content_type="image/png") for _ in range(5)]
    images = [p for p in _build_content(None, files) if p["type"] == "image"]
    assert len(images) == 2  # capped, the rest are dropped


def test_build_content_rejects_unsupported_type() -> None:
    with pytest.raises(InvalidFileError, match="Unsupported file type"):
        _build_content(None, [InputFile(data=b"PK\x03\x04", content_type="application/zip")])


def test_build_content_requires_some_input() -> None:
    with pytest.raises(InvalidFileError, match="No invoice text or files"):
        _build_content(None, [])
