"""Invoice ingestion and CRUD, scoped to the authenticated organization."""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Query, status
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import selectinload

from ap_invoice.api.deps import CurrentOrg, DBSession
from ap_invoice.api.errors import NotFoundError, ServiceUnavailableError, ValidationError
from ap_invoice.core.enums import InvoiceStatus
from ap_invoice.models.audit import ProcessingEvent
from ap_invoice.models.invoice import Invoice, InvoiceLineItem
from ap_invoice.schemas.common import Page
from ap_invoice.schemas.invoice import (
    InvoiceCreate,
    InvoiceDetail,
    InvoiceIngest,
    InvoiceRead,
)
from ap_invoice.schemas.policy_document import StatusTransitionRequest
from ap_invoice.schemas.processing import (
    InvoiceStats,
    ProcessingEventRead,
    ProcessRequest,
    ProcessResult,
)
from ap_invoice.schemas.tools import ExtractedInvoice
from ap_invoice.services.extraction import (
    ExtractionUnavailable,
    InvalidFileError,
    collect_specs,
    decode_files,
    extract_invoice,
)
from ap_invoice.services.ingestion import (
    compute_fingerprint,
    find_by_idempotency,
    invoice_from_extracted,
)
from ap_invoice.services.orchestrator import process_invoice
from ap_invoice.services.reporting import invoice_status_counts, invoice_totals
from ap_invoice.services.workflow import InvalidTransitionError, transition_status

router = APIRouter(prefix="/invoices", tags=["invoices"])


async def _extract_from_request(payload: InvoiceIngest | ProcessRequest) -> ExtractedInvoice:
    """Decode the request's file(s) and run extraction, mapping failures to HTTP errors.

    Bad client input (malformed base64, oversized/too-many/unsupported files) → 422;
    an unavailable LLM provider → 503.
    """
    try:
        specs = collect_specs(payload.file_base64, payload.content_type, payload.files)
        return await extract_invoice(payload.raw_text, files=decode_files(specs))
    except InvalidFileError as exc:
        raise ValidationError(str(exc)) from exc
    except ExtractionUnavailable as exc:
        raise ServiceUnavailableError(f"Invoice extraction is unavailable: {exc}") from exc


async def _load_detail(db: DBSession, invoice_id: uuid.UUID) -> Invoice:
    result = await db.execute(
        select(Invoice).where(Invoice.id == invoice_id).options(selectinload(Invoice.line_items))
    )
    invoice = result.scalar_one()
    return invoice


@router.post(
    "",
    response_model=InvoiceDetail,
    status_code=status.HTTP_201_CREATED,
    summary="Create an invoice from known fields",
)
async def create_invoice(payload: InvoiceCreate, org: CurrentOrg, db: DBSession) -> InvoiceDetail:
    existing = await find_by_idempotency(db, org.id, payload.idempotency_key)
    if existing is not None:
        return InvoiceDetail.model_validate(await _load_detail(db, existing.id))

    invoice = Invoice(
        organization_id=org.id,
        vendor_id=payload.vendor_id,
        raw_vendor_name=payload.raw_vendor_name,
        invoice_number=payload.invoice_number,
        invoice_date=payload.invoice_date,
        due_date=payload.due_date,
        currency=payload.currency,
        subtotal=payload.subtotal,
        tax=payload.tax,
        grand_total=payload.grand_total,
        payment_terms=payload.payment_terms,
        raw_text=payload.raw_text,
        source=payload.source,
        idempotency_key=payload.idempotency_key,
        extra_metadata=payload.extra_metadata,
        status=InvoiceStatus.RECEIVED,
        fingerprint=compute_fingerprint(
            payload.raw_vendor_name, payload.invoice_number, payload.grand_total
        ),
    )
    for li in payload.line_items:
        invoice.line_items.append(
            InvoiceLineItem(
                line_number=li.line_number,
                description=li.description,
                quantity=li.quantity,
                unit_price=li.unit_price,
                line_total=li.line_total,
            )
        )
    db.add(invoice)
    try:
        await db.flush()
    except IntegrityError:
        await db.rollback()
        existing = await find_by_idempotency(db, org.id, payload.idempotency_key)
        if existing is not None:
            return InvoiceDetail.model_validate(await _load_detail(db, existing.id))
        raise
    return InvoiceDetail.model_validate(await _load_detail(db, invoice.id))


@router.post(
    "/ingest",
    response_model=InvoiceDetail,
    status_code=status.HTTP_201_CREATED,
    summary="Ingest raw invoice text and extract its fields",
)
async def ingest_invoice(
    payload: InvoiceIngest,
    org: CurrentOrg,
    db: DBSession,
) -> InvoiceDetail:
    existing = await find_by_idempotency(db, org.id, payload.idempotency_key)
    if existing is not None:
        return InvoiceDetail.model_validate(await _load_detail(db, existing.id))

    extracted = await _extract_from_request(payload)
    invoice = invoice_from_extracted(
        org.id,
        extracted,
        raw_text=payload.raw_text or "",
        source=payload.source,
        idempotency_key=payload.idempotency_key,
        extra_metadata=payload.extra_metadata,
    )
    db.add(invoice)
    await db.flush()
    return InvoiceDetail.model_validate(await _load_detail(db, invoice.id))


@router.get("", response_model=Page[InvoiceRead], summary="List invoices")
async def list_invoices(
    org: CurrentOrg,
    db: DBSession,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
    status_filter: Annotated[InvoiceStatus | None, Query(alias="status")] = None,
    vendor_id: Annotated[uuid.UUID | None, Query()] = None,
) -> Page[InvoiceRead]:
    base = select(Invoice).where(Invoice.organization_id == org.id)
    if status_filter is not None:
        base = base.where(Invoice.status == status_filter)
    if vendor_id is not None:
        base = base.where(Invoice.vendor_id == vendor_id)
    total = (await db.execute(select(func.count()).select_from(base.subquery()))).scalar_one()
    rows = (
        (await db.execute(base.order_by(Invoice.created_at.desc()).limit(limit).offset(offset)))
        .scalars()
        .all()
    )
    return Page[InvoiceRead](
        items=[InvoiceRead.model_validate(i) for i in rows],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.get("/stats", response_model=InvoiceStats, summary="Aggregated invoice counts")
async def invoice_stats(org: CurrentOrg, db: DBSession) -> InvoiceStats:
    """Counts of the org's invoices by status, plus totals — for dashboards/agents."""
    counts = await invoice_status_counts(db, org.id)
    total_invoices, total_amount = await invoice_totals(db, org.id)
    return InvoiceStats(
        total_invoices=total_invoices,
        total_amount=total_amount,
        by_status=counts,
    )


async def _get_invoice(db: DBSession, org_id: uuid.UUID, invoice_id: uuid.UUID) -> Invoice:
    invoice = await db.get(Invoice, invoice_id)
    if invoice is None or invoice.organization_id != org_id:
        raise NotFoundError(f"Invoice {invoice_id} not found.")
    return invoice


@router.get("/{invoice_id}", response_model=InvoiceDetail, summary="Get an invoice")
async def get_invoice(invoice_id: uuid.UUID, org: CurrentOrg, db: DBSession) -> InvoiceDetail:
    await _get_invoice(db, org.id, invoice_id)
    return InvoiceDetail.model_validate(await _load_detail(db, invoice_id))


@router.delete("/{invoice_id}", status_code=status.HTTP_204_NO_CONTENT, summary="Delete an invoice")
async def delete_invoice(invoice_id: uuid.UUID, org: CurrentOrg, db: DBSession) -> None:
    invoice = await _get_invoice(db, org.id, invoice_id)
    await db.delete(invoice)
    await db.flush()


@router.post(
    "/process",
    response_model=ProcessResult,
    status_code=status.HTTP_201_CREATED,
    summary="Ingest raw invoice text and run the full policy pipeline",
)
async def process_raw_invoice(
    payload: ProcessRequest, org: CurrentOrg, db: DBSession
) -> ProcessResult:
    """Extract → normalise → completeness → duplicates → terms → decide, with audit trail."""
    existing = await find_by_idempotency(db, org.id, payload.idempotency_key)
    if existing is not None:
        return await process_invoice(
            db, org, existing, actor=payload.actor, auto_onboard=payload.auto_onboard
        )

    extracted = await _extract_from_request(payload)
    invoice = invoice_from_extracted(
        org.id,
        extracted,
        raw_text=payload.raw_text or "",
        source=payload.source,
        idempotency_key=payload.idempotency_key,
        extra_metadata=payload.extra_metadata,
    )
    db.add(invoice)
    await db.flush()
    return await process_invoice(
        db, org, invoice, actor=payload.actor, auto_onboard=payload.auto_onboard
    )


@router.post(
    "/{invoice_id}/process",
    response_model=ProcessResult,
    summary="Run the full policy pipeline on an existing invoice",
)
async def process_existing_invoice(
    invoice_id: uuid.UUID,
    org: CurrentOrg,
    db: DBSession,
    actor: Annotated[str, Query(max_length=255)] = "agent",
    auto_onboard: Annotated[bool, Query()] = True,
) -> ProcessResult:
    invoice = await _get_invoice(db, org.id, invoice_id)
    return await process_invoice(db, org, invoice, actor=actor, auto_onboard=auto_onboard)


@router.post(
    "/{invoice_id}/status",
    response_model=InvoiceDetail,
    summary="Set an invoice's status (approve / hold / flag / reject)",
)
async def set_invoice_status(
    invoice_id: uuid.UUID, payload: StatusTransitionRequest, org: CurrentOrg, db: DBSession
) -> InvoiceDetail:
    invoice = await _get_invoice(db, org.id, invoice_id)
    try:
        await transition_status(
            db, org, invoice, payload.status, actor=payload.actor, note=payload.note
        )
    except InvalidTransitionError as exc:
        raise ValidationError(str(exc)) from exc
    return InvoiceDetail.model_validate(await _load_detail(db, invoice_id))


@router.get(
    "/{invoice_id}/events",
    response_model=list[ProcessingEventRead],
    summary="Get an invoice's audit trail",
)
async def get_invoice_events(
    invoice_id: uuid.UUID, org: CurrentOrg, db: DBSession
) -> list[ProcessingEvent]:
    await _get_invoice(db, org.id, invoice_id)
    rows = (
        (
            await db.execute(
                select(ProcessingEvent)
                .where(ProcessingEvent.invoice_id == invoice_id)
                .order_by(ProcessingEvent.created_at)
            )
        )
        .scalars()
        .all()
    )
    return list(rows)
