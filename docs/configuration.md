# Configuration

All settings come from environment variables prefixed with `AP_` (12-factor).
A local `.env` is read in development; in production inject values from your
orchestrator's secret store. See [`.env.example`](../.env.example).

## Runtime
| Variable | Default | Description |
|----------|---------|-------------|
| `AP_ENVIRONMENT` | `development` | `development` / `staging` / `production` / `test`. `test` uses a NullPool DB engine. |
| `AP_LOG_LEVEL` | `INFO` | `DEBUG` / `INFO` / `WARNING` / `ERROR` |
| `AP_LOG_JSON` | `true` | JSON logs (prod) vs. console logs (dev) |

## Database
| Variable | Default | Description |
|----------|---------|-------------|
| `AP_DATABASE_URL` | `postgresql+asyncpg://ap:ap_password@localhost:5432/ap_invoice` | Async DSN — **must** use the `+asyncpg` driver |
| `AP_DB_POOL_SIZE` | `10` | |
| `AP_DB_MAX_OVERFLOW` | `20` | |
| `AP_DB_ECHO` | `false` | log SQL |

## REST API
| Variable | Default | Description |
|----------|---------|-------------|
| `AP_API_HOST` | `0.0.0.0` | |
| `AP_API_PORT` | `8000` | |
| `AP_API_ROOT_PATH` | `` | when served behind a path prefix |
| `AP_CORS_ALLOW_ORIGINS` | `` | comma-separated origins |
| `AP_RATE_LIMIT` | `120/minute` | default per-client limit (slowapi syntax) |

## Security
| Variable | Default | Description |
|----------|---------|-------------|
| `AP_API_KEY_PEPPER` | `change-me-in-production` | **Required in prod.** Server-side pepper mixed into key hashes. Generate: `python -c "import secrets; print(secrets.token_urlsafe(48))"` |
| `AP_ADMIN_TOKEN` | _(unset)_ | Bearer token for `/admin/*` provisioning. If unset, those endpoints are disabled. |

## MCP server
| Variable | Default | Description |
|----------|---------|-------------|
| `AP_MCP_HOST` | `0.0.0.0` | |
| `AP_MCP_PORT` | `8080` | |
| `AP_MCP_TRANSPORT` | `streamable-http` | `streamable-http` or `stdio` |
| `AP_MCP_API_KEY` | _(unset)_ | API key used to scope stdio calls (no HTTP headers). Over HTTP, clients send their own. |

## LLM provider (mandatory)
One multimodal provider handles **both** stages — invoice extraction (vision over
images/PDFs) and the RAG + approval decision. Choose Claude or GPT.

| Variable | Default | Description |
|----------|---------|-------------|
| `AP_LLM_PROVIDER` | `claude` | `claude` / `openai` |
| `AP_ANTHROPIC_API_KEY` | _(unset)_ | required when provider is `claude` |
| `AP_CLAUDE_MODEL` | `claude-opus-4-8` | Claude model (vision + decision) |
| `AP_OPENAI_API_KEY` | _(unset)_ | required when provider is `openai` |
| `AP_OPENAI_BASE_URL` | _(unset)_ | blank → api.openai.com |
| `AP_OPENAI_MODEL` | `gpt-4o` | GPT model (vision + decision) |
| `AP_EXTRACTOR_MAX_TOKENS` | `4096` | shared LLM token cap |
| `AP_EXTRACTOR_TIMEOUT_SECONDS` | `60` | shared LLM call timeout |

> The LLM is mandatory: if the configured provider has no key, processing fails
> loudly rather than degrading to an offline path.
