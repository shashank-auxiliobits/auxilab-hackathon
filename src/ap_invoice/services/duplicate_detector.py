"""Duplicate Invoice Detector.

Given a candidate invoice's key fields and a list of recently processed
invoices, determines whether an exact or near-duplicate exists. Vendor names are
matched fuzzily and amounts within a configurable percentage tolerance are
treated as equivalent.
"""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

from rapidfuzz import fuzz

from ap_invoice.schemas.tools import (
    DuplicateCheckRequest,
    DuplicateCheckResult,
    DuplicateMatch,
    ExistingInvoice,
)
from ap_invoice.services._parsing import normalize_invoice_number, normalize_vendor_name


def _vendor_similarity(a: str | None, b: str | None) -> float:
    if not a or not b:
        return 0.0
    na, nb = normalize_vendor_name(a), normalize_vendor_name(b)
    if na and na == nb:
        return 100.0
    return float(max(fuzz.token_sort_ratio(na, nb), fuzz.token_set_ratio(na, nb)))


def _amounts_within_tolerance(
    a: Decimal | None, b: Decimal | None, tolerance_pct: Decimal
) -> tuple[bool, float]:
    """Return (within_tolerance, closeness_0_1)."""
    if a is None or b is None:
        return False, 0.0
    if a == b:
        return True, 1.0
    base = max(abs(a), abs(b))
    if base == 0:
        return True, 1.0
    diff_pct = abs(a - b) / base * Decimal("100")
    within = diff_pct <= tolerance_pct
    closeness = float(max(Decimal("0"), Decimal("1") - diff_pct / Decimal("100")))
    return within, closeness


def _evaluate_candidate(
    request: DuplicateCheckRequest, cand: ExistingInvoice
) -> DuplicateMatch | None:
    reasons: list[str] = []

    # Invoice number comparison (strongest signal).
    same_number = False
    if request.invoice_number and cand.invoice_number:
        same_number = normalize_invoice_number(request.invoice_number) == normalize_invoice_number(
            cand.invoice_number
        )

    vendor_sim = _vendor_similarity(request.vendor_name, cand.vendor_name)
    vendor_match = vendor_sim >= request.vendor_fuzzy_threshold

    within_amount, amount_closeness = _amounts_within_tolerance(
        request.amount, cand.amount, request.amount_tolerance_pct
    )

    same_date = bool(request.date and cand.date and request.date == cand.date)

    # --- Exact duplicate: same invoice number + same vendor ---
    if same_number and vendor_match:
        reasons.append("Identical invoice number for the same vendor.")
        if within_amount:
            reasons.append("Amounts match within tolerance.")
        return DuplicateMatch(
            invoice_id=cand.id,
            invoice_number=cand.invoice_number,
            vendor_name=cand.vendor_name,
            amount=cand.amount,
            date=cand.date,
            match_type="exact",
            confidence=round(min(1.0, 0.9 + 0.1 * amount_closeness), 4),
            reasons=reasons,
        )

    # --- Near-duplicate: same vendor + amount within tolerance (+ corroboration) ---
    if vendor_match and within_amount:
        score = 0.5 + 0.3 * (vendor_sim / 100.0) + 0.2 * amount_closeness
        reasons.append(f"Vendor match ({vendor_sim:.0f}%) with amount within tolerance.")
        if same_number:
            reasons.append("Same invoice number.")
            score = min(1.0, score + 0.1)
        if same_date:
            reasons.append("Same invoice date.")
            score = min(1.0, score + 0.05)
        return DuplicateMatch(
            invoice_id=cand.id,
            invoice_number=cand.invoice_number,
            vendor_name=cand.vendor_name,
            amount=cand.amount,
            date=cand.date,
            match_type="near",
            confidence=round(min(1.0, score), 4),
            reasons=reasons,
        )

    # --- Weak near-duplicate: same number, different/unknown vendor ---
    if same_number and request.invoice_number:
        reasons.append("Same invoice number but vendor did not match confidently.")
        return DuplicateMatch(
            invoice_id=cand.id,
            invoice_number=cand.invoice_number,
            vendor_name=cand.vendor_name,
            amount=cand.amount,
            date=cand.date,
            match_type="near",
            confidence=0.6,
            reasons=reasons,
        )

    return None


def detect_duplicates(request: DuplicateCheckRequest) -> DuplicateCheckResult:
    """Find exact and near-duplicates of the candidate among the supplied invoices."""
    notes: list[str] = []

    candidates = request.candidates
    if request.lookback_days is not None and request.date is not None:
        cutoff = request.date - timedelta(days=request.lookback_days)
        before = len(candidates)
        candidates = [c for c in candidates if c.date is None or c.date >= cutoff]
        skipped = before - len(candidates)
        if skipped:
            notes.append(
                f"Ignored {skipped} invoice(s) older than the {request.lookback_days}-day window."
            )

    matches: list[DuplicateMatch] = []
    for cand in candidates:
        match = _evaluate_candidate(request, cand)
        if match is not None:
            matches.append(match)

    matches.sort(key=lambda m: m.confidence, reverse=True)

    highest = matches[0].confidence if matches else 0.0
    is_duplicate = any(m.match_type == "exact" for m in matches)
    is_near = any(m.match_type == "near" for m in matches)

    if is_duplicate:
        notes.append("Exact duplicate detected — do not process.")
    elif is_near:
        notes.append("Potential near-duplicate detected — review before processing.")
    else:
        notes.append("No duplicates found.")

    return DuplicateCheckResult(
        is_duplicate=is_duplicate,
        is_near_duplicate=is_near,
        highest_confidence=highest,
        matches=matches,
        notes=notes,
    )
