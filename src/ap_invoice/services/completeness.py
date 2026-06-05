"""Invoice Completeness Checker.

Validates extracted invoice fields against a configurable mandatory-field list
and returns a completeness score, the missing fields, and a recommended action
(Process / Hold / Return to Vendor).
"""

from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from ap_invoice.core.enums import CompletenessAction
from ap_invoice.schemas.tools import CompletenessRequest, CompletenessResult, FieldStatus


def _is_present(value: Any) -> bool:
    """A field counts as present if it is not None and not empty/blank."""
    if value is None:
        return False
    if isinstance(value, str):
        return value.strip() != ""
    if isinstance(value, list | dict | tuple | set):
        return len(value) > 0
    return True


def check_completeness(request: CompletenessRequest) -> CompletenessResult:
    """Score completeness and recommend an action based on configured thresholds."""
    required = request.mandatory_fields
    statuses: list[FieldStatus] = []
    present: list[str] = []
    missing: list[str] = []

    for field in required:
        ok = _is_present(request.fields.get(field))
        statuses.append(FieldStatus(field=field, present=ok))
        (present if ok else missing).append(field)

    total = len(required)
    if total == 0:
        score = Decimal("100.00")
    else:
        score = (Decimal(len(present)) / Decimal(total) * Decimal("100")).quantize(
            Decimal("0.01"), rounding=ROUND_HALF_UP
        )

    if score >= request.process_threshold:
        action = CompletenessAction.PROCESS
    elif score >= request.hold_threshold:
        action = CompletenessAction.HOLD
    else:
        action = CompletenessAction.RETURN_TO_VENDOR

    notes: list[str] = []
    if missing:
        notes.append(f"Missing mandatory field(s): {', '.join(missing)}.")
    else:
        notes.append("All mandatory fields are present.")

    return CompletenessResult(
        completeness_score=score,
        total_required=total,
        present_count=len(present),
        present_fields=present,
        missing_fields=missing,
        field_statuses=statuses,
        recommended_action=action,
        notes=notes,
    )
