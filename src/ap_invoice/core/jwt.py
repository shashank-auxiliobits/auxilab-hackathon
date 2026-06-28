"""Session tokens (JWT) for human, email/password-authenticated users.

A successful login (or email verification) mints a short-lived HS256 access token
carrying the user id (``sub``) and their organization id (``org_id``). Tenant
endpoints accept this token *or* an API key (see
:func:`ap_invoice.services.auth.authenticate_bearer`); API keys remain the path
for programmatic and MCP clients.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

import jwt

from ap_invoice.core.config import get_settings

if TYPE_CHECKING:
    from ap_invoice.models.user import User

_ALGORITHM = "HS256"
_TOKEN_TYPE = "access"  # noqa: S105 - a claim label, not a secret


class InvalidToken(Exception):
    """Raised when a session token is missing, malformed, expired, or untrusted."""


def encode_access_token(user: User) -> tuple[str, int]:
    """Return ``(jwt, expires_in_seconds)`` for an authenticated user."""
    settings = get_settings()
    expires_in = settings.jwt_expire_minutes * 60
    now = datetime.now(UTC)
    payload = {
        "sub": str(user.id),
        "org_id": str(user.organization_id),
        "type": _TOKEN_TYPE,
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(seconds=expires_in)).timestamp()),
    }
    token = jwt.encode(payload, settings.jwt_secret, algorithm=_ALGORITHM)
    return token, expires_in


def decode_access_token(token: str) -> tuple[uuid.UUID, uuid.UUID]:
    """Validate a session token and return ``(user_id, org_id)``.

    Raises :class:`InvalidToken` on any problem (bad signature, expiry, shape).
    """
    settings = get_settings()
    try:
        claims: dict[str, Any] = jwt.decode(token, settings.jwt_secret, algorithms=[_ALGORITHM])
    except jwt.PyJWTError as exc:
        raise InvalidToken(str(exc)) from exc

    if claims.get("type") != _TOKEN_TYPE:
        raise InvalidToken("Not an access token.")
    try:
        return uuid.UUID(claims["sub"]), uuid.UUID(claims["org_id"])
    except (KeyError, ValueError) as exc:
        raise InvalidToken("Malformed token claims.") from exc
