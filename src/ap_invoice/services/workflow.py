"""Invoice status transitions driven by an agent or a human reviewer.

Payment is intentionally out of scope: the only transitions allowed here are
approve / hold / flag / reject. Every transition is recorded in the audit trail
with the actor and an optional note.
"""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from ap_invoice.core.enums import InvoiceStatus, ProcessingEventType
from ap_invoice.models.audit import ProcessingEvent
from ap_invoice.models.invoice import Invoice
from ap_invoice.models.organization import Organization

# Statuses an agent/reviewer may set directly (payment statuses excluded for now).
ALLOWED_TARGETS: frozenset[InvoiceStatus] = frozenset(
    {
        InvoiceStatus.APPROVED,
        InvoiceStatus.HELD,
        InvoiceStatus.FLAGGED,
        InvoiceStatus.REJECTED,
    }
)


class InvalidTransitionError(ValueError):
    """Raised when a requested status transition is not permitted."""


async def transition_status(
    db: AsyncSession,
    org: Organization,
    invoice: Invoice,
    target: InvoiceStatus,
    *,
    actor: str = "agent",
    note: str | None = None,
) -> Invoice:
    """Set an invoice's status to ``target``, recording an audit event."""
    if target not in ALLOWED_TARGETS:
        allowed = ", ".join(sorted(t.value for t in ALLOWED_TARGETS))
        raise InvalidTransitionError(f"Cannot set status to '{target.value}'. Allowed: {allowed}.")

    previous = invoice.status
    invoice.status = target

    message = f"Status {previous.value} → {target.value} by {actor}."
    if note:
        message = f"{message} Note: {note}"

    event = ProcessingEvent(
        organization_id=org.id,
        invoice_id=invoice.id,
        event_type=ProcessingEventType.STATUS_CHANGED,
        actor=actor,
        decision=target.value,
        message=message,
        details={"from": previous.value, "to": target.value, "note": note},
    )
    db.add(event)
    await db.flush()
    return invoice
