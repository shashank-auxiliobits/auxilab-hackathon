"""Unit tests for session-token encode/decode."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import jwt as pyjwt
import pytest

from ap_invoice.core.config import get_settings
from ap_invoice.core.jwt import InvalidToken, decode_access_token, encode_access_token
from ap_invoice.models.user import User


def _user() -> User:
    return User(
        id=uuid.uuid4(),
        organization_id=uuid.uuid4(),
        email="user@example.com",
        password_hash="x",
        is_email_verified=True,
    )


def test_round_trip() -> None:
    user = _user()
    token, expires_in = encode_access_token(user)
    user_id, org_id = decode_access_token(token)
    assert user_id == user.id
    assert org_id == user.organization_id
    assert expires_in == get_settings().jwt_expire_minutes * 60


def test_garbage_token_raises() -> None:
    with pytest.raises(InvalidToken):
        decode_access_token("not.a.jwt")


def test_wrong_signature_raises() -> None:
    token = pyjwt.encode(
        {"sub": str(uuid.uuid4()), "org_id": str(uuid.uuid4()), "type": "access"},
        "a-different-secret-key-of-sufficient-length",
        algorithm="HS256",
    )
    with pytest.raises(InvalidToken):
        decode_access_token(token)


def test_expired_token_raises() -> None:
    past = datetime.now(UTC) - timedelta(minutes=5)
    token = pyjwt.encode(
        {
            "sub": str(uuid.uuid4()),
            "org_id": str(uuid.uuid4()),
            "type": "access",
            "exp": int(past.timestamp()),
        },
        get_settings().jwt_secret,
        algorithm="HS256",
    )
    with pytest.raises(InvalidToken):
        decode_access_token(token)


def test_wrong_token_type_raises() -> None:
    token = pyjwt.encode(
        {"sub": str(uuid.uuid4()), "org_id": str(uuid.uuid4()), "type": "refresh"},
        get_settings().jwt_secret,
        algorithm="HS256",
    )
    with pytest.raises(InvalidToken):
        decode_access_token(token)
