"""FastAPI application factory and entrypoint."""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager

import structlog
from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.middleware import SlowAPIMiddleware
from slowapi.util import get_remote_address

from ap_invoice.api.errors import register_exception_handlers
from ap_invoice.api.routes import admin, health, invoices, tools, vendors
from ap_invoice.core.config import get_settings
from ap_invoice.core.logging import configure_logging, get_logger
from ap_invoice.db.session import dispose_engine

logger = get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    configure_logging()
    settings = get_settings()
    logger.info(
        "api_startup",
        environment=settings.environment,
        extractor_engine=settings.extractor_engine,
        llm_available=settings.llm_available,
    )
    yield
    await dispose_engine()
    logger.info("api_shutdown")


def create_app() -> FastAPI:
    settings = get_settings()
    configure_logging()

    limiter = Limiter(key_func=get_remote_address, default_limits=[settings.rate_limit])

    app = FastAPI(
        title="AP Invoice Intelligence",
        description=(
            "REST API + MCP backend for AI-agent-driven invoice automation against "
            "per-vendor policies."
        ),
        version="0.1.0",
        root_path=settings.api_root_path,
        lifespan=lifespan,
    )
    app.state.limiter = limiter
    app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)  # type: ignore[arg-type]
    app.add_middleware(SlowAPIMiddleware)

    if settings.cors_allow_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=settings.cors_allow_origins,
            allow_credentials=True,
            allow_methods=["*"],
            allow_headers=["*"],
        )

    @app.middleware("http")
    async def request_context(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        request_id = request.headers.get("X-Request-ID", str(uuid.uuid4()))
        structlog.contextvars.clear_contextvars()
        structlog.contextvars.bind_contextvars(
            request_id=request_id, path=request.url.path, method=request.method
        )
        response = await call_next(request)
        response.headers["X-Request-ID"] = request_id
        return response

    register_exception_handlers(app)

    app.include_router(health.router)
    app.include_router(admin.router)
    app.include_router(vendors.router)
    app.include_router(invoices.router)
    app.include_router(tools.router)

    return app


app = create_app()


def run() -> None:
    """Console-script entrypoint: ``ap-invoice-api``."""
    import uvicorn

    settings = get_settings()
    uvicorn.run(
        "ap_invoice.api.main:app",
        host=settings.api_host,
        port=settings.api_port,
        log_config=None,
    )
