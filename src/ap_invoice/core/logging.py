"""Structured logging configuration using structlog.

Produces JSON logs in production (machine-parseable for log aggregators) and
human-friendly console logs in development. Call :func:`configure_logging`
once at process startup.
"""

from __future__ import annotations

import logging
import sys

import structlog

from ap_invoice.core.config import get_settings


def configure_logging() -> None:
    """Configure stdlib logging + structlog according to settings."""
    settings = get_settings()
    level = getattr(logging, settings.log_level)

    # In stdio MCP transport, stdout is reserved for JSON-RPC. Redirect all logs to stderr.
    log_stream = sys.stderr if settings.mcp_transport == "stdio" else sys.stdout

    logging.basicConfig(
        format="%(message)s",
        stream=log_stream,
        level=level,
    )

    shared_processors: list[structlog.types.Processor] = [
        structlog.contextvars.merge_contextvars,
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
    ]

    renderer: structlog.types.Processor = (
        structlog.processors.JSONRenderer()
        if settings.log_json
        else structlog.dev.ConsoleRenderer()
    )

    structlog.configure(
        processors=[*shared_processors, renderer],
        wrapper_class=structlog.make_filtering_bound_logger(level),
        logger_factory=structlog.PrintLoggerFactory(file=log_stream),
        cache_logger_on_first_use=True,
    )


def get_logger(name: str | None = None) -> structlog.stdlib.BoundLogger:
    """Return a bound structlog logger."""
    logger: structlog.stdlib.BoundLogger = structlog.get_logger(name)
    return logger
