"""Vendor Name Normaliser.

Matches a raw vendor name as it appears on an invoice against a vendor master,
using exact, alias, and fuzzy matching (with legal-suffix-insensitive
normalization). Unrecognised vendors are flagged for onboarding.
"""

from __future__ import annotations

from rapidfuzz import fuzz

from ap_invoice.schemas.tools import (
    VendorMasterEntry,
    VendorMatch,
    VendorNormaliseRequest,
    VendorNormaliseResult,
)
from ap_invoice.services._parsing import normalize_vendor_name


def _score(a: str, b: str) -> float:
    """Symmetric fuzzy similarity in [0, 100] robust to word order & extra tokens."""
    return float(max(fuzz.token_sort_ratio(a, b), fuzz.token_set_ratio(a, b)))


def normalise_vendor(request: VendorNormaliseRequest) -> VendorNormaliseResult:
    """Resolve a raw vendor name to a canonical vendor, or flag for onboarding."""
    raw = request.raw_name
    norm_query = normalize_vendor_name(raw)
    notes: list[str] = []

    candidates: list[VendorMatch] = []
    for entry in request.vendor_master:
        match = _best_match_for_entry(norm_query, raw, entry)
        if match is not None:
            candidates.append(match)

    # Rank: exact > alias > fuzzy, then by score.
    type_rank = {"exact": 2, "alias": 1, "fuzzy": 0}
    candidates.sort(key=lambda m: (type_rank[m.match_type], m.score), reverse=True)

    best = candidates[0] if candidates else None
    is_recognized = best is not None and (
        best.match_type in ("exact", "alias") or best.score >= request.threshold
    )

    if not is_recognized:
        notes.append(
            f"No confident match for '{raw}' (threshold {request.threshold:.0f}). "
            "Flagged for vendor onboarding."
        )
        best = None
    else:
        notes.append(f"Matched '{raw}' to '{best.canonical_name}' via {best.match_type} match.")  # type: ignore[union-attr]

    suggestions = [m for m in candidates if m is not best][: request.suggestion_limit]

    return VendorNormaliseResult(
        raw_name=raw,
        normalized_query=norm_query,
        is_recognized=is_recognized,
        match=best if is_recognized else None,
        suggestions=suggestions,
        recommend_onboarding=not is_recognized,
        notes=notes,
    )


def _best_match_for_entry(
    norm_query: str, raw: str, entry: VendorMasterEntry
) -> VendorMatch | None:
    """Compute the best match between the query and a single master entry."""
    names = [entry.canonical_name, *entry.aliases]
    best_score = -1.0

    for idx, name in enumerate(names):
        norm_name = normalize_vendor_name(name)
        is_alias = idx > 0
        if norm_name == norm_query:
            # Exact on canonical, alias-exact on an alias.
            return VendorMatch(
                vendor_id=entry.id,
                canonical_name=entry.canonical_name,
                score=100.0,
                match_type="alias" if is_alias else "exact",
            )
        score = _score(norm_query, norm_name)
        best_score = max(best_score, score)

    if best_score < 0:
        return None
    # An alias that only fuzzy-matches is still reported as a fuzzy match.
    return VendorMatch(
        vendor_id=entry.id,
        canonical_name=entry.canonical_name,
        score=round(best_score, 2),
        match_type="fuzzy",
    )
