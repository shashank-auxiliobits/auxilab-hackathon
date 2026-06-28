# Deployment

## Docker Compose (self-hosted)

The included `docker-compose.yml` runs Postgres, the API, and the MCP server.

```bash
# Required secrets (do NOT use the dev defaults in production)
export AP_API_KEY_PEPPER=$(python -c "import secrets; print(secrets.token_urlsafe(48))")
export AP_JWT_SECRET=$(python -c "import secrets; print(secrets.token_urlsafe(48))")
export AP_ANTHROPIC_API_KEY=sk-ant-...      # optional; enables the LLM extractor

docker compose up -d --build
```

- API → `http://localhost:8000` (OpenAPI at `/docs`)
- MCP → `http://localhost:8080/mcp`
- The API container runs **Alembic migrations on startup** (`scripts/entrypoint.sh`).

The image is multi-stage, runs as a **non-root** user, and has a health check.

## Migrations

Migrations are managed with Alembic and applied automatically by the container
entrypoint (`migrate` then `api`). To run them manually:

```bash
make migrate                      # local (uv)
docker compose run --rm api migrate   # in a container
```

Create a new migration after changing models:

```bash
make revision m="add foo to bar"
make migrate
```

Never use `create_all` in production — it is only used by the integration tests.

## Production hardening checklist

- [ ] Set a strong, unique `AP_API_KEY_PEPPER` and `AP_JWT_SECRET` from a secret
      store (rotating the pepper invalidates existing keys — plan a re-issue).
- [ ] `AP_ENVIRONMENT=production`, `AP_LOG_JSON=true`.
- [ ] Terminate TLS at a reverse proxy / load balancer in front of both services.
- [ ] Restrict `AP_CORS_ALLOW_ORIGINS` to known origins.
- [ ] Put the MCP server behind auth/network controls appropriate to your agents.
- [ ] Use managed Postgres with backups + PITR; the audit trail is your system of
      record for AP decisions.
- [ ] Tune `AP_RATE_LIMIT` and DB pool sizes for your load.
- [ ] Scale the API horizontally (stateless); run a single migration step on
      deploy rather than per-replica.
- [ ] Monitor `/health/ready` and ship the JSON logs to your aggregator.

## LLM is mandatory

Both invoice extraction (vision) and the approval decision are handled by one
configured LLM provider and are required; there is no offline/deterministic
fallback. Set `AP_LLM_PROVIDER` (`claude` or `openai`) and its API key. To point
GPT at an OpenAI-compatible gateway, set `AP_OPENAI_BASE_URL`.

## Kubernetes (sketch)

The image works as-is. Run two Deployments from the same image with different
commands — `["api"]` and `["mcp"]` — plus a one-shot migration Job
(`["migrate"]`) as a pre-deploy hook. Expose readiness on `/health/ready`.
