"""Core invoice-intelligence services (the deterministic helper tools + LLM decision).

Each helper service is a pure function over plain schemas so it can be reused
from the REST API, the MCP server, and the agent orchestration layer. The
approval decision itself is made by the LLM (see
:mod:`ap_invoice.services.llm_decision`).
"""

from ap_invoice.services.completeness import check_completeness
from ap_invoice.services.duplicate_detector import detect_duplicates
from ap_invoice.services.extraction import extract_invoice
from ap_invoice.services.llm_decision import decide as decide_invoice
from ap_invoice.services.payment_terms import calculate_payment_terms
from ap_invoice.services.vendor_normaliser import normalise_vendor

__all__ = [
    "calculate_payment_terms",
    "check_completeness",
    "decide_invoice",
    "detect_duplicates",
    "extract_invoice",
    "normalise_vendor",
]
