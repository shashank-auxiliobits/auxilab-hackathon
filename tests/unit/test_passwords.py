"""Unit tests for password hashing."""

from __future__ import annotations

from ap_invoice.core.security import hash_password, verify_password


def test_hash_then_verify_succeeds() -> None:
    hashed = hash_password("correct horse battery staple")
    assert hashed != "correct horse battery staple"
    assert verify_password("correct horse battery staple", hashed)


def test_wrong_password_fails() -> None:
    hashed = hash_password("right-password")
    assert not verify_password("wrong-password", hashed)


def test_hashes_are_salted_and_distinct() -> None:
    assert hash_password("same") != hash_password("same")


def test_malformed_hash_returns_false() -> None:
    assert not verify_password("anything", "not-a-valid-argon2-hash")
