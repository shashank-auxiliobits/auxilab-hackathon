"""API-key generation, hashing, and verification.

Keys have the form ``ap_<prefix>.<secret>``. Only an Argon2 hash of the secret
(mixed with a server-side pepper) is stored; the plaintext is shown exactly once
at creation. The public ``prefix`` is stored in clear text and used to look up
the candidate key before the constant-time hash verification.
"""

from __future__ import annotations

import secrets

from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError

from ap_invoice.core.config import get_settings

_PREFIX_NAMESPACE = "ap"
_hasher = PasswordHasher()


class GeneratedKey:
    """A freshly generated API key (plaintext is only available here)."""

    __slots__ = ("full_key", "key_hash", "prefix")

    def __init__(self, full_key: str, prefix: str, key_hash: str) -> None:
        self.full_key = full_key
        self.prefix = prefix
        self.key_hash = key_hash


def _peppered(secret: str) -> str:
    return f"{secret}{get_settings().api_key_pepper}"


def generate_api_key() -> GeneratedKey:
    """Generate a new API key and its storable hash."""
    prefix = f"{_PREFIX_NAMESPACE}_{secrets.token_hex(6)}"
    secret = secrets.token_urlsafe(32)
    full_key = f"{prefix}.{secret}"
    key_hash = _hasher.hash(_peppered(secret))
    return GeneratedKey(full_key=full_key, prefix=prefix, key_hash=key_hash)


def parse_api_key(full_key: str) -> tuple[str, str] | None:
    """Split a presented key into (prefix, secret), or None if malformed."""
    if "." not in full_key:
        return None
    prefix, _, secret = full_key.partition(".")
    if not prefix.startswith(f"{_PREFIX_NAMESPACE}_") or not secret:
        return None
    return prefix, secret


def verify_secret(secret: str, key_hash: str) -> bool:
    """Constant-time verification of a presented secret against a stored hash."""
    try:
        return _hasher.verify(key_hash, _peppered(secret))
    except VerifyMismatchError:
        return False
    except Exception:
        # Any hashing error (malformed stored hash, etc.) means "not verified".
        return False


def needs_rehash(key_hash: str) -> bool:
    """Whether a stored hash should be upgraded to current Argon2 parameters."""
    return _hasher.check_needs_rehash(key_hash)
