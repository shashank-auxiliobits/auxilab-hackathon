"""Organization and API-key schemas."""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import Field

from ap_invoice.schemas.common import APIModel, ORMModel


class OrganizationCreate(APIModel):
    name: str = Field(min_length=1, max_length=255)
    slug: str = Field(
        min_length=2,
        max_length=120,
        pattern=r"^[a-z0-9][a-z0-9-]*[a-z0-9]$",
        description="URL-safe lowercase identifier, e.g. 'acme-corp'.",
    )


class OrganizationRead(ORMModel):
    id: uuid.UUID
    name: str
    slug: str
    is_active: bool
    created_at: datetime
    updated_at: datetime


class ApiKeyCreate(APIModel):
    name: str = Field(min_length=1, max_length=255)
    expires_at: datetime | None = Field(
        default=None, description="Optional expiry. Omit for a non-expiring key."
    )


class ApiKeyRead(ORMModel):
    id: uuid.UUID
    organization_id: uuid.UUID
    name: str
    prefix: str
    last_used_at: datetime | None
    expires_at: datetime | None
    revoked_at: datetime | None
    created_at: datetime


class ApiKeyCreated(ApiKeyRead):
    """Returned only once, at creation time, with the plaintext secret."""

    api_key: str = Field(description="The full secret. Store it now — it is never shown again.")
