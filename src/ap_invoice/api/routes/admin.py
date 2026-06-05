"""Provisioning endpoints (admin-token protected): organizations & API keys."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from ap_invoice.api.deps import DBSession, require_admin
from ap_invoice.api.errors import ConflictError, NotFoundError
from ap_invoice.core.security import generate_api_key
from ap_invoice.models.organization import ApiKey, Organization
from ap_invoice.schemas.organization import (
    ApiKeyCreate,
    ApiKeyCreated,
    ApiKeyRead,
    OrganizationCreate,
    OrganizationRead,
)

router = APIRouter(prefix="/admin", tags=["admin"], dependencies=[Depends(require_admin)])


@router.post(
    "/organizations",
    response_model=OrganizationRead,
    status_code=status.HTTP_201_CREATED,
    summary="Create an organization",
)
async def create_organization(payload: OrganizationCreate, db: DBSession) -> Organization:
    org = Organization(name=payload.name, slug=payload.slug)
    db.add(org)
    try:
        await db.flush()
    except IntegrityError as exc:
        raise ConflictError(f"Organization slug '{payload.slug}' already exists.") from exc
    await db.refresh(org)
    return org


@router.get(
    "/organizations",
    response_model=list[OrganizationRead],
    summary="List organizations",
)
async def list_organizations(db: DBSession) -> list[Organization]:
    result = await db.execute(select(Organization).order_by(Organization.created_at))
    return list(result.scalars().all())


async def _get_org_or_404(db: DBSession, org_id: uuid.UUID) -> Organization:
    org = await db.get(Organization, org_id)
    if org is None:
        raise NotFoundError(f"Organization {org_id} not found.")
    return org


@router.post(
    "/organizations/{org_id}/api-keys",
    response_model=ApiKeyCreated,
    status_code=status.HTTP_201_CREATED,
    summary="Issue an API key (plaintext returned once)",
)
async def create_api_key(org_id: uuid.UUID, payload: ApiKeyCreate, db: DBSession) -> ApiKeyCreated:
    await _get_org_or_404(db, org_id)
    generated = generate_api_key()
    api_key = ApiKey(
        organization_id=org_id,
        name=payload.name,
        prefix=generated.prefix,
        key_hash=generated.key_hash,
        expires_at=payload.expires_at,
    )
    db.add(api_key)
    await db.flush()
    await db.refresh(api_key)
    # Merge the one-time plaintext into the response model.
    return ApiKeyCreated(
        **ApiKeyRead.model_validate(api_key).model_dump(), api_key=generated.full_key
    )


@router.get(
    "/organizations/{org_id}/api-keys",
    response_model=list[ApiKeyRead],
    summary="List an organization's API keys (metadata only)",
)
async def list_api_keys(org_id: uuid.UUID, db: DBSession) -> list[ApiKey]:
    await _get_org_or_404(db, org_id)
    result = await db.execute(
        select(ApiKey).where(ApiKey.organization_id == org_id).order_by(ApiKey.created_at)
    )
    return list(result.scalars().all())


@router.delete(
    "/organizations/{org_id}/api-keys/{key_id}",
    response_model=ApiKeyRead,
    summary="Revoke an API key",
)
async def revoke_api_key(org_id: uuid.UUID, key_id: uuid.UUID, db: DBSession) -> ApiKey:
    from datetime import UTC, datetime

    api_key = await db.get(ApiKey, key_id)
    if api_key is None or api_key.organization_id != org_id:
        raise NotFoundError(f"API key {key_id} not found.")
    if api_key.revoked_at is None:
        api_key.revoked_at = datetime.now(UTC)
    await db.flush()
    await db.refresh(api_key)
    return api_key
