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
| `AP_API_KEY_PEPPER` | `change-me-in-production` | **Required in prod.** Server-side pepper mixed into API-key and password hashes. Generate: `python -c "import secrets; print(secrets.token_urlsafe(48))"` |

## Auth (users, sessions, email OTP)
| Variable | Default | Description |
|----------|---------|-------------|
| `AP_JWT_SECRET` | `dev-insecure-change-me-in-production` | **Required in prod.** HMAC secret for signing session JWTs (the app refuses to boot in prod/staging with the default). |
| `AP_JWT_EXPIRE_MINUTES` | `60` | Session token lifetime. |
| `AP_PASSWORD_MIN_LENGTH` | `8` | Minimum password length at registration. |
| `AP_OTP_LENGTH` | `6` | Digits in an email OTP. |
| `AP_OTP_TTL_MINUTES` | `10` | OTP validity window. |
| `AP_OTP_MAX_ATTEMPTS` | `5` | Failed OTP guesses before a code is invalidated. |

## Email (OTP delivery)
| Variable | Default | Description |
|----------|---------|-------------|
| `AP_EMAIL_BACKEND` | `console` | `console` logs the email (works out of the box); `smtp` sends it. |
| `AP_EMAIL_FROM` | `no-reply@ap-invoice.local` | From address on outgoing email. |
| `AP_SMTP_HOST` / `AP_SMTP_PORT` | _(unset)_ / `587` | SMTP server (required when `AP_EMAIL_BACKEND=smtp`). |
| `AP_SMTP_USERNAME` / `AP_SMTP_PASSWORD` | _(unset)_ | SMTP credentials, if the server requires auth. |
| `AP_SMTP_USE_TLS` | `true` | STARTTLS on connect. |

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
