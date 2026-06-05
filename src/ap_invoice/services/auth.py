"""Transport-agnostic API-key authentication.

Shared by the REST API (FastAPI dependency) and the MCP server so both honour
the same per-organization key model and tenant isolation.
"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ap_invoice.core.security import parse_api_key, verify_secret
from ap_invoice.models.organization import ApiKey, Organization


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
