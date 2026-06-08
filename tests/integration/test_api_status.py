"""Integration tests for status transitions and auto-onboarding."""

from __future__ import annotations

import pytest
from httpx import AsyncClient

pytestmark = pytest.mark.integration

UNKNOWN = (
    "Globex Industries\nInvoice Number: INV-AO-1\nInvoice Date: 2026-06-01\n"
    "Payment Terms: Net 30\nGrand Total: $500.00"
)


async def test_agent_can_set_status(client: AsyncClient, auth: dict[str, str]) -> None:
    inv = (await client.post("/invoices/process", headers=auth, json={"raw_text": UNKNOWN})).json()
    inv_id = inv["invoice_id"]
    r = await client.post(
        f"/invoices/{inv_id}/status",
        headers=auth,
        json={"status": "approved", "actor": "agent:test", "note": "looks good"},
    )
    assert r.status_code == 200
    assert r.json()["status"] == "approved"

    # The transition is in the audit trail.
    events = (await client.get(f"/invoices/{inv_id}/events", headers=auth)).json()
    assert any(e["event_type"] == "status_changed" and e["decision"] == "approved" for e in events)


async def test_invalid_status_rejected(client: AsyncClient, auth: dict[str, str]) -> None:
    inv = (await client.post("/invoices/process", headers=auth, json={"raw_text": UNKNOWN})).json()
    r = await client.post(
        f"/invoices/{inv['invoice_id']}/status", headers=auth, json={"status": "paid"}
    )
    assert r.status_code == 422


async def test_auto_onboard_creates_vendor_and_holds(
    client: AsyncClient, auth: dict[str, str]
) -> None:
    # No vendors yet; auto_onboard (default) should create one and hold the invoice.
    r = await client.post(
        "/invoices/process", headers=auth, json={"raw_text": UNKNOWN, "auto_onboard": True}
    )
    body = r.json()
    assert body["status"] == "held"
    vendors = (await client.get("/vendors", headers=auth)).json()
    assert any(v["canonical_name"] == "Globex Industries" for v in vendors["items"])
    assert any(v["status"] == "onboarding" for v in vendors["items"])


async def test_no_auto_onboard_leaves_vendor_unrecognised(
    client: AsyncClient, auth: dict[str, str]
) -> None:
    r = await client.post(
        "/invoices/process", headers=auth, json={"raw_text": UNKNOWN, "auto_onboard": False}
    )
    body = r.json()
    assert body["vendor"]["is_recognized"] is False
    assert body["status"] == "held"
    assert (await client.get("/vendors", headers=auth)).json()["total"] == 0
