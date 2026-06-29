"""Model Context Protocol (MCP) server exposing the invoice-intelligence tools.

AI agents connect over streamable-HTTP (multi-tenant, authenticated with a
per-organization API key sent as ``Authorization: Bearer <key>``) or over stdio
for local development (key taken from ``AP_MCP_API_KEY``). Every tool call is
scoped to the authenticated organization.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import date
from decimal import Decimal
from typing import Any

from mcp.server.fastmcp import Context, FastMCP
from mcp.server.fastmcp.exceptions import ToolError
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from ap_invoice.core.config import get_settings
from ap_invoice.core.enums import InvoiceStatus
from ap_invoice.core.logging import configure_logging, get_logger
from ap_invoice.db.session import session_scope
from ap_invoice.models.audit import ProcessingEvent
from ap_invoice.models.invoice import Invoice
from ap_invoice.models.organization import Organization
from ap_invoice.models.policy_document import PolicyRule, VendorDocument
from ap_invoice.models.vendor import Vendor
from ap_invoice.schemas.tools import (
    CompletenessRequest,
    DuplicateCheckRequest,
    ExtractedInvoice,
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
    rag,
)
from ap_invoice.services.auth import authenticate_api_key
from ap_invoice.services.extraction import (
    ExtractionUnavailable,
    InvalidFileError,
    collect_specs,
    decode_files,
)
from ap_invoice.services.ingestion import find_by_idempotency, invoice_from_extracted
from ap_invoice.services.orchestrator import process_invoice
from ap_invoice.services.reporting import (
    aging_buckets,
    decision_breakdown,
    invoice_status_counts,
    invoice_totals,
    spend_by_month,
    spend_by_vendor,
)
from ap_invoice.services.workflow import InvalidTransitionError, transition_status

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


async def _extract(
    raw_text: str | None,
    file_base64: str | None,
    content_type: str | None,
    files: list[dict[str, Any]] | None,
) -> ExtractedInvoice:
    """Decode file(s) and run extraction, surfacing bad input / outages as ToolError."""
    try:
        decoded = decode_files(collect_specs(file_base64, content_type, files))
        return await extract_invoice(raw_text, files=decoded)
    except InvalidFileError as exc:
        raise ToolError(str(exc)) from exc
    except ExtractionUnavailable as exc:
        raise ToolError(f"Invoice extraction is unavailable: {exc}") from exc


def _money(value: Decimal | None) -> str | None:
    return str(value) if value is not None else None


def _parse_date_opt(value: str | None) -> date | None:
    """Parse an optional ISO date, surfacing a clear ToolError on bad input."""
    if not value:
        return None
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise ToolError(f"Invalid date {value!r}; use ISO-8601 (YYYY-MM-DD).") from exc


def _invoice_summary(inv: Invoice) -> dict[str, Any]:
    return {
        "id": str(inv.id),
        "invoice_number": inv.invoice_number,
        "vendor_name": inv.raw_vendor_name,
        "grand_total": _money(inv.grand_total),
        "currency": inv.currency,
        "status": inv.status.value,
        "recommended_action": (inv.recommended_action.value if inv.recommended_action else None),
        "invoice_date": inv.invoice_date.isoformat() if inv.invoice_date else None,
        "due_date": inv.due_date.isoformat() if inv.due_date else None,
    }


def _invoice_detail(inv: Invoice) -> dict[str, Any]:
    """Full invoice view including line items, confidence, and metadata (PO/notes)."""
    return {
        **_invoice_summary(inv),
        "vendor_id": str(inv.vendor_id) if inv.vendor_id else None,
        "payment_terms": inv.payment_terms,
        "subtotal": _money(inv.subtotal),
        "tax": _money(inv.tax),
        "completeness_score": _money(inv.completeness_score),
        "extraction_source": inv.extraction_source.value if inv.extraction_source else None,
        "extraction_confidence": inv.extraction_confidence,
        "metadata": inv.extra_metadata,
        "line_items": [
            {
                "line_number": li.line_number,
                "description": li.description,
                "quantity": _money(li.quantity),
                "unit_price": _money(li.unit_price),
                "line_total": _money(li.line_total),
            }
            for li in inv.line_items
        ],
        "created_at": inv.created_at.isoformat(),
        "updated_at": inv.updated_at.isoformat(),
    }


def _parse_uuid(value: str, label: str) -> uuid.UUID:
    try:
        return uuid.UUID(value)
    except (ValueError, AttributeError) as exc:
        raise ToolError(f"Invalid {label} {value!r}.") from exc


def _parse_status(value: str | None) -> InvoiceStatus | None:
    if value is None:
        return None
    try:
        return InvoiceStatus(value)
    except ValueError as exc:
        valid = ", ".join(s.value for s in InvoiceStatus)
        raise ToolError(f"Unknown status {value!r}. Valid: {valid}.") from exc


async def _vendor_or_error(db: AsyncSession, org: Organization, vendor_id: str) -> Vendor:
    vendor = await db.get(Vendor, _parse_uuid(vendor_id, "vendor_id"))
    if vendor is None or vendor.organization_id != org.id:
        raise ToolError(f"Vendor {vendor_id} not found.")
    return vendor


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
        raw_text: str | None = None,
        file_base64: str | None = None,
        content_type: str | None = None,
        files: list[dict[str, Any]] | None = None,
        ctx: Ctx | None = None,
    ) -> dict[str, Any]:
        """Invoice Field Extractor (vision OCR).

        Parse an invoice into structured fields (invoice number, vendor, dates,
        line items, subtotal, tax, grand total) with a confidence score per
        field. Accepts ``raw_text`` and/or files for scanned copies and photos:
        a single file via ``file_base64`` + ``content_type`` (e.g. 'image/png' or
        'application/pdf'), or several pages/attachments via ``files`` — a list of
        ``{"file_base64": ..., "content_type": ..., "filename": ...}`` extracted
        together as one invoice. Extraction always runs through the vision model.
        """
        async with _org_session(ctx):
            result = await _extract(raw_text, file_base64, content_type, files)
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
                        .limit(get_settings().duplicate_candidate_limit)
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
        raw_text: str | None = None,
        file_base64: str | None = None,
        content_type: str | None = None,
        files: list[dict[str, Any]] | None = None,
        actor: str = "agent",
        idempotency_key: str | None = None,
        source: str = "agent",
        auto_onboard: bool = True,
        ctx: Ctx | None = None,
    ) -> dict[str, Any]:
        """Process an invoice end-to-end and persist the decision + audit trail.

        Extracts fields via vision OCR (from ``raw_text`` and/or files — a single
        file via ``file_base64`` + ``content_type``, or several pages/attachments
        via ``files`` as a list of ``{"file_base64", "content_type", "filename"}``
        extracted together as one invoice), resolves the vendor, checks
        completeness and duplicates, computes payment terms, then has the decision
        LLM judge the invoice against the vendor's policy (retrieved from the RAG)
        and records the verdict (auto_approve / hold / flag / reject) with
        confidence and a full audit trail. With ``auto_onboard`` (default true),
        an unrecognised vendor is auto-created as 'onboarding' so processing
        doesn't halt — the invoice still holds for review until the vendor is
        trusted. The primary action for automation.
        """
        async with _org_session(ctx) as (org, db):
            existing = await find_by_idempotency(db, org.id, idempotency_key)
            if existing is not None:
                result = await process_invoice(
                    db, org, existing, actor=actor, auto_onboard=auto_onboard
                )
                return result.model_dump(mode="json")
            extracted = await _extract(raw_text, file_base64, content_type, files)
            invoice = invoice_from_extracted(
                org.id,
                extracted,
                raw_text=raw_text or "",
                source=source,
                idempotency_key=idempotency_key,
            )
            db.add(invoice)
            await db.flush()
            result = await process_invoice(db, org, invoice, actor=actor, auto_onboard=auto_onboard)
            return result.model_dump(mode="json")

    @mcp.tool()
    async def update_invoice_status(
        invoice_id: str,
        status: str,
        note: str | None = None,
        actor: str = "agent",
        ctx: Ctx | None = None,
    ) -> dict[str, Any]:
        """Set an invoice's status: 'approved', 'held', 'flagged', or 'rejected'.

        Records the change (with actor + note) in the audit trail. Payment is not
        handled here. Use after reviewing a processed invoice's decision.
        """
        async with _org_session(ctx) as (org, db):
            invoice = await db.get(Invoice, _parse_uuid(invoice_id, "invoice_id"))
            if invoice is None or invoice.organization_id != org.id:
                raise ToolError(f"Invoice {invoice_id} not found.")
            try:
                target = InvoiceStatus(status)
            except ValueError as exc:
                raise ToolError(f"Unknown status '{status}'.") from exc
            try:
                await transition_status(db, org, invoice, target, actor=actor, note=note)
            except InvalidTransitionError as exc:
                raise ToolError(str(exc)) from exc
            return {
                "invoice_id": str(invoice.id),
                "status": invoice.status.value,
                "note": note,
            }

    @mcp.tool()
    async def invoice_stats(ctx: Ctx | None = None) -> dict[str, Any]:
        """Aggregated invoice counts for the organization.

        Returns the total invoice count, the summed grand total, and a breakdown
        of counts by status (approved / held / flagged / rejected / paid / ...).
        Use this to answer questions like "how many flagged invoices?".
        """
        async with _org_session(ctx) as (org, db):
            counts = await invoice_status_counts(db, org.id)
            total_invoices, total_amount = await invoice_totals(db, org.id)
            return {
                "total_invoices": total_invoices,
                "total_amount": str(total_amount),
                "by_status": counts,
            }

    @mcp.tool()
    async def list_invoices(
        status: str | None = None,
        vendor_id: str | None = None,
        limit: int = 50,
        offset: int = 0,
        ctx: Ctx | None = None,
    ) -> dict[str, Any]:
        """List the organization's invoices, optionally filtered by status or vendor.

        ``status`` is one of received/extracted/normalized/validated/approved/held/
        flagged/rejected/paid. Returns a page of invoices plus the total count, for
        reviewing or further-processing approved or flagged invoices.
        """
        limit = max(1, min(limit, 200))
        offset = max(0, offset)
        status_enum = _parse_status(status)
        async with _org_session(ctx) as (org, db):
            base = select(Invoice).where(Invoice.organization_id == org.id)
            if status_enum is not None:
                base = base.where(Invoice.status == status_enum)
            if vendor_id:
                base = base.where(Invoice.vendor_id == _parse_uuid(vendor_id, "vendor_id"))
            total = (
                await db.execute(select(func.count()).select_from(base.subquery()))
            ).scalar_one()
            rows = (
                (
                    await db.execute(
                        base.order_by(Invoice.created_at.desc()).limit(limit).offset(offset)
                    )
                )
                .scalars()
                .all()
            )
            return {
                "total": total,
                "limit": limit,
                "offset": offset,
                "invoices": [
                    {
                        "id": str(i.id),
                        "invoice_number": i.invoice_number,
                        "vendor_name": i.raw_vendor_name,
                        "grand_total": str(i.grand_total) if i.grand_total is not None else None,
                        "status": i.status.value,
                        "recommended_action": (
                            i.recommended_action.value if i.recommended_action else None
                        ),
                        "invoice_date": i.invoice_date.isoformat() if i.invoice_date else None,
                    }
                    for i in rows
                ],
            }

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

    @mcp.tool()
    async def get_invoice(invoice_id: str, ctx: Ctx | None = None) -> dict[str, Any]:
        """Get one invoice in full: header fields, line items, per-field extraction
        confidence, metadata (PO number, notes), status, and the recommended policy
        decision. Use to answer "tell me everything about invoice X".
        """
        async with _org_session(ctx) as (org, db):
            invoice = (
                await db.execute(
                    select(Invoice)
                    .where(Invoice.id == _parse_uuid(invoice_id, "invoice_id"))
                    .options(selectinload(Invoice.line_items))
                )
            ).scalar_one_or_none()
            if invoice is None or invoice.organization_id != org.id:
                raise ToolError(f"Invoice {invoice_id} not found.")
            return _invoice_detail(invoice)

    @mcp.tool()
    async def get_invoice_audit_trail(invoice_id: str, ctx: Ctx | None = None) -> dict[str, Any]:
        """Get an invoice's append-only audit trail: every extraction, vendor match,
        completeness/duplicate check, payment-terms calc, policy evaluation, decision,
        and status change — with the recorded reasons. Use to explain WHY an invoice
        was approved, held, flagged, or rejected.
        """
        async with _org_session(ctx) as (org, db):
            inv_uuid = _parse_uuid(invoice_id, "invoice_id")
            invoice = await db.get(Invoice, inv_uuid)
            if invoice is None or invoice.organization_id != org.id:
                raise ToolError(f"Invoice {invoice_id} not found.")
            rows = (
                (
                    await db.execute(
                        select(ProcessingEvent)
                        .where(ProcessingEvent.invoice_id == inv_uuid)
                        .order_by(ProcessingEvent.created_at)
                    )
                )
                .scalars()
                .all()
            )
            return {
                "invoice_id": invoice_id,
                "events": [
                    {
                        "event_type": e.event_type.value,
                        "actor": e.actor,
                        "tool_name": e.tool_name,
                        "decision": e.decision,
                        "message": e.message,
                        "details": e.details,
                        "created_at": e.created_at.isoformat(),
                    }
                    for e in rows
                ],
            }

    @mcp.tool()
    async def search_invoices(
        status: str | None = None,
        vendor_id: str | None = None,
        invoice_number: str | None = None,
        min_amount: float | None = None,
        max_amount: float | None = None,
        date_from: str | None = None,
        date_to: str | None = None,
        query: str | None = None,
        limit: int = 50,
        offset: int = 0,
        ctx: Ctx | None = None,
    ) -> dict[str, Any]:
        """Search invoices by any combination of: status, vendor, invoice number
        (substring), grand-total range (min/max_amount), invoice-date range
        (date_from/date_to, ISO-8601), or free text (``query`` matches vendor name or
        invoice number). Returns a page of summaries plus the total match count.
        """
        limit = max(1, min(limit, 200))
        offset = max(0, offset)
        df, dt = _parse_date_opt(date_from), _parse_date_opt(date_to)
        status_enum = _parse_status(status)
        async with _org_session(ctx) as (org, db):
            base = select(Invoice).where(Invoice.organization_id == org.id)
            if status_enum is not None:
                base = base.where(Invoice.status == status_enum)
            if vendor_id:
                base = base.where(Invoice.vendor_id == _parse_uuid(vendor_id, "vendor_id"))
            if invoice_number:
                base = base.where(Invoice.invoice_number.ilike(f"%{invoice_number}%"))
            if min_amount is not None:
                base = base.where(Invoice.grand_total >= Decimal(str(min_amount)))
            if max_amount is not None:
                base = base.where(Invoice.grand_total <= Decimal(str(max_amount)))
            if df is not None:
                base = base.where(Invoice.invoice_date >= df)
            if dt is not None:
                base = base.where(Invoice.invoice_date <= dt)
            if query:
                like = f"%{query}%"
                base = base.where(
                    or_(Invoice.raw_vendor_name.ilike(like), Invoice.invoice_number.ilike(like))
                )
            total = (
                await db.execute(select(func.count()).select_from(base.subquery()))
            ).scalar_one()
            rows = (
                (
                    await db.execute(
                        base.order_by(Invoice.created_at.desc()).limit(limit).offset(offset)
                    )
                )
                .scalars()
                .all()
            )
            return {
                "total": total,
                "limit": limit,
                "offset": offset,
                "invoices": [_invoice_summary(i) for i in rows],
            }

    @mcp.tool()
    async def get_vendor_policy(vendor_id: str, ctx: Ctx | None = None) -> dict[str, Any]:
        """Get a vendor's policy: its uploaded policy documents and the compiled,
        machine-enforceable rules (amount caps, required PO, allowed payment terms,
        currency). Use to answer "what is the policy for vendor X?".
        """
        async with _org_session(ctx) as (org, db):
            vendor = await _vendor_or_error(db, org, vendor_id)
            docs = (
                (
                    await db.execute(
                        select(VendorDocument)
                        .where(VendorDocument.vendor_id == vendor.id)
                        .order_by(VendorDocument.created_at)
                    )
                )
                .scalars()
                .all()
            )
            rules = (
                (
                    await db.execute(
                        select(PolicyRule)
                        .where(PolicyRule.vendor_id == vendor.id)
                        .order_by(PolicyRule.created_at)
                    )
                )
                .scalars()
                .all()
            )
            return {
                "vendor_id": str(vendor.id),
                "canonical_name": vendor.canonical_name,
                "documents": [
                    {
                        "id": str(d.id),
                        "filename": d.filename,
                        "status": d.status.value,
                        "text": d.text,
                        "created_at": d.created_at.isoformat(),
                    }
                    for d in docs
                ],
                "rules": [
                    {
                        "id": str(r.id),
                        "rule_type": r.rule_type.value,
                        "parameters": r.parameters,
                        "description": r.description,
                        "status": r.status.value,
                        "confidence": r.confidence,
                    }
                    for r in rules
                ],
            }

    @mcp.tool()
    async def search_vendor_policy(
        vendor_id: str, query: str, ctx: Ctx | None = None
    ) -> dict[str, Any]:
        """Semantic search (RAG) over a vendor's policy documents. Returns the most
        relevant policy excerpts for a natural-language question such as "what is the
        amount cap?" or "are late fees allowed?".
        """
        async with _org_session(ctx) as (org, db):
            vendor = await _vendor_or_error(db, org, vendor_id)
            hits = await rag.retrieve_chunks(db, vendor.id, query)
            return {
                "vendor_id": str(vendor.id),
                "query": query,
                "hits": [
                    {
                        "text": chunk.text,
                        "score": round(score, 4),
                        "document_id": str(chunk.document_id),
                    }
                    for chunk, score in hits
                ],
            }

    @mcp.tool()
    async def spend_analytics(
        group_by: str = "vendor",
        date_from: str | None = None,
        date_to: str | None = None,
        limit: int = 20,
        ctx: Ctx | None = None,
    ) -> dict[str, Any]:
        """Spend analytics: total invoiced amount and invoice count grouped by
        'vendor' (top spenders) or 'month' (trend), optionally within an invoice-date
        range (date_from/date_to, ISO-8601). Answers "how much did we spend with X?"
        or "what's our monthly invoice spend?".
        """
        df, dt = _parse_date_opt(date_from), _parse_date_opt(date_to)
        async with _org_session(ctx) as (org, db):
            if group_by == "month":
                results = await spend_by_month(db, org.id, date_from=df, date_to=dt)
            elif group_by == "vendor":
                results = await spend_by_vendor(
                    db, org.id, date_from=df, date_to=dt, limit=max(1, min(limit, 200))
                )
            else:
                raise ToolError("group_by must be 'vendor' or 'month'.")
            return {"group_by": group_by, "results": results}

    @mcp.tool()
    async def payables_aging(as_of: str | None = None, ctx: Ctx | None = None) -> dict[str, Any]:
        """Accounts-payable aging: outstanding (not paid/rejected) invoices bucketed by
        days to their due date — overdue, due in 0-7, 8-30, 31+ days, or no due date —
        each with a count and summed amount. Answers "what's overdue / coming due?".
        """
        ref = _parse_date_opt(as_of) or date.today()
        async with _org_session(ctx) as (org, db):
            return {"as_of": ref.isoformat(), "buckets": await aging_buckets(db, org.id, as_of=ref)}

    @mcp.tool()
    async def discount_opportunities(
        as_of: str | None = None, ctx: Ctx | None = None
    ) -> dict[str, Any]:
        """Early-payment discount opportunities: outstanding invoices whose discount
        window (e.g. '2/10 Net 30') is still open as of the reference date, with the
        capturable discount amount and deadline. Helps avoid leaving cash on the table.
        """
        ref = _parse_date_opt(as_of) or date.today()
        async with _org_session(ctx) as (org, db):
            rows = (
                (
                    await db.execute(
                        select(Invoice).where(
                            Invoice.organization_id == org.id,
                            Invoice.status.not_in([InvoiceStatus.PAID, InvoiceStatus.REJECTED]),
                            Invoice.payment_terms.is_not(None),
                            Invoice.invoice_date.is_not(None),
                        )
                    )
                )
                .scalars()
                .all()
            )
            opportunities: list[dict[str, Any]] = []
            total = Decimal(0)
            for inv in rows:
                if inv.invoice_date is None:
                    continue
                terms = calculate_payment_terms(
                    PaymentTermsRequest(
                        invoice_date=inv.invoice_date,
                        payment_terms=inv.payment_terms or "",
                        amount=inv.grand_total,
                        as_of=ref,
                    )
                )
                if (
                    terms.term_type == "discount"
                    and terms.discount_deadline is not None
                    and terms.discount_deadline >= ref
                ):
                    opportunities.append(
                        {
                            "invoice_id": str(inv.id),
                            "invoice_number": inv.invoice_number,
                            "vendor_name": inv.raw_vendor_name,
                            "discount_percent": _money(terms.discount_percent),
                            "discount_amount": _money(terms.discount_amount),
                            "discount_deadline": terms.discount_deadline.isoformat(),
                            "days_remaining": terms.days_until_discount_deadline,
                        }
                    )
                    if terms.discount_amount is not None:
                        total += terms.discount_amount
            opportunities.sort(key=lambda o: o["discount_deadline"])
            return {
                "as_of": ref.isoformat(),
                "count": len(opportunities),
                "total_capturable_discount": str(total),
                "opportunities": opportunities,
            }

    @mcp.tool()
    async def automation_metrics(ctx: Ctx | None = None) -> dict[str, Any]:
        """Processing metrics: invoice counts by status and by recommended policy
        decision, plus the touchless automation rate (auto-approved / decided). Answers
        "how automated are we?" and "how many need human review?".
        """
        async with _org_session(ctx) as (org, db):
            return {
                "by_status": await invoice_status_counts(db, org.id),
                **await decision_breakdown(db, org.id),
            }

    # The decorator registers each function on the server; bind to avoid "unused".
    _ = (
        extract_invoice_fields,
        calculate_payment_terms_tool,
        check_invoice_completeness,
        normalise_vendor_name,
        detect_duplicate_invoice,
        process_invoice_text,
        update_invoice_status,
        invoice_stats,
        list_invoices,
        list_vendors,
        get_invoice,
        get_invoice_audit_trail,
        search_invoices,
        get_vendor_policy,
        search_vendor_policy,
        spend_analytics,
        payables_aging,
        discount_opportunities,
        automation_metrics,
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
