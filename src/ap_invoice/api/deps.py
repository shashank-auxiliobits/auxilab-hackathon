"""FastAPI dependencies: database session, API-key auth, admin guard."""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends, Header
from sqlalchemy.ext.asyncio import AsyncSession

from ap_invoice.api.errors import AuthenticationError, AuthorizationError
from ap_invoice.core.config import Settings, get_settings
from ap_invoice.db.session import get_db
from ap_invoice.models.organization import Organization
from ap_invoice.services.auth import authenticate_api_key

DBSession = Annotated[AsyncSession, Depends(get_db)]
SettingsDep = Annotated[Settings, Depends(get_settings)]


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
    """Authenticate the request via API key and return the owning organization."""
    token = _extract_token(authorization, x_api_key)
    if not token:
        raise AuthenticationError("Missing API key. Send 'Authorization: Bearer <key>'.")

    org = await authenticate_api_key(db, token)
    if org is None:
        raise AuthenticationError("Invalid, revoked, or expired API key.")
    return org


CurrentOrg = Annotated[Organization, Depends(get_current_org)]


async def require_admin(
    settings: SettingsDep,
    authorization: Annotated[str | None, Header()] = None,
    x_admin_token: Annotated[str | None, Header(alias="X-Admin-Token")] = None,
) -> None:
    """Guard provisioning endpoints with the configured admin token."""
    if not settings.admin_token:
        raise AuthorizationError(
            "Admin endpoints are disabled. Set AP_ADMIN_TOKEN to enable provisioning."
        )
    token = _extract_token(authorization, None) or (
        x_admin_token.strip() if x_admin_token else None
    )
    if not token or not _constant_time_eq(token, settings.admin_token):
        raise AuthenticationError("Invalid admin token.")


def _constant_time_eq(a: str, b: str) -> bool:
    import hmac

    return hmac.compare_digest(a, b)
