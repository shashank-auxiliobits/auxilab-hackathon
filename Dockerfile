# syntax=docker/dockerfile:1
# Multi-stage build for AP Invoice Intelligence.
# Stage 1 installs dependencies into a virtualenv with uv; stage 2 is a slim runtime.

FROM python:3.12-slim AS builder

# uv: fast, reproducible installs straight from the official image.
COPY --from=ghcr.io/astral-sh/uv:0.11.19 /uv /uvx /bin/

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never

WORKDIR /app

# Install dependencies first (cached layer), then the project itself.
COPY pyproject.toml uv.lock* README.md ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv venv /app/.venv && \
    uv sync --no-install-project --no-dev || uv pip install --python /app/.venv -e .

COPY src ./src
COPY alembic ./alembic
COPY alembic.ini ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv pip install --python /app/.venv --no-deps -e .


FROM python:3.12-slim AS runtime

# Run as a non-root user — required for hardened production deployments.
RUN groupadd --system app && useradd --system --gid app --create-home app

ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /app

COPY --from=builder /app/.venv /app/.venv
COPY --from=builder /app/src /app/src
COPY --from=builder /app/alembic /app/alembic
COPY --from=builder /app/alembic.ini /app/alembic.ini
COPY pyproject.toml README.md ./
COPY scripts/entrypoint.sh /app/scripts/entrypoint.sh
RUN chmod +x /app/scripts/entrypoint.sh && chown -R app:app /app

USER app

EXPOSE 8000 8080

# Default: run DB migrations then the API. Override CMD for the MCP server.
ENTRYPOINT ["/app/scripts/entrypoint.sh"]
CMD ["api"]
