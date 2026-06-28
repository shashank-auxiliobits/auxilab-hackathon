"""Vendor policy documents and compiled rules (RAG policy onboarding)."""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Query, status
from sqlalchemy import delete, select

from ap_invoice.api.deps import CurrentOrg, DBSession
from ap_invoice.api.errors import NotFoundError, ValidationError
from ap_invoice.core.enums import PolicyRuleStatus
from ap_invoice.models.policy_document import PolicyRule, VendorDocument
from ap_invoice.models.vendor import Vendor
from ap_invoice.schemas.policy_document import (
    DocumentCompileResult,
    DocumentUpload,
    PolicyRuleRead,
    PolicySearchHit,
    VendorDocumentRead,
)
from ap_invoice.services import rag
from ap_invoice.services.policy_compiler import compile_document
from ap_invoice.services.policy_guardrails import screen_policy_text

router = APIRouter(prefix="/vendors/{vendor_id}", tags=["policy documents"])


async def _get_vendor(db: DBSession, org_id: uuid.UUID, vendor_id: uuid.UUID) -> Vendor:
    vendor = await db.get(Vendor, vendor_id)
    if vendor is None or vendor.organization_id != org_id:
        raise NotFoundError(f"Vendor {vendor_id} not found.")
    return vendor


async def _clear_vendor_policy(db: DBSession, vendor_id: uuid.UUID) -> None:
    """Delete a vendor's policy documents (chunks cascade) and compiled rules."""
    await db.execute(delete(PolicyRule).where(PolicyRule.vendor_id == vendor_id))
    # Deleting documents cascades to their chunks via the FK ondelete=CASCADE.
    await db.execute(delete(VendorDocument).where(VendorDocument.vendor_id == vendor_id))
    await db.flush()


@router.post(
    "/documents",
    response_model=DocumentCompileResult,
    status_code=status.HTTP_201_CREATED,
    summary="Attach a policy document (embed + optionally compile structured rules)",
)
async def upload_document(
    vendor_id: uuid.UUID, payload: DocumentUpload, org: CurrentOrg, db: DBSession
) -> DocumentCompileResult:
    await _get_vendor(db, org.id, vendor_id)

    # Guardrail: the policy becomes the decision LLM's source of truth, so reject
    # text that looks like instructions to the model (prompt injection) before it
    # is ever stored or embedded.
    if reasons := screen_policy_text(payload.text):
        raise ValidationError(
            "Policy rejected: the text contains content that looks like instructions "
            "to the AI rather than business rules (possible prompt injection): "
            + "; ".join(reasons)
            + ". Remove this and re-upload."
        )

    if payload.replace:
        await _clear_vendor_policy(db, vendor_id)
    document = VendorDocument(
        organization_id=org.id,
        vendor_id=vendor_id,
        filename=payload.filename,
        content_type=payload.content_type,
        text=payload.text,
    )
    db.add(document)
    await db.flush()

    await rag.embed_document(db, document)

    rules: list[PolicyRule] = []
    if payload.compile:
        rules = await compile_document(db, document, engine=payload.engine)

    return DocumentCompileResult(
        document_id=document.id,
        status=document.status,
        rules=[PolicyRuleRead.model_validate(r) for r in rules],
    )


@router.get("/documents", response_model=list[VendorDocumentRead], summary="List policy documents")
async def list_documents(
    vendor_id: uuid.UUID, org: CurrentOrg, db: DBSession
) -> list[VendorDocument]:
    await _get_vendor(db, org.id, vendor_id)
    rows = (
        (
            await db.execute(
                select(VendorDocument)
                .where(VendorDocument.vendor_id == vendor_id)
                .order_by(VendorDocument.created_at.desc())
            )
        )
        .scalars()
        .all()
    )
    return list(rows)


@router.delete(
    "/documents/{document_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete a policy document (removes its chunks + compiled rules)",
)
async def delete_document(
    vendor_id: uuid.UUID, document_id: uuid.UUID, org: CurrentOrg, db: DBSession
) -> None:
    await _get_vendor(db, org.id, vendor_id)
    document = await db.get(VendorDocument, document_id)
    if document is None or document.vendor_id != vendor_id or document.organization_id != org.id:
        raise NotFoundError(f"Document {document_id} not found.")
    # Drop rules compiled from this doc, then the doc (chunks cascade via FK).
    await db.execute(delete(PolicyRule).where(PolicyRule.document_id == document_id))
    await db.delete(document)
    await db.flush()


@router.get("/rules", response_model=list[PolicyRuleRead], summary="List compiled policy rules")
async def list_rules(
    vendor_id: uuid.UUID,
    org: CurrentOrg,
    db: DBSession,
    rule_status: Annotated[PolicyRuleStatus | None, Query(alias="status")] = None,
) -> list[PolicyRule]:
    await _get_vendor(db, org.id, vendor_id)
    stmt = select(PolicyRule).where(PolicyRule.vendor_id == vendor_id)
    if rule_status is not None:
        stmt = stmt.where(PolicyRule.status == rule_status)
    rows = (await db.execute(stmt.order_by(PolicyRule.created_at))).scalars().all()
    return list(rows)


async def _get_rule(
    db: DBSession, org_id: uuid.UUID, vendor_id: uuid.UUID, rule_id: uuid.UUID
) -> PolicyRule:
    rule = await db.get(PolicyRule, rule_id)
    if rule is None or rule.organization_id != org_id or rule.vendor_id != vendor_id:
        raise NotFoundError(f"Policy rule {rule_id} not found.")
    return rule


@router.post(
    "/rules/{rule_id}/approve",
    response_model=PolicyRuleRead,
    summary="Approve a compiled rule (only approved rules are enforced)",
)
async def approve_rule(
    vendor_id: uuid.UUID, rule_id: uuid.UUID, org: CurrentOrg, db: DBSession
) -> PolicyRule:
    rule = await _get_rule(db, org.id, vendor_id, rule_id)
    rule.status = PolicyRuleStatus.APPROVED
    await db.flush()
    return rule


@router.post(
    "/rules/{rule_id}/reject",
    response_model=PolicyRuleRead,
    summary="Reject a compiled rule",
)
async def reject_rule(
    vendor_id: uuid.UUID, rule_id: uuid.UUID, org: CurrentOrg, db: DBSession
) -> PolicyRule:
    rule = await _get_rule(db, org.id, vendor_id, rule_id)
    rule.status = PolicyRuleStatus.REJECTED
    await db.flush()
    return rule


@router.get(
    "/policy-search",
    response_model=list[PolicySearchHit],
    summary="Semantic search over the vendor's policy documents (RAG retrieval)",
)
async def policy_search(
    vendor_id: uuid.UUID,
    org: CurrentOrg,
    db: DBSession,
    q: Annotated[str, Query(min_length=1, description="Query text.")],
) -> list[PolicySearchHit]:
    await _get_vendor(db, org.id, vendor_id)
    hits = await rag.retrieve_chunks(db, vendor_id, q)
    return [
        PolicySearchHit(text=chunk.text, score=round(score, 4), document_id=chunk.document_id)
        for chunk, score in hits
    ]
