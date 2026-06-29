"""Unit tests for configuration validation (fail-fast guards)."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from ap_invoice.core.config import Settings


def test_smtp_backend_without_host_fails_fast() -> None:
    with pytest.raises(ValidationError, match="AP_SMTP_HOST"):
        Settings(_env_file=None, email_backend="smtp")


def test_smtp_backend_with_host_is_accepted() -> None:
    settings = Settings(_env_file=None, email_backend="smtp", smtp_host="smtp.example.com")
    assert settings.email_backend == "smtp"


def test_production_refuses_console_email() -> None:
    with pytest.raises(ValidationError, match="smtp"):
        Settings(_env_file=None, environment="production", email_backend="console")
