"""Payment Terms Calculator.

Parses free-form payment-term strings (``Net 30``, ``2/10 Net 30``,
``Due on Receipt``, ``1.5/15 Net 45``, ``EOM``, ``COD`` ...) and computes the
net due date, any early-payment-discount deadline and amount, and the days
remaining to each milestone.
"""

from __future__ import annotations

import re
from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal

from ap_invoice.schemas.tools import (
    PaymentMilestone,
    PaymentTermsRequest,
    PaymentTermsResult,
)

# "2/10 Net 30", "2/10 net 30", "1.5/15 N30"
_DISCOUNT_RE = re.compile(
    r"(?P<disc>\d+(?:\.\d+)?)\s*/\s*(?P<ddays>\d+)\s*(?:,)?\s*(?:net|n)\s*(?P<net>\d+)",
    re.IGNORECASE,
)
# "Net 30", "Net30", "N 45"
_NET_RE = re.compile(r"\b(?:net|n)\s*(?P<net>\d+)\b", re.IGNORECASE)
_DUE_ON_RECEIPT_RE = re.compile(
    r"due\s*(?:on|upon)?\s*receipt|^dor$|payable on receipt", re.IGNORECASE
)
_COD_RE = re.compile(r"\bc\.?o\.?d\.?\b|cash on delivery", re.IGNORECASE)
_EOM_RE = re.compile(r"\b(?:net\s*)?(?P<net>\d+)?\s*eom\b|end of month", re.IGNORECASE)


def _round_money(value: Decimal) -> Decimal:
    return value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def _days_between(target: date, reference: date) -> int:
    return (target - reference).days


def calculate_payment_terms(request: PaymentTermsRequest) -> PaymentTermsResult:
    """Compute due dates, discount milestones, and days remaining from terms."""
    terms = request.payment_terms.strip()
    as_of = request.as_of or request.invoice_date
    notes: list[str] = []

    result = PaymentTermsResult(raw_terms=terms, parsed=False, term_type="unknown")

    # --- Due on Receipt ---
    if _DUE_ON_RECEIPT_RE.search(terms):
        result.parsed = True
        result.term_type = "due_on_receipt"
        result.net_days = 0
        result.due_date = request.invoice_date
    # --- COD ---
    elif _COD_RE.search(terms):
        result.parsed = True
        result.term_type = "cod"
        result.net_days = 0
        result.due_date = request.invoice_date
        notes.append("Cash on delivery: payment due at delivery.")
    else:
        discount_match = _DISCOUNT_RE.search(terms)
        net_match = _NET_RE.search(terms)
        eom_match = _EOM_RE.search(terms)

        if discount_match:
            result.parsed = True
            result.term_type = "discount"
            result.discount_percent = Decimal(discount_match.group("disc"))
            result.discount_days = int(discount_match.group("ddays"))
            result.net_days = int(discount_match.group("net"))
            result.due_date = request.invoice_date + timedelta(days=result.net_days)
            result.discount_deadline = request.invoice_date + timedelta(days=result.discount_days)
        elif eom_match:
            # Checked before plain net: "Net 15 EOM" is end-of-month, not Net 15.
            result.parsed = True
            result.term_type = "eom"
            extra = int(eom_match.group("net")) if eom_match.group("net") else 0
            # End of the invoice month, plus any trailing net days.
            if request.invoice_date.month == 12:
                end_of_month = date(request.invoice_date.year, 12, 31)
            else:
                first_next = date(request.invoice_date.year, request.invoice_date.month + 1, 1)
                end_of_month = first_next - timedelta(days=1)
            result.due_date = end_of_month + timedelta(days=extra)
            result.net_days = _days_between(result.due_date, request.invoice_date)
        elif net_match:
            result.parsed = True
            result.term_type = "net"
            result.net_days = int(net_match.group("net"))
            result.due_date = request.invoice_date + timedelta(days=result.net_days)

    if not result.parsed:
        notes.append(f"Could not parse payment terms: '{terms}'. Treat manually.")
        result.notes = notes
        return result

    # --- Discount amount ---
    if result.discount_percent is not None and request.amount is not None and request.amount > 0:
        discount_amount = _round_money(request.amount * result.discount_percent / Decimal("100"))
        result.discount_amount = discount_amount
        result.amount_after_discount = _round_money(request.amount - discount_amount)

    # --- Days remaining ---
    milestones: list[PaymentMilestone] = []
    if result.discount_deadline is not None:
        result.days_until_discount_deadline = _days_between(result.discount_deadline, as_of)
        milestones.append(
            PaymentMilestone(
                label="early_payment_discount",
                due_on=result.discount_deadline,
                days_remaining=result.days_until_discount_deadline,
                amount_due=result.amount_after_discount,
            )
        )
    if result.due_date is not None:
        result.days_until_due = _days_between(result.due_date, as_of)
        milestones.append(
            PaymentMilestone(
                label="net_due",
                due_on=result.due_date,
                days_remaining=result.days_until_due,
                amount_due=request.amount,
            )
        )

    if result.days_until_due is not None and result.days_until_due < 0:
        notes.append("Invoice is past its net due date.")
    if result.days_until_discount_deadline is not None and result.days_until_discount_deadline < 0:
        notes.append("Early-payment discount window has closed.")

    result.milestones = milestones
    result.notes = notes
    return result
