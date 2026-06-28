"""API-key management, scoped to the authenticated user's organization.

A logged-in user (session JWT) mints keys for programmatic / MCP access. Replaces
the old admin-token provisioning of keys.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from fastapi import APIRouter, status
from sqlalchemy import select

from ap_invoice.api.deps import CurrentOrg, DBSession
from ap_invoice.api.errors import NotFoundError
from ap_invoice.core.security import generate_api_key
from ap_invoice.models.organization import ApiKey
from ap_invoice.schemas.organization import ApiKeyCreate, ApiKeyCreated, ApiKeyRead

router = APIRouter(prefix="/api-keys", tags=["api keys"])


@router.post(
    "",
    response_model=ApiKeyCreated,
    status_code=status.HTTP_201_CREATED,
    summary="Issue an API key for your organization (plaintext returned once)",
)
async def create_api_key(payload: ApiKeyCreate, org: CurrentOrg, db: DBSession) -> ApiKeyCreated:
    generated = generate_api_key()
    api_key = ApiKey(
        organization_id=org.id,
        name=payload.name,
        prefix=generated.prefix,
        key_hash=generated.key_hash,
        expires_at=payload.expires_at,
    )
    db.add(api_key)
    await db.flush()
    await db.refresh(api_key)
    return ApiKeyCreated(
        **ApiKeyRead.model_validate(api_key).model_dump(), api_key=generated.full_key
    )


@router.get(
    "",
    response_model=list[ApiKeyRead],
    summary="List your organization's API keys (metadata only)",
)
async def list_api_keys(org: CurrentOrg, db: DBSession) -> list[ApiKey]:
    result = await db.execute(
        select(ApiKey).where(ApiKey.organization_id == org.id).order_by(ApiKey.created_at)
    )
    return list(result.scalars().all())


@router.delete(
    "/{key_id}",
    response_model=ApiKeyRead,
    summary="Revoke one of your organization's API keys",
)
async def revoke_api_key(key_id: uuid.UUID, org: CurrentOrg, db: DBSession) -> ApiKey:
    api_key = await db.get(ApiKey, key_id)
    if api_key is None or api_key.organization_id != org.id:
        raise NotFoundError(f"API key {key_id} not found.")
    if api_key.revoked_at is None:
        api_key.revoked_at = datetime.now(UTC)
    await db.flush()
    await db.refresh(api_key)
    return api_key
