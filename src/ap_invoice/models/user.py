"""User accounts and email-OTP verification (self-service auth)."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ap_invoice.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin

if TYPE_CHECKING:
    from ap_invoice.models.organization import Organization


class User(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """A human account that owns an organization and signs in with email + password.

    Only an Argon2 hash of the password is stored. ``email`` is unique across the
    system and always persisted lowercased. A user must verify their email (via an
    emailed OTP) before they can log in.
    """

    __tablename__ = "users"

    organization_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    email: Mapped[str] = mapped_column(String(320), nullable=False, unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    is_email_verified: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    organization: Mapped[Organization] = relationship(back_populates="users")
    verifications: Mapped[list[EmailVerification]] = relationship(
        back_populates="user",
        cascade="all, delete-orphan",
    )


class EmailVerification(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """A one-time email verification code (hashed), with expiry and attempt limits.

    On resend a new row is created; only the latest unconsumed, unexpired row is
    honoured. ``attempts`` counts failed guesses so a code can be locked out.
    """

    __tablename__ = "email_verifications"

    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    code_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    user: Mapped[User] = relationship(back_populates="verifications")
