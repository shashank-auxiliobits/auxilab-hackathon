"""Integration tests for vendor & policy CRUD and tenant isolation."""

from __future__ import annotations

import uuid

import pytest
from httpx import AsyncClient

from ap_invoice.core.security import generate_api_key
from ap_invoice.db.session import session_scope
from ap_invoice.models.organization import ApiKey, Organization

pytestmark = pytest.mark.integration


async def _provision_org_auth() -> dict[str, str]:
    """Create a second org + API key directly in the DB; return its Bearer header."""
    async with session_scope() as db:
        org = Organization(name="Other", slug=f"o-{uuid.uuid4().hex[:8]}")
        db.add(org)
        await db.flush()
        generated = generate_api_key()
        db.add(
            ApiKey(
                organization_id=org.id,
                name="k",
                prefix=generated.prefix,
                key_hash=generated.key_hash,
            )
        )
        await db.flush()
    return {"Authorization": f"Bearer {generated.full_key}"}


async def test_create_vendor_with_policy(client: AsyncClient, auth: dict[str, str]) -> None:
    r = await client.post(
        "/vendors",
        headers=auth,
        json={
            "canonical_name": "Microsoft Corporation",
            "aliases": ["MSFT", "Microsoft"],
            "policy": {"payment_terms": "Net 30", "auto_approve_max_amount": "5000"},
        },
    )
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["active_policy"]["version"] == 1
    assert body["active_policy"]["auto_approve_max_amount"] == "5000.00"


async def test_duplicate_vendor_conflict(client: AsyncClient, auth: dict[str, str]) -> None:
    payload = {"canonical_name": "Acme Co"}
    assert (await client.post("/vendors", headers=auth, json=payload)).status_code == 201
    r = await client.post("/vendors", headers=auth, json=payload)
    assert r.status_code == 409


async def test_policy_versioning(client: AsyncClient, auth: dict[str, str]) -> None:
    vid = (await client.post("/vendors", headers=auth, json={"canonical_name": "Globex"})).json()[
        "id"
    ]
    r1 = await client.post(
        f"/vendors/{vid}/policies", headers=auth, json={"payment_terms": "Net 15"}
    )
    r2 = await client.post(
        f"/vendors/{vid}/policies", headers=auth, json={"payment_terms": "Net 45"}
    )
    assert r1.json()["version"] == 1
    assert r2.json()["version"] == 2
    active = await client.get(f"/vendors/{vid}/policies/active", headers=auth)
    assert active.json()["version"] == 2
    assert active.json()["payment_terms"] == "Net 45"
    versions = await client.get(f"/vendors/{vid}/policies", headers=auth)
    assert len(versions.json()) == 2


async def test_update_vendor(client: AsyncClient, auth: dict[str, str]) -> None:
    vid = (await client.post("/vendors", headers=auth, json={"canonical_name": "Initech"})).json()[
        "id"
    ]
    r = await client.patch(f"/vendors/{vid}", headers=auth, json={"status": "inactive"})
    assert r.status_code == 200
    assert r.json()["status"] == "inactive"


async def test_tenant_isolation(client: AsyncClient, auth: dict[str, str]) -> None:
    vid = (
        await client.post("/vendors", headers=auth, json={"canonical_name": "Secret Vendor"})
    ).json()["id"]

    # Provision a second org; it must not see the first org's vendor.
    other_auth = await _provision_org_auth()

    assert (await client.get("/vendors", headers=other_auth)).json()["total"] == 0
    assert (await client.get(f"/vendors/{vid}", headers=other_auth)).status_code == 404
