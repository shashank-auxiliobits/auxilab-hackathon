"""Core invoice-intelligence services (the five MCP tools + policy engine).

Each service is a pure function over plain schemas so it can be reused from the
REST API, the MCP server, and the agent orchestration layer.
"""

from ap_invoice.services.completeness import check_completeness
from ap_invoice.services.duplicate_detector import detect_duplicates
from ap_invoice.services.extraction import extract_invoice
from ap_invoice.services.payment_terms import calculate_payment_terms
from ap_invoice.services.policy_engine import evaluate_policy
from ap_invoice.services.vendor_normaliser import normalise_vendor

__all__ = [
    "calculate_payment_terms",
    "check_completeness",
    "detect_duplicates",
    "evaluate_policy",
    "extract_invoice",
    "normalise_vendor",
]
