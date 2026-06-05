"""Shared test configuration.

Sets a deterministic, offline test environment *before* the application package
is imported, so unit tests never need a database or an LLM key.
"""

from __future__ import annotations

import os

os.environ.setdefault("AP_ENVIRONMENT", "test")
os.environ.setdefault("AP_API_KEY_PEPPER", "test-pepper-for-pytest-only")
os.environ.setdefault("AP_ADMIN_TOKEN", "test-admin-token")
os.environ.setdefault("AP_EXTRACTOR_ENGINE", "deterministic")
os.environ.setdefault("AP_LOG_JSON", "false")
os.environ.setdefault(
    "AP_DATABASE_URL",
    "postgresql+asyncpg://ap:ap_password@localhost:5432/ap_invoice_test",
)

from ap_invoice.core.config import get_settings

# Ensure the cached settings reflect the env we just set.
get_settings.cache_clear()
