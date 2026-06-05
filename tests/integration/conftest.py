"""Fixtures for integration tests (require a live PostgreSQL database).

The schema is created once per session against ``AP_DATABASE_URL`` (a dedicated
test database) and dropped afterwards. Each test provisions its own organization
so tests are isolated by tenant. The app engine is disposed after every test so
it re-binds to that test's event loop.
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import AsyncIterator, Iterator

import pytest
import pytest_asyncio
from asgi_lifespan import LifespanManager
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import create_async_engine

import ap_invoice.models  # noqa: F401  -- register all tables on Base.metadata
from ap_invoice.core.config import get_settings
from ap_invoice.db.base import Base
from ap_invoice.db.session import dispose_engine

pytestmark = pytest.mark.integration


async def _reset_schema(drop_only: bool = False) -> None:
    engine = create_async_engine(str(get_settings().database_url))
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        if not drop_only:
            await conn.run_sync(Base.metadata.create_all)
    await engine.dispose()


@pytest.fixture(scope="session", autouse=True)
def _schema() -> Iterator[None]:
    """Create all tables before the session and drop them afterwards."""
    asyncio.run(_reset_schema())
    yield
    asyncio.run(_reset_schema(drop_only=True))


@pytest_asyncio.fixture
async def client() -> AsyncIterator[AsyncClient]:
    from ap_invoice.api.main import create_app

    app = create_app()
    async with LifespanManager(app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            yield c
    await dispose_engine()


@pytest_asyncio.fixture
async def org(client: AsyncClient) -> dict[str, str]:
    """Provision an organization + API key; return ids and an auth header value."""
    admin = {"X-Admin-Token": get_settings().admin_token or ""}
    slug = f"test-{uuid.uuid4().hex[:10]}"
    org_resp = await client.post(
        "/admin/organizations", headers=admin, json={"name": "Test Org", "slug": slug}
    )
    assert org_resp.status_code == 201, org_resp.text
    org_id = org_resp.json()["id"]
    key_resp = await client.post(
        f"/admin/organizations/{org_id}/api-keys", headers=admin, json={"name": "test"}
    )
    assert key_resp.status_code == 201, key_resp.text
    return {"org_id": org_id, "api_key": key_resp.json()["api_key"]}


@pytest_asyncio.fixture
def auth(org: dict[str, str]) -> dict[str, str]:
    return {"Authorization": f"Bearer {org['api_key']}"}
