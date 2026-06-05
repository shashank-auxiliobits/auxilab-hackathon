"""Integration tests for invoice ingestion and CRUD."""

from __future__ import annotations

import pytest
from httpx import AsyncClient

pytestmark = pytest.mark.integration

RAW = """Acme Co
Invoice Number: INV-5001
Invoice Date: 2026-06-01
Payment Terms: Net 30
Grand Total: $999.00
"""


async def test_ingest_extracts_fields(client: AsyncClient, auth: dict[str, str]) -> None:
    r = await client.post(
        "/invoices/ingest?engine=deterministic", headers=auth, json={"raw_text": RAW}
    )
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["invoice_number"] == "INV-5001"
    assert body["grand_total"] == "999.00"
    assert body["status"] == "extracted"
    assert body["extraction_source"] == "deterministic"


async def test_ingest_idempotency(client: AsyncClient, auth: dict[str, str]) -> None:
    payload = {"raw_text": RAW, "idempotency_key": "dup-key-1"}
    r1 = await client.post("/invoices/ingest?engine=deterministic", headers=auth, json=payload)
    r2 = await client.post("/invoices/ingest?engine=deterministic", headers=auth, json=payload)
    assert r1.json()["id"] == r2.json()["id"]


async def test_create_from_fields_and_get(client: AsyncClient, auth: dict[str, str]) -> None:
    r = await client.post(
        "/invoices",
        headers=auth,
        json={
            "raw_vendor_name": "Acme",
            "invoice_number": "INV-6001",
            "grand_total": "100.00",
            "line_items": [
                {
                    "line_number": 1,
                    "description": "Item",
                    "quantity": "1",
                    "unit_price": "100",
                    "line_total": "100",
                }
            ],
        },
    )
    assert r.status_code == 201
    inv_id = r.json()["id"]
    got = await client.get(f"/invoices/{inv_id}", headers=auth)
    assert got.status_code == 200
    assert len(got.json()["line_items"]) == 1


async def test_list_and_filter(client: AsyncClient, auth: dict[str, str]) -> None:
    await client.post("/invoices/ingest?engine=deterministic", headers=auth, json={"raw_text": RAW})
    r = await client.get("/invoices?status=extracted", headers=auth)
    assert r.status_code == 200
    assert r.json()["total"] >= 1


async def test_get_missing_invoice_404(client: AsyncClient, auth: dict[str, str]) -> None:
    r = await client.get("/invoices/00000000-0000-0000-0000-000000000000", headers=auth)
    assert r.status_code == 404
