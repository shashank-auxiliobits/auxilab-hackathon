"""Read-only reporting/aggregation queries over invoices.

Pure, org-scoped helpers shared by the REST API and the MCP server so an agent
(or a dashboard) can answer questions like "how many flagged invoices?" without
either surface duplicating the query.
"""

from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal
from typing import Any

from sqlalchemy import Select, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ap_invoice.core.enums import ApprovalDecision, InvoiceStatus
from ap_invoice.models.invoice import Invoice
from ap_invoice.models.vendor import Vendor


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


# --------------------------------------------------------------------------- #
# Analytics aggregations (spend, aging, automation) — all org-scoped.
# --------------------------------------------------------------------------- #

_SUM = func.coalesce(func.sum(Invoice.grand_total), 0)


def _date_window(stmt: Select[Any], date_from: date | None, date_to: date | None) -> Select[Any]:
    """Apply an optional inclusive invoice-date range to a select."""
    if date_from is not None:
        stmt = stmt.where(Invoice.invoice_date >= date_from)
    if date_to is not None:
        stmt = stmt.where(Invoice.invoice_date <= date_to)
    return stmt


async def spend_by_vendor(
    db: AsyncSession,
    org_id: uuid.UUID,
    *,
    date_from: date | None = None,
    date_to: date | None = None,
    limit: int = 20,
) -> list[dict[str, Any]]:
    """Total spend and invoice count per *canonical* vendor, highest spend first.

    Groups by the resolved vendor (joined from the vendor master) so name variants
    that normalised to the same vendor are summed together; invoices with no
    resolved vendor fall back to their raw name.
    """
    name = func.coalesce(Vendor.canonical_name, Invoice.raw_vendor_name, "(unknown)")
    stmt = (
        select(name.label("vendor"), func.count(Invoice.id), _SUM)
        .select_from(Invoice)
        .join(Vendor, Vendor.id == Invoice.vendor_id, isouter=True)
        .where(Invoice.organization_id == org_id)
        .group_by(name)
        .order_by(_SUM.desc())
        .limit(limit)
    )
    rows = (await db.execute(_date_window(stmt, date_from, date_to))).all()
    return [
        {"vendor": vendor, "invoice_count": int(count), "total_amount": str(Decimal(total))}
        for vendor, count, total in rows
    ]


async def spend_by_month(
    db: AsyncSession,
    org_id: uuid.UUID,
    *,
    date_from: date | None = None,
    date_to: date | None = None,
) -> list[dict[str, Any]]:
    """Total spend and invoice count per calendar month (by invoice_date)."""
    month = func.to_char(func.date_trunc("month", Invoice.invoice_date), "YYYY-MM")
    stmt = (
        select(month.label("month"), func.count(Invoice.id), _SUM)
        .where(Invoice.organization_id == org_id, Invoice.invoice_date.is_not(None))
        .group_by(month)
        .order_by(month)
    )
    rows = (await db.execute(_date_window(stmt, date_from, date_to))).all()
    return [
        {"month": m, "invoice_count": int(count), "total_amount": str(Decimal(total))}
        for m, count, total in rows
    ]


# Buckets, in display order, by days-to-due relative to "as of".
_AGING_BUCKETS = ("overdue", "due_0_7", "due_8_30", "due_31_plus", "no_due_date")


def _aging_key(due: date | None, as_of: date) -> str:
    if due is None:
        return "no_due_date"
    days = (due - as_of).days
    if days < 0:
        return "overdue"
    if days <= 7:
        return "due_0_7"
    if days <= 30:
        return "due_8_30"
    return "due_31_plus"


async def aging_buckets(
    db: AsyncSession, org_id: uuid.UUID, *, as_of: date
) -> dict[str, dict[str, Any]]:
    """Bucket outstanding (not paid/rejected) invoices by days-to-due-date."""
    counts: dict[str, int] = dict.fromkeys(_AGING_BUCKETS, 0)
    totals: dict[str, Decimal] = {b: Decimal(0) for b in _AGING_BUCKETS}
    rows = (
        await db.execute(
            select(Invoice.due_date, func.coalesce(Invoice.grand_total, 0)).where(
                Invoice.organization_id == org_id,
                Invoice.status.not_in([InvoiceStatus.PAID, InvoiceStatus.REJECTED]),
            )
        )
    ).all()
    for due, amount in rows:
        key = _aging_key(due, as_of)
        counts[key] += 1
        totals[key] += Decimal(amount)
    cents = Decimal("0.01")
    return {
        b: {"count": counts[b], "total_amount": str(totals[b].quantize(cents))}
        for b in _AGING_BUCKETS
    }


async def decision_breakdown(db: AsyncSession, org_id: uuid.UUID) -> dict[str, Any]:
    """Counts by recommended (policy) decision plus the touchless automation rate."""
    counts = {d.value: 0 for d in ApprovalDecision}
    counts["none"] = 0
    rows = await db.execute(
        select(Invoice.recommended_action, func.count())
        .where(Invoice.organization_id == org_id)
        .group_by(Invoice.recommended_action)
    )
    for action, count in rows.all():
        key = action.value if isinstance(action, ApprovalDecision) else "none"
        counts[key] = int(count)
    decided = sum(v for k, v in counts.items() if k != "none")
    auto = counts[ApprovalDecision.AUTO_APPROVE.value]
    return {
        "by_decision": counts,
        "decided": decided,
        "auto_approved": auto,
        "automation_rate": round(auto / decided, 4) if decided else 0.0,
    }
