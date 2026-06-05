"""REST endpoints exposing the five invoice-intelligence tools.

Stateless tools (extract, payment terms, completeness) operate purely on the
request body. DB-backed tools (vendor normaliser, duplicate detector) pull the
organization's vendor master / recent invoices from the database so callers do
not have to supply them.
"""

from __future__ import annotations

from fastapi import APIRouter
from sqlalchemy import select

from ap_invoice.api.deps import CurrentOrg, DBSession
from ap_invoice.models.invoice import Invoice
from ap_invoice.models.vendor import Vendor
from ap_invoice.schemas.tools import (
    CompletenessRequest,
    CompletenessResult,
    DuplicateCheckRequest,
    DuplicateCheckResult,
    ExistingInvoice,
    ExtractedInvoice,
    ExtractRequest,
    PaymentTermsRequest,
    PaymentTermsResult,
    VendorMasterEntry,
    VendorNormaliseRequest,
    VendorNormaliseResult,
)
from ap_invoice.services import (
    calculate_payment_terms,
    check_completeness,
    detect_duplicates,
    extract_invoice,
    normalise_vendor,
)

router = APIRouter(prefix="/tools", tags=["tools"])

# Cap on how many recent invoices are pulled as duplicate candidates per request.
_DUP_CANDIDATE_LIMIT = 1000


@router.post("/extract", response_model=ExtractedInvoice, summary="Invoice Field Extractor")
async def tool_extract(payload: ExtractRequest, _: CurrentOrg) -> ExtractedInvoice:
    return await extract_invoice(payload.raw_text, engine=payload.engine)


@router.post(
    "/payment-terms", response_model=PaymentTermsResult, summary="Payment Terms Calculator"
)
async def tool_payment_terms(payload: PaymentTermsRequest, _: CurrentOrg) -> PaymentTermsResult:
    return calculate_payment_terms(payload)


@router.post(
    "/completeness", response_model=CompletenessResult, summary="Invoice Completeness Checker"
)
async def tool_completeness(payload: CompletenessRequest, _: CurrentOrg) -> CompletenessResult:
    return check_completeness(payload)


@router.post(
    "/normalise-vendor",
    response_model=VendorNormaliseResult,
    summary="Vendor Name Normaliser (against this org's vendor master)",
)
async def tool_normalise_vendor(
    payload: VendorNormaliseRequest, org: CurrentOrg, db: DBSession
) -> VendorNormaliseResult:
    rows = (
        (await db.execute(select(Vendor).where(Vendor.organization_id == org.id))).scalars().all()
    )
    payload.vendor_master = [
        VendorMasterEntry(id=str(v.id), canonical_name=v.canonical_name, aliases=v.aliases)
        for v in rows
    ]
    return normalise_vendor(payload)


@router.post(
    "/detect-duplicates",
    response_model=DuplicateCheckResult,
    summary="Duplicate Invoice Detector (against this org's recent invoices)",
)
async def tool_detect_duplicates(
    payload: DuplicateCheckRequest, org: CurrentOrg, db: DBSession
) -> DuplicateCheckResult:
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
    payload.candidates = [
        ExistingInvoice(
            id=str(i.id),
            vendor_name=i.raw_vendor_name,
            invoice_number=i.invoice_number,
            amount=i.grand_total,
            date=i.invoice_date,
        )
        for i in rows
    ]
    return detect_duplicates(payload)
