"""Decoding and validation of uploaded invoice files (multi-file support).

An invoice may arrive as several files — a scan split into per-page images, or an
invoice plus supporting attachments. This module turns the raw request payload
(legacy single ``file_base64`` and/or a ``files`` list) into validated, decoded
:class:`InputFile` objects ready for the vision extractor.

Validation here represents *client* input errors and raises
:class:`InvalidFileError` (mapped to HTTP 422 at the API boundary), keeping it
distinct from :class:`~ap_invoice.services.extraction.ocr.ExtractionUnavailable`,
which signals a provider/runtime failure (mapped to 503).
"""

from __future__ import annotations

import base64
import binascii
from collections.abc import Iterable, Sequence
from dataclasses import dataclass

from ap_invoice.core.config import get_settings


class InvalidFileError(ValueError):
    """An uploaded file is malformed, empty, too large, too numerous, or unsupported."""


@dataclass(frozen=True)
class FileSpec:
    """A not-yet-decoded file: base64 contents plus optional metadata."""

    file_base64: str
    content_type: str | None = None
    filename: str | None = None


@dataclass(frozen=True)
class InputFile:
    """A decoded, validated invoice file ready for extraction."""

    data: bytes
    content_type: str | None = None
    filename: str | None = None


# Magic-number prefixes used to infer a content type when the client omits one,
# so common uploads "just work" without a caller having to set MIME types.
_MAGIC: tuple[tuple[bytes, str], ...] = (
    (b"%PDF", "application/pdf"),
    (b"\x89PNG\r\n\x1a\n", "image/png"),
    (b"\xff\xd8\xff", "image/jpeg"),
    (b"GIF87a", "image/gif"),
    (b"GIF89a", "image/gif"),
)


def _sniff_content_type(data: bytes) -> str | None:
    """Best-effort content-type detection from magic bytes (None if unknown)."""
    for prefix, ctype in _MAGIC:
        if data.startswith(prefix):
            return ctype
    # WEBP: "RIFF"<size>"WEBP"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    return None


def collect_specs(
    file_base64: str | None,
    content_type: str | None,
    files: Iterable[object] | None,
) -> list[FileSpec]:
    """Merge the legacy single-file fields and a ``files`` list into one spec list.

    ``files`` items may be mapping-like (``{"file_base64": ..., "content_type": ...}``,
    as sent over MCP) or attribute-bearing objects (the ``InvoiceFileInput`` schema).
    """
    specs: list[FileSpec] = []
    if file_base64:
        specs.append(FileSpec(file_base64=file_base64, content_type=content_type))
    for item in files or []:
        if isinstance(item, dict):
            raw = item.get("file_base64")
            if not raw:
                raise InvalidFileError("Each entry in 'files' requires a 'file_base64' value.")
            specs.append(
                FileSpec(
                    file_base64=raw,
                    content_type=item.get("content_type"),
                    filename=item.get("filename"),
                )
            )
        else:
            raw = getattr(item, "file_base64", None)
            if not raw:
                raise InvalidFileError("Each entry in 'files' requires a 'file_base64' value.")
            specs.append(
                FileSpec(
                    file_base64=raw,
                    content_type=getattr(item, "content_type", None),
                    filename=getattr(item, "filename", None),
                )
            )
    return specs


def _decode_one(spec: FileSpec, index: int, max_bytes: int) -> InputFile:
    try:
        data = base64.b64decode(spec.file_base64, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise InvalidFileError(f"file #{index}: not valid base64: {exc}") from exc
    if not data:
        raise InvalidFileError(f"file #{index}: decoded to empty content.")
    if len(data) > max_bytes:
        raise InvalidFileError(
            f"file #{index}: {len(data)} bytes exceeds the {max_bytes}-byte limit "
            f"(AP_MAX_FILE_BYTES)."
        )
    content_type = spec.content_type or _sniff_content_type(data)
    return InputFile(data=data, content_type=content_type, filename=spec.filename)


def decode_files(specs: Sequence[FileSpec]) -> list[InputFile]:
    """Decode and validate file specs. Raises :class:`InvalidFileError` on bad input."""
    settings = get_settings()
    if len(specs) > settings.max_files_per_invoice:
        raise InvalidFileError(
            f"Too many files: {len(specs)} (max {settings.max_files_per_invoice}, "
            f"AP_MAX_FILES_PER_INVOICE)."
        )
    return [_decode_one(spec, i, settings.max_file_bytes) for i, spec in enumerate(specs, start=1)]
