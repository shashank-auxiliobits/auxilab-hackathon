"""User accounts: registration, email-OTP verification, and login.

Transport-agnostic business logic shared by the auth API routes. All rules
(duplicate email, OTP expiry / attempts / single-use, must-be-verified login)
live here and surface as the domain exceptions below, which the API layer maps
to HTTP status codes.
"""

from __future__ import annotations

import re
import secrets
from datetime import UTC, datetime, timedelta

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from ap_invoice.core.config import get_settings
from ap_invoice.core.security import (
    generate_otp,
    hash_otp,
    hash_password,
    verify_otp,
    verify_password,
)
from ap_invoice.db.session import session_scope
from ap_invoice.models.organization import Organization
from ap_invoice.models.user import EmailVerification, User
from ap_invoice.services.email import EmailMessage, EmailSender


class AccountError(Exception):
    """Base class for account-flow errors."""


class EmailAlreadyRegistered(AccountError):
    """A verified account already exists for this email."""


class InvalidCredentials(AccountError):
    """Email/password did not match."""


class EmailNotVerified(AccountError):
    """The account exists but its email has not been verified yet."""


class OtpError(AccountError):
    """The verification code is missing, wrong, expired, or exhausted."""


def _normalize_email(email: str) -> str:
    return email.strip().lower()


def _slugify(name: str) -> str:
    base = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:80] or "org"
    return f"{base}-{secrets.token_hex(4)}"


async def get_user_by_email(db: AsyncSession, email: str) -> User | None:
    return (
        await db.execute(select(User).where(User.email == _normalize_email(email)))
    ).scalar_one_or_none()


async def _latest_pending_verification(
    db: AsyncSession, user_id: object
) -> EmailVerification | None:
    return (
        await db.execute(
            select(EmailVerification)
            .where(EmailVerification.user_id == user_id, EmailVerification.consumed_at.is_(None))
            .order_by(EmailVerification.created_at.desc())
            .limit(1)
        )
    ).scalar_one_or_none()


async def issue_otp(db: AsyncSession, user: User, sender: EmailSender) -> None:
    """Create a fresh OTP for the user and send it via the configured email backend."""
    settings = get_settings()
    code = generate_otp(settings.otp_length)
    expires_at = datetime.now(UTC) + timedelta(minutes=settings.otp_ttl_minutes)
    db.add(EmailVerification(user_id=user.id, code_hash=hash_otp(code), expires_at=expires_at))
    await db.flush()
    await sender.send(
        EmailMessage(
            to=user.email,
            subject="Your AP Invoice verification code",
            body=(
                f"Your verification code is {code}\n\n"
                f"It expires in {settings.otp_ttl_minutes} minutes."
            ),
        )
    )


async def register_user(
    db: AsyncSession,
    *,
    email: str,
    password: str,
    organization_name: str | None,
    sender: EmailSender,
) -> User:
    """Create an organization + owner user (unverified) and send a verification OTP.

    Re-registering an *unverified* email updates the password and resends a code
    (so a typo'd password before verifying isn't a dead end). A *verified* email
    raises :class:`EmailAlreadyRegistered`.
    """
    email = _normalize_email(email)
    existing = await get_user_by_email(db, email)
    if existing is not None:
        if existing.is_email_verified:
            raise EmailAlreadyRegistered(email)
        existing.password_hash = hash_password(password)
        await db.flush()
        await issue_otp(db, existing, sender)
        return existing

    org_name = (organization_name or "").strip() or f"{email.split('@')[0]}'s workspace"
    org = Organization(name=org_name, slug=_slugify(org_name))
    db.add(org)
    await db.flush()

    user = User(
        organization_id=org.id,
        email=email,
        password_hash=hash_password(password),
        is_email_verified=False,
    )
    db.add(user)
    await db.flush()
    await issue_otp(db, user, sender)
    return user


async def verify_email_otp(db: AsyncSession, *, email: str, code: str) -> User:
    """Verify an emailed OTP. Marks the email verified, or raises :class:`OtpError`."""
    user = await get_user_by_email(db, email)
    if user is None:
        raise OtpError("No pending verification for this email.")
    if user.is_email_verified:
        return user

    verification = await _latest_pending_verification(db, user.id)
    if verification is None:
        raise OtpError("No pending verification code; request a new one.")
    if verification.expires_at < datetime.now(UTC):
        raise OtpError("Verification code has expired; request a new one.")
    if verification.attempts >= get_settings().otp_max_attempts:
        raise OtpError("Too many incorrect attempts; request a new code.")
    if not verify_otp(code, verification.code_hash):
        # Persist the failed attempt in its own transaction: the API returns 4xx
        # here, and the request-scoped session is rolled back on 4xx — so the
        # counter would otherwise never stick and the cap could never trip.
        async with session_scope() as side:
            await side.execute(
                update(EmailVerification)
                .where(EmailVerification.id == verification.id)
                .values(attempts=verification.attempts + 1)
            )
        raise OtpError("Incorrect verification code.")

    verification.consumed_at = datetime.now(UTC)
    user.is_email_verified = True
    await db.flush()
    return user


async def resend_otp(db: AsyncSession, *, email: str, sender: EmailSender) -> None:
    """Issue a fresh OTP for an unverified account. Silently no-ops otherwise.

    The no-op (rather than an error) avoids leaking which emails are registered.
    """
    user = await get_user_by_email(db, email)
    if user is not None and not user.is_email_verified:
        await issue_otp(db, user, sender)


async def authenticate_user(db: AsyncSession, *, email: str, password: str) -> User:
    """Return the user for valid, verified credentials, or raise.

    Raises :class:`InvalidCredentials` on a bad email/password and
    :class:`EmailNotVerified` if the account hasn't completed verification.
    """
    user = await get_user_by_email(db, email)
    if user is None or not verify_password(password, user.password_hash):
        raise InvalidCredentials(email)
    if not user.is_email_verified:
        raise EmailNotVerified(email)
    user.last_login_at = datetime.now(UTC)
    await db.flush()
    return user
