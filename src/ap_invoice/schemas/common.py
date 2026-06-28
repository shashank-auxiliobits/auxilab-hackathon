"""Shared Pydantic base models and helpers."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class APIModel(BaseModel):
    """Base for request/response models (camel-free, strict-ish)."""

    model_config = ConfigDict(
        from_attributes=True,
        extra="forbid",
        str_strip_whitespace=True,
    )


class InvoiceFileInput(APIModel):
    """One uploaded invoice file (a page or supporting attachment).

    Used for multi-file invoices — e.g. a scan split into per-page images, or an
    invoice plus its attachments. Each file is base64-encoded; all files supplied
    on a request are extracted together as a single logical invoice.
    """

    file_base64: str = Field(
        min_length=1, description="Base64-encoded file contents (image or PDF)."
    )
    content_type: str | None = Field(
        default=None,
        max_length=128,
        description="MIME type of the file, e.g. 'image/png' or 'application/pdf'.",
    )
    filename: str | None = Field(
        default=None,
        max_length=255,
        description="Original filename, for the audit trail (optional).",
    )


class ORMModel(BaseModel):
    """Base for models read directly from ORM objects."""

    model_config = ConfigDict(from_attributes=True)


class PageParams(BaseModel):
    """Standard pagination query parameters."""

    limit: int = Field(default=50, ge=1, le=200)
    offset: int = Field(default=0, ge=0)


class Page[T](ORMModel):
    """A paginated result envelope."""

    items: list[T]
    total: int
    limit: int
    offset: int


class Message(BaseModel):
    """Generic message response."""

    detail: str
