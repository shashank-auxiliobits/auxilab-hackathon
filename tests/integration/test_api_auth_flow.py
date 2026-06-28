"""Integration tests for the self-service auth flow: register → verify → login.

The verification OTP is only logged (never returned or stored in plaintext), so a
``_RecordingSender`` is patched in to capture the emailed code the way a real
inbox would receive it.
"""

from __future__ import annotations

import re
import uuid
from datetime import UTC, datetime, timedelta

import pytest
import pytest_asyncio
from httpx import AsyncClient

from ap_invoice.db.session import session_scope
from ap_invoice.services.accounts import _latest_pending_verification, get_user_by_email
from ap_invoice.services.email import EmailMessage

pytestmark = pytest.mark.integration


class _RecordingSender:
    def __init__(self) -> None:
        self.messages: list[EmailMessage] = []

    async def send(self, message: EmailMessage) -> None:
        self.messages.append(message)


@pytest_asyncio.fixture
def mailbox(monkeypatch: pytest.MonkeyPatch) -> _RecordingSender:
    sender = _RecordingSender()
    monkeypatch.setattr("ap_invoice.api.routes.auth.get_email_sender", lambda: sender)
    return sender


def _code(sender: _RecordingSender) -> str:
    match = re.search(r"\b(\d{6})\b", sender.messages[-1].body)
    assert match, f"no code in email body: {sender.messages[-1].body!r}"
    return match.group(1)


def _email() -> str:
    return f"user-{uuid.uuid4().hex[:8]}@example.com"


def _wrong(good: str) -> str:
    """A 6-digit code guaranteed different from ``good``."""
    return "000000" if good != "000000" else "111111"


# --------------------------------------------------------------------------- #
# Happy path
# --------------------------------------------------------------------------- #


async def test_full_signup_flow(client: AsyncClient, mailbox: _RecordingSender) -> None:
    email = _email()

    reg = await client.post(
        "/auth/register",
        json={"email": email, "password": "sup3r-secret-pw", "organization_name": "Acme AP"},
    )
    assert reg.status_code == 201, reg.text
    assert len(mailbox.messages) == 1

    # Cannot log in before verifying.
    early = await client.post("/auth/login", json={"email": email, "password": "sup3r-secret-pw"})
    assert early.status_code == 403

    # Verify with the emailed code → get a session token.
    verify = await client.post("/auth/verify", json={"email": email, "code": _code(mailbox)})
    assert verify.status_code == 200, verify.text
    token = verify.json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}

    me = await client.get("/auth/me", headers=headers)
    assert me.status_code == 200
    assert me.json()["email"] == email
    assert me.json()["organization_name"] == "Acme AP"

    # The JWT authorizes tenant work: create a vendor scoped to the new org.
    vendor = await client.post("/vendors", headers=headers, json={"canonical_name": "Globex"})
    assert vendor.status_code == 201, vendor.text

    # And email+password login now succeeds.
    login = await client.post("/auth/login", json={"email": email, "password": "sup3r-secret-pw"})
    assert login.status_code == 200
    assert login.json()["token_type"] == "bearer"


async def test_self_service_api_key_issuance(
    client: AsyncClient, mailbox: _RecordingSender
) -> None:
    """After verifying, a user mints an API key for programmatic/MCP use."""
    email = _email()
    await client.post("/auth/register", json={"email": email, "password": "sup3r-secret-pw"})
    verify = await client.post("/auth/verify", json={"email": email, "code": _code(mailbox)})
    headers = {"Authorization": f"Bearer {verify.json()['access_token']}"}

    key_resp = await client.post("/api-keys", headers=headers, json={"name": "ci"})
    assert key_resp.status_code == 201, key_resp.text
    api_key = key_resp.json()["api_key"]

    # The minted key works on tenant endpoints too.
    assert (
        await client.get("/vendors", headers={"Authorization": f"Bearer {api_key}"})
    ).status_code == 200


# --------------------------------------------------------------------------- #
# Negative paths
# --------------------------------------------------------------------------- #


async def test_duplicate_verified_email_conflicts(
    client: AsyncClient, mailbox: _RecordingSender
) -> None:
    email = _email()
    await client.post("/auth/register", json={"email": email, "password": "sup3r-secret-pw"})
    await client.post("/auth/verify", json={"email": email, "code": _code(mailbox)})
    again = await client.post("/auth/register", json={"email": email, "password": "another-pw-xx"})
    assert again.status_code == 409


async def test_wrong_password_unauthorized(client: AsyncClient, mailbox: _RecordingSender) -> None:
    email = _email()
    await client.post("/auth/register", json={"email": email, "password": "sup3r-secret-pw"})
    await client.post("/auth/verify", json={"email": email, "code": _code(mailbox)})
    bad = await client.post("/auth/login", json={"email": email, "password": "not-the-password"})
    assert bad.status_code == 401


async def test_bad_otp_rejected(client: AsyncClient, mailbox: _RecordingSender) -> None:
    email = _email()
    await client.post("/auth/register", json={"email": email, "password": "sup3r-secret-pw"})
    bad = await client.post("/auth/verify", json={"email": email, "code": _wrong(_code(mailbox))})
    assert bad.status_code == 422


async def test_short_password_rejected(client: AsyncClient) -> None:
    r = await client.post("/auth/register", json={"email": _email(), "password": "short"})
    assert r.status_code == 422


async def test_expired_otp_rejected(client: AsyncClient, mailbox: _RecordingSender) -> None:
    email = _email()
    await client.post("/auth/register", json={"email": email, "password": "sup3r-secret-pw"})
    # Force the pending code to have expired.
    async with session_scope() as db:
        user = await get_user_by_email(db, email)
        assert user is not None
        verification = await _latest_pending_verification(db, user.id)
        assert verification is not None
        verification.expires_at = datetime.now(UTC) - timedelta(minutes=1)
    r = await client.post("/auth/verify", json={"email": email, "code": _code(mailbox)})
    assert r.status_code == 422
    assert "expired" in r.json()["error"]["detail"].lower()


async def test_otp_attempts_are_capped(client: AsyncClient, mailbox: _RecordingSender) -> None:
    email = _email()
    await client.post("/auth/register", json={"email": email, "password": "sup3r-secret-pw"})
    good = _code(mailbox)
    # Exhaust the attempt budget with wrong guesses.
    for _ in range(5):
        await client.post("/auth/verify", json={"email": email, "code": _wrong(good)})
    # Even the correct code is now locked out until a new one is requested.
    r = await client.post("/auth/verify", json={"email": email, "code": good})
    assert r.status_code == 422
    assert "attempt" in r.json()["error"]["detail"].lower()


async def test_invalid_session_token_unauthorized(client: AsyncClient) -> None:
    r = await client.get("/auth/me", headers={"Authorization": "Bearer not.a.valid.jwt"})
    assert r.status_code == 401


async def test_resend_always_accepted(client: AsyncClient) -> None:
    # Unknown email: still 202 (no account enumeration).
    r = await client.post("/auth/resend", json={"email": _email()})
    assert r.status_code == 202
