"""Request/response schemas for the self-service auth flow."""

from __future__ import annotations

import uuid

from pydantic import EmailStr, Field, field_validator

from ap_invoice.core.config import get_settings
from ap_invoice.schemas.common import APIModel


class RegisterRequest(APIModel):
    """Create an account: an organization + its owner user."""

    email: EmailStr
    password: str = Field(min_length=8, max_length=200)
    organization_name: str | None = Field(
        default=None,
        max_length=255,
        description="Workspace name. Defaults to one derived from the email.",
    )

    @field_validator("password")
    @classmethod
    def _password_policy(cls, value: str) -> str:
        min_len = get_settings().password_min_length
        if len(value) < min_len:
            raise ValueError(f"Password must be at least {min_len} characters.")
        return value


class VerifyRequest(APIModel):
    """Confirm an email address with the OTP that was sent to it."""

    email: EmailStr
    code: str = Field(min_length=4, max_length=10)


class ResendRequest(APIModel):
    """Request a fresh verification OTP."""

    email: EmailStr


class LoginRequest(APIModel):
    """Authenticate with email + password."""

    email: EmailStr
    password: str = Field(min_length=1, max_length=200)


class TokenResponse(APIModel):
    """A session access token (JWT) issued on verify/login."""

    access_token: str
    token_type: str = "bearer"  # noqa: S105 - a token-type label, not a secret
    expires_in: int = Field(description="Token lifetime in seconds.")
    organization_id: uuid.UUID


class MeResponse(APIModel):
    """The authenticated user and their organization."""

    user_id: uuid.UUID
    email: EmailStr
    is_email_verified: bool
    organization_id: uuid.UUID
    organization_name: str
