"""Unit tests for API-key generation and verification."""

from __future__ import annotations

from ap_invoice.core.security import generate_api_key, parse_api_key, verify_secret


def test_generate_and_verify_roundtrip() -> None:
    gen = generate_api_key()
    assert gen.full_key.startswith("ap_")
    parsed = parse_api_key(gen.full_key)
    assert parsed is not None
    prefix, secret = parsed
    assert prefix == gen.prefix
    assert verify_secret(secret, gen.key_hash)


def test_wrong_secret_fails() -> None:
    gen = generate_api_key()
    assert not verify_secret("not-the-secret", gen.key_hash)


def test_malformed_key_returns_none() -> None:
    assert parse_api_key("no-dot-here") is None
    assert parse_api_key("wrongprefix.secret") is None
    assert parse_api_key("ap_abc.") is None


def test_keys_are_unique() -> None:
    assert generate_api_key().full_key != generate_api_key().full_key


def test_verify_handles_garbage_hash() -> None:
    assert not verify_secret("secret", "not-a-valid-argon2-hash")
