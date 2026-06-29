"""Integration tests for the analytics aggregation queries (reporting.py).

Seeds invoices with known amounts/dates/due-dates/decisions directly in the DB and
asserts the exact aggregation output, so the spend / aging / automation math is
pinned down independently of the MCP/REST layer.
"""

from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal

import pytest
import pytest_asyncio

from ap_invoice.core.enums import ApprovalDecision, InvoiceStatus, VendorStatus
from ap_invoice.db.session import session_scope
from ap_invoice.models.invoice import Invoice
from ap_invoice.models.organization import Organization
from ap_invoice.models.vendor import Vendor
from ap_invoice.services.reporting import (
    aging_buckets,
    decision_breakdown,
    spend_by_month,
    spend_by_vendor,
)

pytestmark = pytest.mark.integration

AS_OF = date(2026, 3, 1)


@pytest_asyncio.fixture
async def seeded_org() -> uuid.UUID:
    """An org with three invoices spanning vendors, months, due-dates, and decisions."""
    async with session_scope() as db:
        org = Organization(name="Rep Org", slug=f"rep-{uuid.uuid4().hex[:8]}")
        db.add(org)
        await db.flush()
        db.add_all(
            [
                Invoice(
                    organization_id=org.id,
                    raw_vendor_name="Globex",
                    invoice_number="A",
                    invoice_date=date(2026, 1, 15),
                    due_date=date(2026, 3, 5),  # +4 days -> due_0_7
                    grand_total=Decimal("1000"),
                    status=InvoiceStatus.APPROVED,
                    recommended_action=ApprovalDecision.AUTO_APPROVE,
                ),
                Invoice(
                    organization_id=org.id,
                    raw_vendor_name="Globex",
                    invoice_number="B",
                    invoice_date=date(2026, 1, 20),
                    due_date=date(2026, 2, 25),  # -4 days -> overdue
                    grand_total=Decimal("2000"),
                    status=InvoiceStatus.FLAGGED,
                    recommended_action=ApprovalDecision.FLAG,
                ),
                Invoice(
                    organization_id=org.id,
                    raw_vendor_name="Initech",
                    invoice_number="C",
                    invoice_date=date(2026, 2, 10),
                    due_date=None,  # -> no_due_date
                    grand_total=Decimal("500"),
                    status=InvoiceStatus.HELD,
                    recommended_action=ApprovalDecision.HOLD,
                ),
            ]
        )
        await db.flush()
        return org.id


async def test_spend_by_vendor(seeded_org: uuid.UUID) -> None:
    async with session_scope() as db:
        rows = await spend_by_vendor(db, seeded_org)
    assert rows[0] == {"vendor": "Globex", "invoice_count": 2, "total_amount": "3000.00"}
    assert rows[1] == {"vendor": "Initech", "invoice_count": 1, "total_amount": "500.00"}


async def test_spend_by_month(seeded_org: uuid.UUID) -> None:
    async with session_scope() as db:
        rows = await spend_by_month(db, seeded_org)
    by_month = {r["month"]: r for r in rows}
    assert by_month["2026-01"]["total_amount"] == "3000.00"
    assert by_month["2026-01"]["invoice_count"] == 2
    assert by_month["2026-02"]["total_amount"] == "500.00"


async def test_aging_buckets(seeded_org: uuid.UUID) -> None:
    async with session_scope() as db:
        buckets = await aging_buckets(db, seeded_org, as_of=AS_OF)
    assert buckets["overdue"] == {"count": 1, "total_amount": "2000.00"}
    assert buckets["due_0_7"] == {"count": 1, "total_amount": "1000.00"}
    assert buckets["no_due_date"] == {"count": 1, "total_amount": "500.00"}
    assert buckets["due_8_30"] == {"count": 0, "total_amount": "0.00"}


async def test_decision_breakdown(seeded_org: uuid.UUID) -> None:
    async with session_scope() as db:
        result = await decision_breakdown(db, seeded_org)
    assert result["decided"] == 3
    assert result["auto_approved"] == 1
    assert result["automation_rate"] == pytest.approx(1 / 3, abs=1e-4)
    assert result["by_decision"]["flag"] == 1
    assert result["by_decision"]["hold"] == 1


async def test_spend_by_vendor_groups_by_canonical_vendor() -> None:
    """Raw-name variants that resolved to one vendor are summed under the canonical name."""
    async with session_scope() as db:
        org = Organization(name="Canon", slug=f"canon-{uuid.uuid4().hex[:8]}")
        db.add(org)
        await db.flush()
        vendor = Vendor(
            organization_id=org.id,
            canonical_name="Globex Corporation",
            aliases=["Globex"],
            status=VendorStatus.ACTIVE,
        )
        db.add(vendor)
        await db.flush()
        db.add_all(
            [
                Invoice(
                    organization_id=org.id,
                    vendor_id=vendor.id,
                    raw_vendor_name="Globex",
                    invoice_number="X1",
                    grand_total=Decimal("100"),
                    status=InvoiceStatus.APPROVED,
                ),
                Invoice(
                    organization_id=org.id,
                    vendor_id=vendor.id,
                    raw_vendor_name="GLOBEX CORP.",  # different OCR string, same vendor
                    invoice_number="X2",
                    grand_total=Decimal("200"),
                    status=InvoiceStatus.APPROVED,
                ),
            ]
        )
        await db.flush()
        rows = await spend_by_vendor(db, org.id)
    assert rows == [{"vendor": "Globex Corporation", "invoice_count": 2, "total_amount": "300.00"}]


async def test_empty_org_is_safe() -> None:
    async with session_scope() as db:
        org = Organization(name="Empty", slug=f"empty-{uuid.uuid4().hex[:8]}")
        db.add(org)
        await db.flush()
        assert await spend_by_vendor(db, org.id) == []
        assert (await decision_breakdown(db, org.id))["automation_rate"] == 0.0
        buckets = await aging_buckets(db, org.id, as_of=AS_OF)
        assert all(b["count"] == 0 for b in buckets.values())
