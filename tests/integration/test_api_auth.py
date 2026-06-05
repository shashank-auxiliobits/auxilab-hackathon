"""Integration tests for authentication and health endpoints."""

from __future__ import annotations

import pytest
from httpx import AsyncClient

pytestmark = pytest.mark.integration


async def test_health_live(client: AsyncClient) -> None:
    r = await client.get("/health/live")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"


async def test_health_ready(client: AsyncClient) -> None:
    r = await client.get("/health/ready")
    assert r.status_code == 200
    assert r.json()["database"] == "ok"


async def test_missing_key_unauthorized(client: AsyncClient) -> None:
    r = await client.get("/vendors")
    assert r.status_code == 401
    assert r.json()["error"]["code"] == "authentication_error"


async def test_bad_key_unauthorized(client: AsyncClient) -> None:
    r = await client.get("/vendors", headers={"Authorization": "Bearer ap_deadbeef.nope"})
    assert r.status_code == 401


async def test_wrong_admin_token(client: AsyncClient) -> None:
    r = await client.post(
        "/admin/organizations",
        headers={"X-Admin-Token": "wrong"},
        json={"name": "X", "slug": "x-org"},
    )
    assert r.status_code == 401


async def test_valid_key_authorized(client: AsyncClient, auth: dict[str, str]) -> None:
    r = await client.get("/vendors", headers=auth)
    assert r.status_code == 200
    assert r.json()["total"] == 0
