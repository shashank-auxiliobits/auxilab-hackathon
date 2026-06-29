"""Shared rate limiter.

A single :class:`Limiter` instance is used by both the global middleware
(``main.py``) and per-endpoint decorators (e.g. the auth routes), so they share
the same storage and the per-route limits stack on top of the global default.
"""

from __future__ import annotations

from slowapi import Limiter
from slowapi.util import get_remote_address

from ap_invoice.core.config import get_settings

_settings = get_settings()

# Disabled under the test environment so the shared in-memory counters don't bleed
# across the suite; a dedicated test flips ``limiter.enabled`` on to verify limits.
limiter = Limiter(
    key_func=get_remote_address,
    default_limits=[_settings.rate_limit],
    enabled=_settings.environment != "test",
)

# Tight limits for credential/OTP endpoints (per client IP), layered on top of the
# global default. These blunt password and OTP brute-force; the OTP per-code
# attempt cap (services.accounts) is the second layer.
AUTH_LOGIN_LIMIT = "10/minute"
AUTH_VERIFY_LIMIT = "20/minute"
AUTH_REGISTER_LIMIT = "10/minute"
AUTH_RESEND_LIMIT = "5/minute"
