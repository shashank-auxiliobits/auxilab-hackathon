"""Lightweight RAG layer for vendor policy documents.

* Chunk documents into overlapping windows.
* Embed chunks with a pluggable embedder (default: a deterministic, offline
  hashing embedder — no external calls, reproducible, fine for local dev/tests).
* Store embeddings as JSON float arrays and retrieve by cosine similarity in
  application code (the candidate set is scoped per vendor, so it stays small).

This is intentionally simple and dependency-free. For large corpora, swap the
storage for a pgvector column and the embedder for a hosted model — the
``retrieve_chunks`` / ``embed_document`` interface stays the same.
"""

from __future__ import annotations

import hashlib
import math
import re
import uuid

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from ap_invoice.core.config import get_settings
from ap_invoice.models.policy_document import PolicyChunk, VendorDocument

_WORD_RE = re.compile(r"[a-z0-9]+")


# --------------------------------------------------------------------------- #
# Embedding (deterministic, offline)
# --------------------------------------------------------------------------- #


def _tokenize(text: str) -> list[str]:
    return _WORD_RE.findall(text.lower())


def embed_text(text: str) -> list[float]:
    """Deterministic hashing bag-of-words embedding, L2-normalised.

    Not semantic, but stable and offline — good enough to power keyword-overlap
    retrieval and to exercise the full pipeline without any API dependency.
    """
    dim = get_settings().embedding_dim
    vec = [0.0] * dim
    for token in _tokenize(text):
        h = int.from_bytes(hashlib.md5(token.encode()).digest()[:4], "big")  # noqa: S324
        vec[h % dim] += 1.0
    norm = math.sqrt(sum(v * v for v in vec))
    if norm == 0.0:
        return vec
    return [v / norm for v in vec]


def embed_texts(texts: list[str]) -> list[list[float]]:
    return [embed_text(t) for t in texts]


def cosine_similarity(a: list[float], b: list[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b, strict=False))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    if na == 0.0 or nb == 0.0:
        return 0.0
    return dot / (na * nb)


# --------------------------------------------------------------------------- #
# Chunking
# --------------------------------------------------------------------------- #


def chunk_text(text: str, *, chunk_size: int | None = None, overlap: int = 150) -> list[str]:
    """Split text into ~chunk_size-character windows, preferring paragraph breaks."""
    size = chunk_size or get_settings().rag_chunk_size
    paragraphs = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
    chunks: list[str] = []
    current = ""
    for para in paragraphs:
        if len(current) + len(para) + 2 <= size:
            current = f"{current}\n\n{para}" if current else para
        else:
            if current:
                chunks.append(current)
            if len(para) <= size:
                current = para
            else:
                # Hard-split an oversized paragraph with overlap.
                start = 0
                while start < len(para):
                    chunks.append(para[start : start + size])
                    start += max(1, size - overlap)
                current = ""
    if current:
        chunks.append(current)
    return chunks or ([text.strip()] if text.strip() else [])


# --------------------------------------------------------------------------- #
# Store + retrieve
# --------------------------------------------------------------------------- #


async def embed_document(db: AsyncSession, document: VendorDocument) -> list[PolicyChunk]:
    """(Re)chunk and embed a document, replacing any existing chunks."""
    await db.execute(delete(PolicyChunk).where(PolicyChunk.document_id == document.id))
    model = f"{get_settings().embedding_provider}:{get_settings().embedding_dim}"
    chunks: list[PolicyChunk] = []
    for idx, text in enumerate(chunk_text(document.text)):
        chunks.append(
            PolicyChunk(
                document_id=document.id,
                vendor_id=document.vendor_id,
                chunk_index=idx,
                text=text,
                embedding=embed_text(text),
                embedding_model=model,
            )
        )
    db.add_all(chunks)
    await db.flush()
    return chunks


async def retrieve_chunks(
    db: AsyncSession, vendor_id: uuid.UUID, query: str, *, k: int | None = None
) -> list[tuple[PolicyChunk, float]]:
    """Return the top-k policy chunks for a vendor most similar to the query."""
    top_k = k or get_settings().rag_top_k
    rows = (
        (await db.execute(select(PolicyChunk).where(PolicyChunk.vendor_id == vendor_id)))
        .scalars()
        .all()
    )
    q = embed_text(query)
    scored = [(c, cosine_similarity(q, c.embedding)) for c in rows]
    scored.sort(key=lambda pair: pair[1], reverse=True)
    return scored[:top_k]
