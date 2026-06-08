"""Async database engine and session management."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import NullPool

from ap_invoice.core.config import Settings, get_settings

_engine: AsyncEngine | None = None
_sessionmaker: async_sessionmaker[AsyncSession] | None = None


def _build_engine(settings: Settings) -> AsyncEngine:
    if settings.environment == "test":
        # Per-loop test isolation: NullPool opens/closes a connection per use,
        # so no pooled connection outlives the event loop that created it.
        return create_async_engine(
            str(settings.database_url),
            echo=settings.db_echo,
            poolclass=NullPool,
            future=True,
        )
    return create_async_engine(
        str(settings.database_url),
        echo=settings.db_echo,
        pool_size=settings.db_pool_size,
        max_overflow=settings.db_max_overflow,
        pool_pre_ping=True,  # transparently recover from dropped connections
        future=True,
    )


def get_engine() -> AsyncEngine:
    """Return the process-wide async engine, creating it on first use."""
    global _engine
    if _engine is None:
        _engine = _build_engine(get_settings())
    return _engine


def get_sessionmaker() -> async_sessionmaker[AsyncSession]:
    """Return the process-wide async session factory."""
    global _sessionmaker
    if _sessionmaker is None:
        _sessionmaker = async_sessionmaker(
            bind=get_engine(),
            expire_on_commit=False,
            autoflush=False,
        )
    return _sessionmaker


@asynccontextmanager
async def session_scope() -> AsyncIterator[AsyncSession]:
    """Context manager yielding a session that commits on success, rolls back on error.

    Use in scripts, the MCP server, and background tasks. For FastAPI request
    handling, the request-scoped session is created by the DB middleware and
    injected via the ``get_db`` dependency (see ``api/deps.py`` and ``api/main.py``).
    """
    factory = get_sessionmaker()
    async with factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


async def dispose_engine() -> None:
    """Dispose the engine's connection pool (call on application shutdown)."""
    global _engine
    if _engine is not None:
        await _engine.dispose()
        _engine = None
