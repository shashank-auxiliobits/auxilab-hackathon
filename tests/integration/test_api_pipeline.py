"""Integration tests for the end-to-end processing pipeline + audit trail."""

from __future__ import annotations

import pytest
from httpx import AsyncClient

pytestmark = pytest.mark.integration


MSFT_POLICY = (
    "Microsoft Corporation — Accounts Payable Policy.\n"
    "Payment terms are 2/10 Net 30.\n"
    "Invoices must not exceed $5,000.\n"
    "All invoices must be issued in USD.\n"
)


async def _create_msft(client: AsyncClient, auth: dict[str, str]) -> None:
    r = await client.post(
        "/vendors",
        headers=auth,
        json={
            "canonical_name": "Microsoft Corporation",
            "aliases": ["MSFT", "Microsoft"],
            "status": "active",
            "policy": {"payment_terms": "2/10 Net 30"},
        },
    )
    vid = r.json()["id"]
    # Policy is the source of truth — upload it so invoices can be judged against it.
    await client.post(
        f"/vendors/{vid}/documents",
        headers=auth,
        json={"filename": "policy.txt", "text": MSFT_POLICY, "compile": False},
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


async def test_large_invoice_flagged_over_policy_cap(
    client: AsyncClient, auth: dict[str, str]
) -> None:
    await _create_msft(client, auth)
    big = "Microsoft\nInvoice Number: INV-BIG\nInvoice Date: 2026-06-01\nPayment Terms: 2/10 Net 30\nGrand Total: $25,000.00"
    r = await client.post("/invoices/process", headers=auth, json={"raw_text": big})
    # Policy states invoices must not exceed $5,000 → flagged against the policy.
    assert r.json()["decision"] == "flag"


async def test_unknown_vendor_held(client: AsyncClient, auth: dict[str, str]) -> None:
    unknown = (
        "Globex Industries\nInvoice Number: INV-U1\nInvoice Date: 2026-06-01\nGrand Total: $500.00"
    )
    r = await client.post("/invoices/process", headers=auth, json={"raw_text": unknown})
    body = r.json()
    assert body["decision"] == "hold"
    assert body["vendor"]["is_recognized"] is False


async def test_invoice_stats_counts_by_status(client: AsyncClient, auth: dict[str, str]) -> None:
    await _create_msft(client, auth)
    # One approved, one held (unknown vendor).
    await client.post("/invoices/process", headers=auth, json={"raw_text": CLEAN})
    await client.post(
        "/invoices/process",
        headers=auth,
        json={
            "raw_text": "Globex\nInvoice Number: INV-Z1\nInvoice Date: 2026-06-01\nGrand Total: $9.00"
        },
    )
    stats = (await client.get("/invoices/stats", headers=auth)).json()
    assert stats["total_invoices"] == 2
    assert stats["by_status"]["approved"] == 1
    assert stats["by_status"]["held"] == 1
    # Filtered list returns only the flagged/held subset.
    held = (await client.get("/invoices?status=held", headers=auth)).json()
    assert held["total"] == 1
