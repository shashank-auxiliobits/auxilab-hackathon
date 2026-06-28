"""Unit tests for one-time passcode generation and verification."""

from __future__ import annotations

import pytest

from ap_invoice.core.security import generate_otp, hash_otp, verify_otp


@pytest.mark.parametrize("length", [4, 6, 8])
def test_generate_otp_shape(length: int) -> None:
    for _ in range(50):
        code = generate_otp(length)
        assert len(code) == length  # always zero-padded to the requested length
        assert code.isdigit()


def test_hash_then_verify_succeeds() -> None:
    code = generate_otp(6)
    hashed = hash_otp(code)
    assert hashed != code
    assert verify_otp(code, hashed)


def test_wrong_code_fails() -> None:
    code = generate_otp(6)
    wrong = "111111" if code != "111111" else "222222"
    assert not verify_otp(wrong, hash_otp(code))
