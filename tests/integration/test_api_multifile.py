"""Integration tests for multi-file invoice ingestion and processing.

Exercises the ``files`` list on /invoices/process and /invoices/ingest end to end
(positive paths) and the error mapping for malformed/oversized/unsupported uploads
(negative paths). The LLM is stubbed by the autouse fixture in tests/conftest.py,
so the fake OCR reads the supplied ``raw_text``; the attached files exercise the
decode + content-building plumbing.
"""

from __future__ import annotations

import base64

import pytest
from httpx import AsyncClient

pytestmark = pytest.mark.integration

PNG = base64.b64encode(b"\x89PNG\r\n\x1a\n" + b"\x00" * 16).decode()
JPEG = base64.b64encode(b"\xff\xd8\xff\xe0" + b"\x00" * 16).decode()

MSFT_POLICY = (
    "Microsoft Corporation — Accounts Payable Policy.\n"
    "Invoices must not exceed $5,000.\n"
    "All invoices must be issued in USD.\n"
)
CLEAN = (
    "Microsoft\nInvoice Number: INV-MF1\nInvoice Date: 2026-06-01\n"
    "Payment Terms: 2/10 Net 30\nGrand Total: $1,250.00"
)


async def _create_msft(client: AsyncClient, auth: dict[str, str]) -> None:
    vid = (
        await client.post(
            "/vendors",
            headers=auth,
            json={"canonical_name": "Microsoft Corporation", "aliases": ["Microsoft"]},
        )
    ).json()["id"]
    await client.post(
        f"/vendors/{vid}/documents",
        headers=auth,
        json={"filename": "policy.txt", "text": MSFT_POLICY, "compile": False},
    )


# --------------------------------------------------------------------------- #
# Positive paths
# --------------------------------------------------------------------------- #


async def test_process_with_multiple_files(client: AsyncClient, auth: dict[str, str]) -> None:
    await _create_msft(client, auth)
    r = await client.post(
        "/invoices/process",
        headers=auth,
        json={
            "raw_text": CLEAN,
            "files": [
                {"file_base64": PNG, "content_type": "image/png", "filename": "page1.png"},
                {"file_base64": JPEG, "content_type": "image/jpeg", "filename": "page2.jpg"},
            ],
        },
    )
    assert r.status_code == 201, r.text
    assert r.json()["decision"] == "auto_approve"


async def test_process_infers_content_type(client: AsyncClient, auth: dict[str, str]) -> None:
    """A file with no content_type still works — the type is sniffed from magic bytes."""
    await _create_msft(client, auth)
    r = await client.post(
        "/invoices/process",
        headers=auth,
        json={"raw_text": CLEAN, "files": [{"file_base64": PNG}]},
    )
    assert r.status_code == 201, r.text


async def test_ingest_with_files_only(client: AsyncClient, auth: dict[str, str]) -> None:
    r = await client.post(
        "/invoices/ingest",
        headers=auth,
        json={"files": [{"file_base64": PNG, "content_type": "image/png"}]},
    )
    assert r.status_code == 201, r.text


# --------------------------------------------------------------------------- #
# Negative paths
# --------------------------------------------------------------------------- #


async def test_bad_base64_in_files_returns_422(client: AsyncClient, auth: dict[str, str]) -> None:
    r = await client.post(
        "/invoices/process",
        headers=auth,
        json={"raw_text": CLEAN, "files": [{"file_base64": "!!!not base64!!!"}]},
    )
    assert r.status_code == 422, r.text
    assert r.json()["error"]["code"] == "validation_error"


async def test_unsupported_file_type_returns_422(client: AsyncClient, auth: dict[str, str]) -> None:
    zip_b64 = base64.b64encode(b"PK\x03\x04 not an invoice").decode()
    r = await client.post(
        "/invoices/process",
        headers=auth,
        json={
            "raw_text": CLEAN,
            "files": [{"file_base64": zip_b64, "content_type": "application/zip"}],
        },
    )
    assert r.status_code == 422, r.text


async def test_too_many_files_returns_422(client: AsyncClient, auth: dict[str, str]) -> None:
    r = await client.post(
        "/invoices/process",
        headers=auth,
        json={"raw_text": CLEAN, "files": [{"file_base64": PNG} for _ in range(11)]},
    )
    assert r.status_code == 422, r.text


async def test_no_text_or_file_returns_422(client: AsyncClient, auth: dict[str, str]) -> None:
    r = await client.post("/invoices/process", headers=auth, json={"source": "api"})
    assert r.status_code == 422, r.text
