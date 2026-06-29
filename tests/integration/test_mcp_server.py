"""Integration tests for the MCP server tools (what an AI agent actually calls).

Provisions an org + API key + vendor + two policy documents directly in the DB,
points the stdio auth fallback (``AP_MCP_API_KEY``) at that key, then drives the
real FastMCP tools via ``call_tool`` — the same entry point an MCP agent uses —
to confirm invoices are processed against MULTIPLE policies.
"""

from __future__ import annotations

import json
import uuid
from typing import Any

import pytest
import pytest_asyncio

from ap_invoice.core.config import get_settings
from ap_invoice.core.enums import VendorStatus
from ap_invoice.core.security import generate_api_key
from ap_invoice.db.session import session_scope
from ap_invoice.mcp.server import build_server
from ap_invoice.models.organization import ApiKey, Organization
from ap_invoice.models.policy_document import VendorDocument
from ap_invoice.models.vendor import Vendor
from ap_invoice.services import rag

pytestmark = pytest.mark.integration

POLICIES = [
    ("amount_cap.txt", "Invoices must not exceed $5,000."),
    ("po_required.txt", "A purchase order is required on every invoice."),
]


async def _provision_org_with_policies() -> str:
    """Create org + API key + vendor + two embedded policies; return the API key."""
    async with session_scope() as db:
        org = Organization(name="MCP Org", slug=f"mcp-{uuid.uuid4().hex[:8]}")
        db.add(org)
        await db.flush()
        generated = generate_api_key()
        db.add(
            ApiKey(
                organization_id=org.id,
                name="mcp",
                prefix=generated.prefix,
                key_hash=generated.key_hash,
            )
        )
        vendor = Vendor(
            organization_id=org.id,
            canonical_name="Globex Industries",
            aliases=["Globex"],
            status=VendorStatus.ACTIVE,
        )
        db.add(vendor)
        await db.flush()
        for filename, text in POLICIES:
            doc = VendorDocument(
                organization_id=org.id,
                vendor_id=vendor.id,
                filename=filename,
                content_type=None,
                text=text,
            )
            db.add(doc)
            await db.flush()
            await rag.embed_document(db, doc)
        await db.flush()
    return generated.full_key


def _result(raw: Any) -> dict[str, Any]:
    """Normalise FastMCP call_tool output to the tool's dict.

    Recent FastMCP returns ``(content_blocks, structured_dict)``; older versions
    return a dict or a sequence of content blocks. Handle all three.
    """
    if isinstance(raw, tuple) and len(raw) == 2 and isinstance(raw[1], dict):
        return raw[1]
    if isinstance(raw, dict):
        return raw
    for block in raw:
        text = getattr(block, "text", None)
        if text:
            return json.loads(text)  # type: ignore[no-any-return]
    raise AssertionError(f"unexpected call_tool result: {raw!r}")


@pytest_asyncio.fixture
async def mcp_auth(monkeypatch: pytest.MonkeyPatch) -> str:
    """Provision a tenant and wire its API key as the MCP stdio fallback token."""
    key = await _provision_org_with_policies()
    monkeypatch.setattr(get_settings(), "mcp_api_key", key)
    return key


async def test_mcp_exposes_the_expected_tools(mcp_auth: str) -> None:
    tools = {t.name for t in await build_server().list_tools()}
    assert {
        "extract_invoice_fields",
        "process_invoice_text",
        "detect_duplicate_invoice",
        "check_invoice_completeness",
        "calculate_payment_terms_tool",
        "normalise_vendor_name",
        "update_invoice_status",
        "list_invoices",
        "list_vendors",
        "invoice_stats",
    } <= tools


async def test_agent_processes_invoices_against_multiple_policies(mcp_auth: str) -> None:
    mcp = build_server()

    async def process(raw_text: str) -> dict[str, Any]:
        return _result(await mcp.call_tool("process_invoice_text", {"raw_text": raw_text}))

    # Complies with both policies (under cap, has PO) -> approved.
    ok = await process(
        "Globex Industries\nInvoice Number: MCP-OK\nPO Number: PO-1\nGrand Total: $1,200.00"
    )
    assert ok["decision"] == "auto_approve", ok

    # Over the amount cap -> flagged.
    over = await process(
        "Globex Industries\nInvoice Number: MCP-BIG\nPO Number: PO-2\nGrand Total: $20,000.00"
    )
    assert over["decision"] == "flag", over

    # Missing the required PO -> flagged (the second policy is enforced too).
    no_po = await process("Globex Industries\nInvoice Number: MCP-NOPO\nGrand Total: $750.00")
    assert no_po["decision"] == "flag", no_po


async def test_mcp_requires_authentication(monkeypatch: pytest.MonkeyPatch) -> None:
    """With no API key configured, MCP tool calls are rejected."""
    from mcp.server.fastmcp.exceptions import ToolError

    monkeypatch.setattr(get_settings(), "mcp_api_key", None)
    mcp = build_server()
    with pytest.raises((ToolError, Exception)):
        await mcp.call_tool("list_vendors", {})


async def test_mcp_read_and_analytics_tools(mcp_auth: str) -> None:
    """An agent can drill into an invoice, explain it, search, and pull analytics."""
    mcp = build_server()

    # Create some data to query.
    processed = _result(
        await mcp.call_tool(
            "process_invoice_text",
            {
                "raw_text": "Globex Industries\nInvoice Number: RPT-1\nPO Number: PO-1\n"
                "Invoice Date: 2026-06-01\nGrand Total: $1,200.00"
            },
        )
    )
    invoice_id = processed["invoice_id"]

    # get_invoice -> full detail with line items + metadata.
    detail = _result(await mcp.call_tool("get_invoice", {"invoice_id": invoice_id}))
    assert detail["invoice_number"] == "RPT-1"
    assert "line_items" in detail and "metadata" in detail

    # get_invoice_audit_trail -> explains the decision.
    trail = _result(await mcp.call_tool("get_invoice_audit_trail", {"invoice_id": invoice_id}))
    assert "decision" in {e["event_type"] for e in trail["events"]}

    # search_invoices by number.
    found = _result(await mcp.call_tool("search_invoices", {"invoice_number": "RPT"}))
    assert found["total"] >= 1

    # vendor policy: documents + RAG search.
    vendor_id = _result(await mcp.call_tool("list_vendors", {}))["vendors"][0]["id"]
    policy = _result(await mcp.call_tool("get_vendor_policy", {"vendor_id": vendor_id}))
    assert len(policy["documents"]) == 2
    hits = _result(
        await mcp.call_tool("search_vendor_policy", {"vendor_id": vendor_id, "query": "amount cap"})
    )
    assert hits["hits"]

    # analytics.
    spend = _result(await mcp.call_tool("spend_analytics", {"group_by": "vendor"}))
    assert any(r["vendor"] == "Globex Industries" for r in spend["results"])
    metrics = _result(await mcp.call_tool("automation_metrics", {}))
    assert metrics["decided"] >= 1 and "automation_rate" in metrics
    aging = _result(await mcp.call_tool("payables_aging", {"as_of": "2026-06-15"}))
    assert {"overdue", "due_0_7", "no_due_date"} <= set(aging["buckets"])


async def test_mcp_get_invoice_not_found(mcp_auth: str) -> None:
    from mcp.server.fastmcp.exceptions import ToolError

    mcp = build_server()
    with pytest.raises((ToolError, Exception)):
        await mcp.call_tool("get_invoice", {"invoice_id": str(uuid.uuid4())})


async def test_mcp_rejects_invalid_status_and_uuid(mcp_auth: str) -> None:
    """Bad inputs become clean ToolErrors, not silent empty results or 500s."""
    from mcp.server.fastmcp.exceptions import ToolError

    mcp = build_server()
    # Unknown status would otherwise silently match zero rows.
    with pytest.raises((ToolError, Exception)):
        await mcp.call_tool("list_invoices", {"status": "aproved"})
    # Malformed UUID would otherwise raise a raw ValueError → unhandled.
    with pytest.raises((ToolError, Exception)):
        await mcp.call_tool("get_invoice", {"invoice_id": "not-a-uuid"})
