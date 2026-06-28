"""Transport-agnostic authentication.

Shared by the REST API (FastAPI dependency) and the MCP server so both honour the
same per-organization tenant isolation. API keys back programmatic/MCP access;
session JWTs (issued at login) back human users. ``authenticate_bearer`` accepts
either and is used by the REST API; the MCP server stays API-key only.
"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ap_invoice.core.jwt import InvalidToken, decode_access_token
from ap_invoice.core.security import parse_api_key, verify_secret
from ap_invoice.models.organization import ApiKey, Organization
from ap_invoice.models.user import User


async def authenticate_api_key(db: AsyncSession, token: str | None) -> Organization | None:
    """Resolve an API-key token to its active organization, or None if invalid.

    Validates the key format, looks up the candidate by public prefix, verifies
    the secret in constant time, and checks revocation/expiry plus org activity.
    Updates ``last_used_at`` as a side effect on success.
    """
    if not token:
        return None
    parsed = parse_api_key(token)
    if parsed is None:
        return None
    prefix, secret = parsed

    api_key = (await db.execute(select(ApiKey).where(ApiKey.prefix == prefix))).scalar_one_or_none()
    if api_key is None or not verify_secret(secret, api_key.key_hash):
        return None

    now = datetime.now(UTC)
    if api_key.revoked_at is not None:
        return None
    if api_key.expires_at is not None and api_key.expires_at < now:
        return None

    org = await db.get(Organization, api_key.organization_id)
    if org is None or not org.is_active:
        return None

    api_key.last_used_at = now
    return org


async def authenticate_user_token(db: AsyncSession, token: str | None) -> User | None:
    """Resolve a session JWT to its user, or None if the token is invalid/stale."""
    if not token:
        return None
    try:
        user_id, _org_id = decode_access_token(token)
    except InvalidToken:
        return None
    user = await db.get(User, user_id)
    if user is None or not user.is_email_verified:
        return None
    org = await db.get(Organization, user.organization_id)
    if org is None or not org.is_active:
        return None
    return user


async def authenticate_bearer(db: AsyncSession, token: str | None) -> Organization | None:
    """Resolve a bearer token (API key *or* session JWT) to its active organization."""
    org = await authenticate_api_key(db, token)
    if org is not None:
        return org
    user = await authenticate_user_token(db, token)
    if user is not None:
        return await db.get(Organization, user.organization_id)
    return None
