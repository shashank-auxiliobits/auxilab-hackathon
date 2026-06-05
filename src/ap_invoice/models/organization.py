"""Organization and API-key models (the multi-tenancy root)."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import Boolean, DateTime, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ap_invoice.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin

if TYPE_CHECKING:
    from ap_invoice.models.invoice import Invoice
    from ap_invoice.models.vendor import Vendor


class Organization(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """A tenant. All vendors, invoices, and API keys are scoped to an org."""

    __tablename__ = "organizations"

    name: Mapped[str] = mapped_column(String(255), nullable=False)
    slug: Mapped[str] = mapped_column(String(120), nullable=False, unique=True, index=True)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    api_keys: Mapped[list[ApiKey]] = relationship(
        back_populates="organization",
        cascade="all, delete-orphan",
    )
    vendors: Mapped[list[Vendor]] = relationship(
        back_populates="organization",
        cascade="all, delete-orphan",
    )
    invoices: Mapped[list[Invoice]] = relationship(
        back_populates="organization",
        cascade="all, delete-orphan",
    )


class ApiKey(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """A hashed API key scoped to one organization.

    Only an Argon2 hash of the secret is stored — the plaintext is shown exactly
    once at creation. ``prefix`` is the public, non-secret leading segment used
    to look up the candidate key before verifying the hash.
    """

    __tablename__ = "api_keys"

    organization_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    prefix: Mapped[str] = mapped_column(String(16), nullable=False, unique=True, index=True)
    key_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    organization: Mapped[Organization] = relationship(back_populates="api_keys")

    @property
    def is_revoked(self) -> bool:
        return self.revoked_at is not None
