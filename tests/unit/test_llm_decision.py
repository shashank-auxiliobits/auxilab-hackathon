"""Unit tests for the LLM decision engine (policy = source of truth).

The decision provider is stubbed by the autouse ``_stub_llm`` fixture
(``tests/conftest.py``), which enforces the retrieved policy TEXT against the
invoice. RAG retrieval is monkeypatched so no database is needed.
"""

from __future__ import annotations

import asyncio
import uuid
from dataclasses import dataclass
from decimal import Decimal

import pytest

from ap_invoice.core.enums import ApprovalDecision
from ap_invoice.schemas.tools import DuplicateCheckResult
from ap_invoice.services import rag
from ap_invoice.services.llm_decision import decide

POLICY = "Vendor policy. Invoices must not exceed $5,000. All invoices must be in USD."


@dataclass
class _Chunk:
    id: uuid.UUID
    text: str


def _patch_policy(monkeypatch: pytest.MonkeyPatch, text: str | None) -> None:
    chunks = [(_Chunk(uuid.uuid4(), text), 0.9)] if text else []

    async def _fake_retrieve(_db, _vendor_id, _query, *, k=None):
        return chunks

    monkeypatch.setattr(rag, "retrieve_chunks", _fake_retrieve)


def _run(**overrides):
    base = {
        "vendor_id": uuid.uuid4(),
        "vendor_name": "Acme",
        "fields": {"grand_total": Decimal("100.00"), "currency": "USD", "has_purchase_order": False},
        "duplicates": None,
    }
    base.update(overrides)
    return asyncio.run(decide(None, **base))  # type: ignore[arg-type]


def test_complies_with_policy_auto_approves(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_policy(monkeypatch, POLICY)
    evaluation, meta = _run()
    assert evaluation.decision is ApprovalDecision.AUTO_APPROVE
    assert meta["provider"] == "claude"
    assert meta["decided_by"] == "llm"


def test_over_policy_cap_flagged(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_policy(monkeypatch, POLICY)
    evaluation, _ = _run(
        fields={"grand_total": Decimal("25000.00"), "currency": "USD", "has_purchase_order": False}
    )
    assert evaluation.decision is ApprovalDecision.FLAG


def test_no_policy_holds(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_policy(monkeypatch, None)  # no policy on file
    evaluation, meta = _run()
    assert evaluation.decision is ApprovalDecision.HOLD
    assert meta["decided_by"] == "no_policy_guardrail"


def test_exact_duplicate_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_policy(monkeypatch, POLICY)
    dup = DuplicateCheckResult(is_duplicate=True, is_near_duplicate=False, highest_confidence=1.0)
    evaluation, meta = _run(duplicates=dup)
    assert evaluation.decision is ApprovalDecision.REJECT
    assert meta["decided_by"] == "duplicate_guardrail"
