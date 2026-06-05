"""Model Context Protocol (MCP) server exposing the invoice-intelligence tools.

AI agents connect over streamable-HTTP (multi-tenant, authenticated with a
per-organization API key sent as ``Authorization: Bearer <key>``) or over stdio
for local development (key taken from ``AP_MCP_API_KEY``). Every tool call is
scoped to the authenticated organization.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from mcp.server.fastmcp import Context, FastMCP
from mcp.server.fastmcp.exceptions import ToolError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ap_invoice.core.config import get_settings
from ap_invoice.core.logging import configure_logging, get_logger
from ap_invoice.db.session import session_scope
from ap_invoice.models.invoice import Invoice
from ap_invoice.models.organization import Organization
from ap_invoice.models.vendor import Vendor
from ap_invoice.schemas.tools import (
    CompletenessRequest,
    DuplicateCheckRequest,
    PaymentTermsRequest,
    VendorMasterEntry,
    VendorNormaliseRequest,
)
from ap_invoice.services import (
    calculate_payment_terms,
    check_completeness,
    detect_duplicates,
    extract_invoice,
    normalise_vendor,
)
from ap_invoice.services.auth import authenticate_api_key
from ap_invoice.services.ingestion import find_by_idempotency, invoice_from_extracted
from ap_invoice.services.orchestrator import process_invoice

logger = get_logger(__name__)

# FastMCP's Context is generic over (ServerSession, LifespanContext, Request).
Ctx = Context[Any, Any, Any]

_INSTRUCTIONS = (
    "AP Invoice Intelligence tools for automating accounts-payable invoice "
    "processing against per-vendor policies. Authenticate with an organization "
    "API key (Authorization: Bearer <key>). Use extract_invoice_fields to parse "
    "raw text, normalise_vendor_name to resolve the vendor, detect_duplicate_invoice "
    "to guard against double-payment, calculate_payment_terms for due dates and "
    "discounts, and check_invoice_completeness before approving."
)

_DUP_CANDIDATE_LIMIT = 1000


def _token_from_ctx(ctx: Ctx | None) -> str | None:
    """Extract the bearer/API-key token from the HTTP request, or fall back to config."""
    if ctx is not None:
        try:
            request = ctx.request_context.request
        except Exception:
            request = None
        if request is not None:
            authz = request.headers.get("authorization")
            if authz:
                scheme, _, value = str(authz).partition(" ")
                if scheme.lower() == "bearer" and value:
                    return value.strip()
            x_api_key = request.headers.get("x-api-key")
            if x_api_key:
                return str(x_api_key).strip()
    return get_settings().mcp_api_key


@asynccontextmanager
async def _org_session(ctx: Ctx | None) -> AsyncIterator[tuple[Organization, AsyncSession]]:
    """Yield the authenticated organization and a DB session for a tool call."""
    token = _token_from_ctx(ctx)
    async with session_scope() as db:
        org = await authenticate_api_key(db, token)
        if org is None:
            raise ToolError(
                "Unauthorized: provide a valid organization API key "
                "(Authorization: Bearer <key>, or AP_MCP_API_KEY for stdio)."
            )
        yield org, db


def build_server() -> FastMCP:
    """Construct and configure the FastMCP server with all tools registered."""
    settings = get_settings()
    mcp = FastMCP(
        name="ap-invoice-intelligence",
        instructions=_INSTRUCTIONS,
        host=settings.mcp_host,
        port=settings.mcp_port,
        log_level=settings.log_level,
        streamable_http_path="/mcp",
        stateless_http=True,
    )

    @mcp.tool()
    async def extract_invoice_fields(
        raw_text: str,
        engine: str | None = None,
        ctx: Ctx | None = None,
    ) -> dict[str, Any]:
        """Invoice Field Extractor.

        Parse raw invoice text into structured fields (invoice number, vendor,
        dates, line items, subtotal, tax, grand total) with a confidence score
        per field. ``engine`` may be 'hybrid', 'llm', or 'deterministic'.
        """
        async with _org_session(ctx):
            result = await extract_invoice(raw_text, engine=engine)  # type: ignore[arg-type]
        return result.model_dump(mode="json")

    @mcp.tool()
    async def calculate_payment_terms_tool(
        invoice_date: str,
        payment_terms: str,
        amount: float | None = None,
        as_of: str | None = None,
        ctx: Ctx | None = None,
    ) -> dict[str, Any]:
        """Payment Terms Calculator.

        Given an invoice date and terms (e.g. 'Net 30', '2/10 Net 30',
        'Due on Receipt'), return the due date, early-payment-discount deadline
        and amount, and days remaining to each milestone. Dates are ISO-8601.
        """
        async with _org_session(ctx):
            req = PaymentTermsRequest.model_validate(
                {
                    "invoice_date": invoice_date,
                    "payment_terms": payment_terms,
                    "amount": amount,
                    "as_of": as_of,
                }
            )
            return calculate_payment_terms(req).model_dump(mode="json")

    @mcp.tool()
    async def check_invoice_completeness(
        fields: dict[str, Any],
        mandatory_fields: list[str] | None = None,
        ctx: Ctx | None = None,
    ) -> dict[str, Any]:
        """Invoice Completeness Checker.

        Validate extracted fields against a mandatory-field list; returns a
        completeness score (%), the missing fields, and a recommended action
        (Process / Hold / Return to Vendor).
        """
        async with _org_session(ctx):
            payload: dict[str, Any] = {"fields": fields}
            if mandatory_fields is not None:
                payload["mandatory_fields"] = mandatory_fields
            return check_completeness(CompletenessRequest.model_validate(payload)).model_dump(
                mode="json"
            )

    @mcp.tool()
    async def normalise_vendor_name(
        raw_name: str,
        threshold: float = 85.0,
        ctx: Ctx | None = None,
    ) -> dict[str, Any]:
        """Vendor Name Normaliser.

        Match a raw vendor name against this organization's vendor master and
        return the canonical vendor, or flag it for onboarding if unrecognised.
        """
        async with _org_session(ctx) as (org, db):
            rows = (
                (await db.execute(select(Vendor).where(Vendor.organization_id == org.id)))
                .scalars()
                .all()
            )
            req = VendorNormaliseRequest(
                raw_name=raw_name,
                threshold=threshold,
                vendor_master=[
                    VendorMasterEntry(
                        id=str(v.id), canonical_name=v.canonical_name, aliases=v.aliases
                    )
                    for v in rows
                ],
            )
            return normalise_vendor(req).model_dump(mode="json")

    @mcp.tool()
    async def detect_duplicate_invoice(
        vendor_name: str | None = None,
        invoice_number: str | None = None,
        amount: float | None = None,
        date: str | None = None,
        amount_tolerance_pct: float = 5.0,
        lookback_days: int | None = None,
        ctx: Ctx | None = None,
    ) -> dict[str, Any]:
        """Duplicate Invoice Detector.

        Check a candidate invoice against this organization's recent invoices
        using fuzzy vendor matching and an amount tolerance (default 5%).
        Returns exact and near-duplicate matches with confidence scores.
        """
        async with _org_session(ctx) as (org, db):
            rows = (
                (
                    await db.execute(
                        select(Invoice)
                        .where(Invoice.organization_id == org.id)
                        .order_by(Invoice.created_at.desc())
                        .limit(_DUP_CANDIDATE_LIMIT)
                    )
                )
                .scalars()
                .all()
            )
            req = DuplicateCheckRequest.model_validate(
                {
                    "vendor_name": vendor_name,
                    "invoice_number": invoice_number,
                    "amount": amount,
                    "date": date,
                    "amount_tolerance_pct": amount_tolerance_pct,
                    "lookback_days": lookback_days,
                    "candidates": [
                        {
                            "id": str(i.id),
                            "vendor_name": i.raw_vendor_name,
                            "invoice_number": i.invoice_number,
                            "amount": i.grand_total,
                            "date": i.invoice_date,
                        }
                        for i in rows
                    ],
                }
            )
            return detect_duplicates(req).model_dump(mode="json")

    @mcp.tool()
    async def process_invoice_text(
        raw_text: str,
        actor: str = "agent",
        idempotency_key: str | None = None,
        engine: str | None = None,
        source: str = "agent",
        ctx: Ctx | None = None,
    ) -> dict[str, Any]:
        """Process an invoice end-to-end and persist the decision + audit trail.

        Extracts fields from raw text, resolves the vendor, checks completeness
        and duplicates, computes payment terms, applies the vendor's policy, and
        records the verdict (auto_approve / hold / flag / reject) with a full
        audit trail. Returns the explained decision. This is the primary action
        for automating approvals.
        """
        async with _org_session(ctx) as (org, db):
            existing = await find_by_idempotency(db, org.id, idempotency_key)
            if existing is not None:
                result = await process_invoice(db, org, existing, actor=actor)
                return result.model_dump(mode="json")
            extracted = await extract_invoice(raw_text, engine=engine)  # type: ignore[arg-type]
            invoice = invoice_from_extracted(
                org.id,
                extracted,
                raw_text=raw_text,
                source=source,
                idempotency_key=idempotency_key,
            )
            db.add(invoice)
            await db.flush()
            result = await process_invoice(db, org, invoice, actor=actor)
            return result.model_dump(mode="json")

    @mcp.tool()
    async def list_vendors(ctx: Ctx | None = None) -> dict[str, Any]:
        """List the organization's vendors and statuses, to ground decisions."""
        async with _org_session(ctx) as (org, db):
            rows = (
                (
                    await db.execute(
                        select(Vendor)
                        .where(Vendor.organization_id == org.id)
                        .order_by(Vendor.canonical_name)
                    )
                )
                .scalars()
                .all()
            )
            return {
                "vendors": [
                    {
                        "id": str(v.id),
                        "canonical_name": v.canonical_name,
                        "aliases": v.aliases,
                        "status": v.status.value,
                    }
                    for v in rows
                ]
            }

    # The decorator registers each function on the server; bind to avoid "unused".
    _ = (
        extract_invoice_fields,
        calculate_payment_terms_tool,
        check_invoice_completeness,
        normalise_vendor_name,
        detect_duplicate_invoice,
        process_invoice_text,
        list_vendors,
    )
    return mcp


def run() -> None:
    """Console-script entrypoint: ``ap-invoice-mcp``."""
    configure_logging()
    settings = get_settings()
    server = build_server()
    logger.info("mcp_startup", transport=settings.mcp_transport, port=settings.mcp_port)
    server.run(transport=settings.mcp_transport)


if __name__ == "__main__":
    run()
