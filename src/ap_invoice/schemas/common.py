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
