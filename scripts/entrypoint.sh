#!/usr/bin/env bash
# Container entrypoint. Usage:
#   entrypoint.sh api        -> run Alembic migrations, then the REST API (default)
#   entrypoint.sh mcp        -> run the MCP server (streamable-http)
#   entrypoint.sh migrate    -> run Alembic migrations only
#   entrypoint.sh <other...> -> exec the given command verbatim
set -euo pipefail

run_migrations() {
    echo "[entrypoint] Running database migrations..."
    alembic upgrade head
}

case "${1:-api}" in
    api)
        run_migrations
        echo "[entrypoint] Starting REST API..."
        exec uvicorn ap_invoice.api.main:app \
            --host "${AP_API_HOST:-0.0.0.0}" \
            --port "${AP_API_PORT:-8000}"
        ;;
    mcp)
        echo "[entrypoint] Starting MCP server..."
        exec ap-invoice-mcp
        ;;
    migrate)
        run_migrations
        ;;
    *)
        exec "$@"
        ;;
esac
