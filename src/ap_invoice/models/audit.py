"""Append-only audit trail for invoice processing.

Every meaningful action — extraction, vendor match, duplicate check, completeness
check, policy evaluation, decision, status change — is recorded here. Rows are
never updated or deleted, giving finance teams a complete, reproducible history
for compliance and dispute resolution.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy import DateTime, ForeignKey, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ap_invoice.core.enums import ProcessingEventType
from ap_invoice.db.base import Base, UUIDPrimaryKeyMixin, str_enum

if TYPE_CHECKING:
    from ap_invoice.models.invoice import Invoice


class ProcessingEvent(UUIDPrimaryKeyMixin, Base):
    """One immutable audit-trail entry."""

    __tablename__ = "processing_events"

    organization_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    invoice_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("invoices.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )
    event_type: Mapped[ProcessingEventType] = mapped_column(
        str_enum(ProcessingEventType, length=40), nullable=False
    )
    # Who/what performed the action, e.g. "agent:claude", "system", "user:alice@co".
    actor: Mapped[str] = mapped_column(String(255), nullable=False, default="system")
    tool_name: Mapped[str | None] = mapped_column(String(100), nullable=True)
    decision: Mapped[str | None] = mapped_column(String(40), nullable=True)
    message: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Full structured payload of the check/result for reproducibility.
    details: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
        index=True,
    )

    invoice: Mapped[Invoice | None] = relationship(back_populates="events")
