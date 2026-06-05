"""Vendor and vendor-policy CRUD, scoped to the authenticated organization."""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Query, status
from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError

from ap_invoice.api.deps import CurrentOrg, DBSession
from ap_invoice.api.errors import ConflictError, NotFoundError
from ap_invoice.models.vendor import Vendor, VendorPolicy
from ap_invoice.schemas.common import Page
from ap_invoice.schemas.vendor import (
    VendorCreate,
    VendorPolicyCreate,
    VendorPolicyRead,
    VendorRead,
    VendorUpdate,
    VendorWithPolicy,
)

router = APIRouter(prefix="/vendors", tags=["vendors"])


async def _get_vendor(db: DBSession, org_id: uuid.UUID, vendor_id: uuid.UUID) -> Vendor:
    vendor = await db.get(Vendor, vendor_id)
    if vendor is None or vendor.organization_id != org_id:
        raise NotFoundError(f"Vendor {vendor_id} not found.")
    return vendor


async def _get_active_policy(db: DBSession, vendor_id: uuid.UUID) -> VendorPolicy | None:
    """Fetch a vendor's active policy without triggering async lazy-loading."""
    return (
        await db.execute(
            select(VendorPolicy).where(
                VendorPolicy.vendor_id == vendor_id, VendorPolicy.is_active.is_(True)
            )
        )
    ).scalar_one_or_none()


def _with_policy(vendor: Vendor, policy: VendorPolicy | None) -> VendorWithPolicy:
    data = VendorRead.model_validate(vendor).model_dump()
    active = VendorPolicyRead.model_validate(policy) if policy is not None else None
    return VendorWithPolicy(**data, active_policy=active)


async def _create_policy_version(
    db: DBSession, vendor_id: uuid.UUID, payload: VendorPolicyCreate
) -> VendorPolicy:
    """Create a new active policy version, deactivating any prior active one."""
    max_version = (
        await db.execute(
            select(func.max(VendorPolicy.version)).where(VendorPolicy.vendor_id == vendor_id)
        )
    ).scalar()
    next_version = (max_version or 0) + 1

    await db.execute(
        update(VendorPolicy)
        .where(VendorPolicy.vendor_id == vendor_id, VendorPolicy.is_active.is_(True))
        .values(is_active=False)
    )

    policy = VendorPolicy(
        vendor_id=vendor_id,
        version=next_version,
        is_active=True,
        **payload.model_dump(),
    )
    db.add(policy)
    await db.flush()
    await db.refresh(policy)
    return policy


@router.post(
    "",
    response_model=VendorWithPolicy,
    status_code=status.HTTP_201_CREATED,
    summary="Create a vendor (optionally with an initial policy)",
)
async def create_vendor(payload: VendorCreate, org: CurrentOrg, db: DBSession) -> VendorWithPolicy:
    vendor = Vendor(
        organization_id=org.id,
        canonical_name=payload.canonical_name,
        display_name=payload.display_name,
        aliases=payload.aliases,
        tax_id=payload.tax_id,
        email=payload.email,
        status=payload.status,
        notes=payload.notes,
    )
    db.add(vendor)
    try:
        await db.flush()
    except IntegrityError as exc:
        raise ConflictError(
            f"Vendor '{payload.canonical_name}' already exists in this organization."
        ) from exc

    active_policy = None
    if payload.policy is not None:
        active_policy = await _create_policy_version(db, vendor.id, payload.policy)
    await db.refresh(vendor)
    return _with_policy(vendor, active_policy)


@router.get("", response_model=Page[VendorRead], summary="List vendors")
async def list_vendors(
    org: CurrentOrg,
    db: DBSession,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
    q: Annotated[str | None, Query(description="Filter by canonical name substring.")] = None,
) -> Page[VendorRead]:
    base = select(Vendor).where(Vendor.organization_id == org.id)
    if q:
        base = base.where(Vendor.canonical_name.ilike(f"%{q}%"))
    total = (await db.execute(select(func.count()).select_from(base.subquery()))).scalar_one()
    rows = (
        (await db.execute(base.order_by(Vendor.canonical_name).limit(limit).offset(offset)))
        .scalars()
        .all()
    )
    return Page[VendorRead](
        items=[VendorRead.model_validate(v) for v in rows],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.get("/{vendor_id}", response_model=VendorWithPolicy, summary="Get a vendor")
async def get_vendor(vendor_id: uuid.UUID, org: CurrentOrg, db: DBSession) -> VendorWithPolicy:
    vendor = await _get_vendor(db, org.id, vendor_id)
    active = await _get_active_policy(db, vendor_id)
    return _with_policy(vendor, active)


@router.patch("/{vendor_id}", response_model=VendorRead, summary="Update a vendor")
async def update_vendor(
    vendor_id: uuid.UUID, payload: VendorUpdate, org: CurrentOrg, db: DBSession
) -> Vendor:
    vendor = await _get_vendor(db, org.id, vendor_id)
    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(vendor, field, value)
    try:
        await db.flush()
    except IntegrityError as exc:
        raise ConflictError("Vendor update violates a uniqueness constraint.") from exc
    await db.refresh(vendor)
    return vendor


@router.delete("/{vendor_id}", status_code=status.HTTP_204_NO_CONTENT, summary="Delete a vendor")
async def delete_vendor(vendor_id: uuid.UUID, org: CurrentOrg, db: DBSession) -> None:
    vendor = await _get_vendor(db, org.id, vendor_id)
    await db.delete(vendor)
    await db.flush()


@router.post(
    "/{vendor_id}/policies",
    response_model=VendorPolicyRead,
    status_code=status.HTTP_201_CREATED,
    summary="Create a new policy version for a vendor",
)
async def create_policy(
    vendor_id: uuid.UUID, payload: VendorPolicyCreate, org: CurrentOrg, db: DBSession
) -> VendorPolicy:
    await _get_vendor(db, org.id, vendor_id)
    return await _create_policy_version(db, vendor_id, payload)


@router.get(
    "/{vendor_id}/policies",
    response_model=list[VendorPolicyRead],
    summary="List a vendor's policy versions",
)
async def list_policies(vendor_id: uuid.UUID, org: CurrentOrg, db: DBSession) -> list[VendorPolicy]:
    await _get_vendor(db, org.id, vendor_id)
    rows = (
        (
            await db.execute(
                select(VendorPolicy)
                .where(VendorPolicy.vendor_id == vendor_id)
                .order_by(VendorPolicy.version.desc())
            )
        )
        .scalars()
        .all()
    )
    return list(rows)


@router.get(
    "/{vendor_id}/policies/active",
    response_model=VendorPolicyRead,
    summary="Get a vendor's active policy",
)
async def get_active_policy(vendor_id: uuid.UUID, org: CurrentOrg, db: DBSession) -> VendorPolicy:
    await _get_vendor(db, org.id, vendor_id)
    active = await _get_active_policy(db, vendor_id)
    if active is None:
        raise NotFoundError("Vendor has no active policy.")
    return active
