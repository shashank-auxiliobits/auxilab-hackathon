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
from ap_invoice.core.jwt import encode_access_token
from ap_invoice.core.security import generate_api_key, hash_password
from ap_invoice.db.base import Base
from ap_invoice.db.session import dispose_engine, session_scope
from ap_invoice.models.organization import ApiKey, Organization
from ap_invoice.models.user import User

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
    """Provision an organization + API key directly in the DB (no admin endpoint).

    Depends on ``client`` so the app (and its engine) is initialised first.
    """
    async with session_scope() as db:
        organization = Organization(name="Test Org", slug=f"test-{uuid.uuid4().hex[:10]}")
        db.add(organization)
        await db.flush()
        generated = generate_api_key()
        db.add(
            ApiKey(
                organization_id=organization.id,
                name="test",
                prefix=generated.prefix,
                key_hash=generated.key_hash,
            )
        )
        await db.flush()
        return {"org_id": str(organization.id), "api_key": generated.full_key}


@pytest_asyncio.fixture
def auth(org: dict[str, str]) -> dict[str, str]:
    """Bearer header using the org's API key (the default for most tenant tests)."""
    return {"Authorization": f"Bearer {org['api_key']}"}


@pytest_asyncio.fixture
async def user_auth(org: dict[str, str]) -> dict[str, str]:
    """Bearer header using a session JWT for a verified user in the org."""
    async with session_scope() as db:
        user = User(
            organization_id=uuid.UUID(org["org_id"]),
            email=f"user-{uuid.uuid4().hex[:8]}@example.com",
            password_hash=hash_password("test-password"),
            is_email_verified=True,
        )
        db.add(user)
        await db.flush()
        token, _ = encode_access_token(user)
    return {"Authorization": f"Bearer {token}"}
