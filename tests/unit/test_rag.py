"""Unit tests for the RAG helpers (chunking, embedding, similarity)."""

from __future__ import annotations

from ap_invoice.services.rag import chunk_text, cosine_similarity, embed_text


def test_embedding_is_deterministic_and_normalised() -> None:
    a = embed_text("Net 30 payment terms")
    b = embed_text("Net 30 payment terms")
    assert a == b  # deterministic
    # L2-normalised → self-similarity ~1.0
    assert abs(cosine_similarity(a, a) - 1.0) < 1e-9


def test_related_text_more_similar_than_unrelated() -> None:
    query = embed_text("what are the payment terms")
    related = embed_text("payment terms are Net 30")
    unrelated = embed_text("the quick brown fox jumps")
    assert cosine_similarity(query, related) > cosine_similarity(query, unrelated)


def test_chunking_splits_long_text() -> None:
    text = "\n\n".join(f"Paragraph number {i} with some content." for i in range(50))
    chunks = chunk_text(text, chunk_size=200)
    assert len(chunks) > 1
    assert all(len(c) <= 400 for c in chunks)  # rough upper bound with paragraph packing


def test_chunking_empty() -> None:
    assert chunk_text("") == []
