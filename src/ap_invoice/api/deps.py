"""FastAPI dependencies: database session, bearer auth (API key or session JWT)."""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends, Header, Request
from sqlalchemy.ext.asyncio import AsyncSession

from ap_invoice.api.errors import AuthenticationError
from ap_invoice.models.organization import Organization
from ap_invoice.models.user import User
from ap_invoice.services.auth import authenticate_bearer, authenticate_user_token


async def get_db(request: Request) -> AsyncSession:
    """Return the request-scoped session opened by the DB middleware.

    The middleware (``api/main.py``) commits it **before** the response is sent —
    so back-to-back dependent requests never race the commit — and rolls back on
    error. Handlers just use the session and stay declarative.
    """
    return request.state.db  # type: ignore[no-any-return]


DBSession = Annotated[AsyncSession, Depends(get_db)]


def _extract_token(authorization: str | None, x_api_key: str | None) -> str | None:
    """Pull a bearer token from the Authorization header or X-API-Key header."""
    if authorization:
        scheme, _, value = authorization.partition(" ")
        if scheme.lower() == "bearer" and value:
            return value.strip()
    if x_api_key:
        return x_api_key.strip()
    return None


async def get_current_org(
    db: DBSession,
    authorization: Annotated[str | None, Header()] = None,
    x_api_key: Annotated[str | None, Header(alias="X-API-Key")] = None,
) -> Organization:
    """Authenticate via an API key or session JWT and return the owning organization."""
    token = _extract_token(authorization, x_api_key)
    if not token:
        raise AuthenticationError("Missing credentials. Send 'Authorization: Bearer <token>'.")

    org = await authenticate_bearer(db, token)
    if org is None:
        raise AuthenticationError("Invalid, revoked, or expired credentials.")
    return org


CurrentOrg = Annotated[Organization, Depends(get_current_org)]


async def get_current_user(
    db: DBSession,
    authorization: Annotated[str | None, Header()] = None,
) -> User:
    """Authenticate a human user via a session JWT (not an API key)."""
    token = _extract_token(authorization, None)
    if not token:
        raise AuthenticationError("Missing session token. Log in to obtain one.")
    user = await authenticate_user_token(db, token)
    if user is None:
        raise AuthenticationError("Invalid or expired session token.")
    return user


CurrentUser = Annotated[User, Depends(get_current_user)]
