"""Application exceptions and FastAPI exception handlers."""

from __future__ import annotations

from fastapi import FastAPI, Request, status
from fastapi.responses import JSONResponse


class APIError(Exception):
    """Base class for domain errors that map to HTTP responses."""

    status_code: int = status.HTTP_400_BAD_REQUEST
    code: str = "error"

    def __init__(self, detail: str) -> None:
        self.detail = detail
        super().__init__(detail)


class NotFoundError(APIError):
    status_code = status.HTTP_404_NOT_FOUND
    code = "not_found"


class ConflictError(APIError):
    status_code = status.HTTP_409_CONFLICT
    code = "conflict"


class AuthenticationError(APIError):
    status_code = status.HTTP_401_UNAUTHORIZED
    code = "authentication_error"


class AuthorizationError(APIError):
    status_code = status.HTTP_403_FORBIDDEN
    code = "authorization_error"


class ValidationError(APIError):
    status_code = 422
    code = "validation_error"


def _error_body(code: str, detail: str) -> dict[str, dict[str, str]]:
    return {"error": {"code": code, "detail": detail}}


def register_exception_handlers(app: FastAPI) -> None:
    """Attach handlers that render APIError (and unexpected errors) as JSON."""

    @app.exception_handler(APIError)
    async def _handle_api_error(_: Request, exc: APIError) -> JSONResponse:
        headers = {}
        if isinstance(exc, AuthenticationError):
            headers["WWW-Authenticate"] = "Bearer"
        return JSONResponse(
            status_code=exc.status_code,
            content=_error_body(exc.code, exc.detail),
            headers=headers,
        )
