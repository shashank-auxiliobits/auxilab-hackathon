"""Self-service authentication: register → verify email (OTP) → login."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, status

from ap_invoice.api.deps import CurrentUser, DBSession
from ap_invoice.api.errors import (
    AuthenticationError,
    AuthorizationError,
    ConflictError,
    ValidationError,
)
from ap_invoice.core.jwt import encode_access_token
from ap_invoice.models.organization import Organization
from ap_invoice.schemas.auth import (
    LoginRequest,
    MeResponse,
    RegisterRequest,
    ResendRequest,
    TokenResponse,
    VerifyRequest,
)
from ap_invoice.services.accounts import (
    EmailAlreadyRegistered,
    EmailNotVerified,
    InvalidCredentials,
    OtpError,
    authenticate_user,
    register_user,
    resend_otp,
    verify_email_otp,
)
from ap_invoice.services.email import get_email_sender

router = APIRouter(prefix="/auth", tags=["auth"])


def _token_response(org_id: uuid.UUID, token: str, expires_in: int) -> TokenResponse:
    return TokenResponse(access_token=token, expires_in=expires_in, organization_id=org_id)


@router.post(
    "/register",
    status_code=status.HTTP_201_CREATED,
    summary="Register a new account (organization + owner) and email a verification code",
)
async def register(payload: RegisterRequest, db: DBSession) -> dict[str, str]:
    try:
        user = await register_user(
            db,
            email=payload.email,
            password=payload.password,
            organization_name=payload.organization_name,
            sender=get_email_sender(),
        )
    except EmailAlreadyRegistered as exc:
        raise ConflictError("An account with this email already exists.") from exc
    return {
        "message": "Registered. Check your email for a verification code, then verify and log in.",
        "email": user.email,
    }


@router.post(
    "/verify",
    response_model=TokenResponse,
    summary="Verify an email with its OTP (returns a session token)",
)
async def verify(payload: VerifyRequest, db: DBSession) -> TokenResponse:
    try:
        user = await verify_email_otp(db, email=payload.email, code=payload.code)
    except OtpError as exc:
        raise ValidationError(str(exc)) from exc
    token, expires_in = encode_access_token(user)
    return _token_response(user.organization_id, token, expires_in)


@router.post(
    "/resend",
    status_code=status.HTTP_202_ACCEPTED,
    summary="Resend a verification code",
)
async def resend(payload: ResendRequest, db: DBSession) -> dict[str, str]:
    await resend_otp(db, email=payload.email, sender=get_email_sender())
    return {"message": "If that email is registered and unverified, a new code has been sent."}


@router.post(
    "/login",
    response_model=TokenResponse,
    summary="Log in with email + password (returns a session token)",
)
async def login(payload: LoginRequest, db: DBSession) -> TokenResponse:
    try:
        user = await authenticate_user(db, email=payload.email, password=payload.password)
    except InvalidCredentials as exc:
        raise AuthenticationError("Invalid email or password.") from exc
    except EmailNotVerified as exc:
        raise AuthorizationError(
            "Email not verified. Check your inbox for the verification code."
        ) from exc
    token, expires_in = encode_access_token(user)
    return _token_response(user.organization_id, token, expires_in)


@router.get("/me", response_model=MeResponse, summary="The authenticated user and organization")
async def me(user: CurrentUser, db: DBSession) -> MeResponse:
    org = await db.get(Organization, user.organization_id)
    return MeResponse(
        user_id=user.id,
        email=user.email,
        is_email_verified=user.is_email_verified,
        organization_id=user.organization_id,
        organization_name=org.name if org else "",
    )
