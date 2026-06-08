"""Vendor policy documents, their embedded chunks (for RAG), and compiled rules.

A vendor attaches a free-form policy document (contract / T&Cs). It is chunked
and embedded for retrieval, then an LLM "policy compiler" extracts structured,
typed rules into :class:`PolicyRule`. Enforcement at invoice time uses the
*structured rules* (deterministic) — never the raw document — which keeps
decisions reproducible, auditable, and safe from prompt injection.

Embeddings are stored as JSONB float arrays with cosine similarity computed in
application code (the candidate set is scoped per vendor, so it stays small).
For large-scale deployments, swap this for a pgvector column.
"""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING, Any

from sqlalchemy import ForeignKey, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ap_invoice.core.enums import DocumentStatus, PolicyRuleStatus, PolicyRuleType
from ap_invoice.db.base import Base, TimestampMixin, UUIDPrimaryKeyMixin, str_enum

if TYPE_CHECKING:
    from ap_invoice.models.vendor import Vendor


class VendorDocument(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """A policy document a vendor has attached (stored as extracted text)."""

    __tablename__ = "vendor_documents"

    organization_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    vendor_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("vendors.id", ondelete="CASCADE"), nullable=False, index=True
    )
    filename: Mapped[str] = mapped_column(String(255), nullable=False)
    content_type: Mapped[str | None] = mapped_column(String(100), nullable=True)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[DocumentStatus] = mapped_column(
        str_enum(DocumentStatus, length=20), nullable=False, default=DocumentStatus.UPLOADED
    )

    vendor: Mapped[Vendor] = relationship(back_populates="documents")
    chunks: Mapped[list[PolicyChunk]] = relationship(
        back_populates="document", cascade="all, delete-orphan"
    )


class PolicyChunk(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """A chunk of a vendor document plus its embedding, for retrieval (RAG)."""

    __tablename__ = "policy_chunks"

    document_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("vendor_documents.id", ondelete="CASCADE"), nullable=False, index=True
    )
    vendor_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("vendors.id", ondelete="CASCADE"), nullable=False, index=True
    )
    chunk_index: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    # Embedding vector as a JSON float array (cosine computed in app code).
    embedding: Mapped[list[float]] = mapped_column(JSONB, nullable=False, default=list)
    embedding_model: Mapped[str | None] = mapped_column(String(100), nullable=True)

    document: Mapped[VendorDocument] = relationship(back_populates="chunks")


class PolicyRule(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """A structured, machine-enforceable rule compiled from a policy document.

    Rules are ``proposed`` until a human/vendor approves them; only ``approved``
    rules are enforced. Each rule keeps a citation back to its source clause.
    """

    __tablename__ = "policy_rules"

    organization_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    vendor_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("vendors.id", ondelete="CASCADE"), nullable=False, index=True
    )
    document_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("vendor_documents.id", ondelete="SET NULL"), nullable=True
    )
    rule_type: Mapped[PolicyRuleType] = mapped_column(
        str_enum(PolicyRuleType, length=40), nullable=False
    )
    parameters: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    source_quote: Mapped[str | None] = mapped_column(Text, nullable=True)
    confidence: Mapped[float | None] = mapped_column(nullable=True)
    status: Mapped[PolicyRuleStatus] = mapped_column(
        str_enum(PolicyRuleStatus, length=20), nullable=False, default=PolicyRuleStatus.PROPOSED
    )

    vendor: Mapped[Vendor] = relationship(back_populates="policy_rules")
