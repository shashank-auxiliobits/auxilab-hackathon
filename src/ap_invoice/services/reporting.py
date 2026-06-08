"""Read-only reporting/aggregation queries over invoices.

Pure, org-scoped helpers shared by the REST API and the MCP server so an agent
(or a dashboard) can answer questions like "how many flagged invoices?" without
either surface duplicating the query.
"""

from __future__ import annotations

import uuid
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ap_invoice.core.enums import InvoiceStatus
from ap_invoice.models.invoice import Invoice


async def invoice_status_counts(db: AsyncSession, org_id: uuid.UUID) -> dict[str, int]:
    """Return a count of the org's invoices keyed by status (all statuses present)."""
    counts = {status.value: 0 for status in InvoiceStatus}
    rows = await db.execute(
        select(Invoice.status, func.count())
        .where(Invoice.organization_id == org_id)
        .group_by(Invoice.status)
    )
    for status, count in rows.all():
        # status is an InvoiceStatus (enum columns round-trip), but guard for str.
        key = status.value if isinstance(status, InvoiceStatus) else str(status)
        counts[key] = count
    return counts


async def invoice_totals(db: AsyncSession, org_id: uuid.UUID) -> tuple[int, Decimal]:
    """Return (invoice_count, summed_grand_total) for the org."""
    count, total_amount = (
        await db.execute(
            select(func.count(Invoice.id), func.coalesce(func.sum(Invoice.grand_total), 0)).where(
                Invoice.organization_id == org_id
            )
        )
    ).one()
    return int(count), Decimal(total_amount)
