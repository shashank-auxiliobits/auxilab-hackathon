"""Integration tests for the end-to-end processing pipeline + audit trail."""

from __future__ import annotations

import pytest
from httpx import AsyncClient

pytestmark = pytest.mark.integration


async def _create_msft(client: AsyncClient, auth: dict[str, str]) -> None:
    await client.post(
        "/vendors",
        headers=auth,
        json={
            "canonical_name": "Microsoft Corporation",
            "aliases": ["MSFT", "Microsoft"],
            "status": "active",
            "policy": {
                "payment_terms": "2/10 Net 30",
                "auto_approve_max_amount": "5000",
                "requires_review_above_amount": "10000",
            },
        },
    )


CLEAN = "Microsoft\nInvoice Number: INV-P1\nInvoice Date: 2026-06-01\nPayment Terms: 2/10 Net 30\nGrand Total: $1,250.00"


async def test_clean_invoice_auto_approved(client: AsyncClient, auth: dict[str, str]) -> None:
    await _create_msft(client, auth)
    r = await client.post(
        "/invoices/process", headers=auth, json={"raw_text": CLEAN, "actor": "agent:test"}
    )
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["decision"] == "auto_approve"
    assert body["status"] == "approved"
    assert body["vendor"]["match"]["canonical_name"] == "Microsoft Corporation"


async def test_audit_trail_recorded(client: AsyncClient, auth: dict[str, str]) -> None:
    await _create_msft(client, auth)
    inv_id = (
        await client.post("/invoices/process", headers=auth, json={"raw_text": CLEAN})
    ).json()["invoice_id"]
    events = (await client.get(f"/invoices/{inv_id}/events", headers=auth)).json()
    types = [e["event_type"] for e in events]
    assert "vendor_matched" in types
    assert "policy_evaluated" in types
    assert "decision" in types
    assert "status_changed" in types


async def test_duplicate_rejected(client: AsyncClient, auth: dict[str, str]) -> None:
    await _create_msft(client, auth)
    await client.post("/invoices/process", headers=auth, json={"raw_text": CLEAN})
    dup = "MSFT Corp.\nInvoice Number: INV-P1\nInvoice Date: 2026-06-02\nGrand Total: $1,260.00"
    r = await client.post("/invoices/process", headers=auth, json={"raw_text": dup})
    assert r.json()["decision"] == "reject"


async def test_large_invoice_held(client: AsyncClient, auth: dict[str, str]) -> None:
    await _create_msft(client, auth)
    big = "Microsoft\nInvoice Number: INV-BIG\nInvoice Date: 2026-06-01\nPayment Terms: Net 30\nGrand Total: $25,000.00"
    r = await client.post("/invoices/process", headers=auth, json={"raw_text": big})
    assert r.json()["decision"] == "hold"


async def test_unknown_vendor_held(client: AsyncClient, auth: dict[str, str]) -> None:
    unknown = (
        "Globex Industries\nInvoice Number: INV-U1\nInvoice Date: 2026-06-01\nGrand Total: $500.00"
    )
    r = await client.post("/invoices/process", headers=auth, json={"raw_text": unknown})
    body = r.json()
    assert body["decision"] == "hold"
    assert body["vendor"]["is_recognized"] is False
