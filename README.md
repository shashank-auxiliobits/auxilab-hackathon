# AP Invoice Intelligence

> Production-grade, open-source backend that lets AI agents automate **Accounts Payable invoice processing** — extract, validate, de-duplicate, and approve or flag invoices against **per-vendor policies** — via a clean REST API and a **Model Context Protocol (MCP)** server.

[![License: Apache 2.0](https://img.shields.io/badge/License-Apache_2.0-blue.svg)](./LICENSE)
[![Python 3.12](https://img.shields.io/badge/python-3.12-blue.svg)](https://www.python.org/)

There is **no frontend** by design. AP Invoice Intelligence is a headless platform: a
REST API for your systems and an MCP server so AI agents (Claude, or any
MCP-compatible agent) can read policies, run checks, and take actions — auto-approving
clean invoices and flagging the ones that violate vendor terms.

---

## Why

Accounts-Payable teams drown in manual invoice review: matching vendors, catching
duplicates, checking mandatory fields, computing due dates and early-payment discounts,
and applying each vendor's contractual terms. AP Invoice Intelligence turns those
repetitive judgments into **deterministic, auditable tools** and lets an AI agent
orchestrate them — so humans only touch the exceptions.

## What's in the box

**Multi-tenant domain model**

```
Organization ──< API Keys (hashed)
     │
     └──< Vendor ──< VendorPolicy (payment terms, mandatory fields,
            │                       amount thresholds, tolerances, T&Cs)
            └──< Invoice ──< LineItem
                    │
                    └──< ProcessingEvent  (append-only audit trail)
```

**Five MCP tools** (also exposed as REST endpoints):

| Tool | What it does |
|------|--------------|
| **Invoice Field Extractor** | Parse raw invoice text → structured JSON (number, vendor, dates, line items, totals) with a **confidence score per field**. Hybrid engine: Claude API + deterministic fallback. |
| **Duplicate Invoice Detector** | Detect exact & near-duplicates with fuzzy vendor matching and ±5% amount tolerance. |
| **Vendor Name Normaliser** | Map messy vendor strings (`MSFT Corp.`) to the canonical vendor master; flag unknowns for onboarding. |
| **Payment Terms Calculator** | Parse terms (`Net 30`, `2/10 Net 30`, `Due on Receipt`) → due date, discount deadline & amount, days remaining. |
| **Invoice Completeness Checker** | Validate against a configurable mandatory-field list → completeness %, missing fields, recommended action (Process / Hold / Return). |

Plus a **deterministic rule-based policy engine** that combines these checks per vendor
to recommend **Auto-Approve / Hold / Flag**, with every decision written to an immutable
audit trail.

## Tech stack

Python 3.12 · FastAPI · SQLAlchemy 2.0 (async) + Alembic · Pydantic v2 · PostgreSQL ·
MCP Python SDK (FastMCP, streamable-HTTP + stdio) · Anthropic SDK · structlog ·
pytest / ruff / mypy · Docker.

## Quickstart

### Prerequisites
- [`uv`](https://docs.astral.sh/uv/) (`curl -LsSf https://astral.sh/uv/install.sh | sh`)
- Docker (for PostgreSQL)

### Local development

**One command** — installs `uv`, dependencies, generates a `.env` with secrets,
starts Postgres, runs migrations, seeds a demo org + API key, **runs the full
test suite, and runs a live end-to-end demo**:

```bash
./scripts/setup.sh --all      # or: make setup
```

(Use `./scripts/setup.sh` alone for setup only, or `--seed` to just add demo data.)

Then run the services:

```bash
make run-api     # REST API  → http://127.0.0.1:8000/docs
make run-mcp     # MCP server → http://127.0.0.1:8080/mcp
```

<details>
<summary>Manual setup (if you prefer step-by-step)</summary>

```bash
make install                                   # deps into a uv venv
make db-up                                      # start PostgreSQL
cp .env.example .env                            # then set AP_API_KEY_PEPPER & AP_JWT_SECRET
make migrate                                     # apply migrations
# Optional: bootstrap the first owner directly (otherwise use /auth/register below):
uv run python scripts/seed.py --email you@example.com   # prints login + an API key
```
</details>

### Create your account (self-service)

Register with email + password, verify with the one-time code, then log in for a
session token. With the default `AP_EMAIL_BACKEND=console`, the OTP is **printed to
the API server log** — no mail server needed to try it locally.

```bash
# 1. Register (creates your organization). The OTP is logged by the API.
curl -s -X POST http://localhost:8000/auth/register \
  -H "content-type: application/json" \
  -d '{"email":"you@example.com","password":"a-strong-password"}'

# 2. Verify the email with the code from the API log → returns a session token.
curl -s -X POST http://localhost:8000/auth/verify \
  -H "content-type: application/json" \
  -d '{"email":"you@example.com","code":"123456"}'
# → {"access_token":"<jwt>","token_type":"bearer","expires_in":3600, ...}

# 3. Later, log in any time with email + password for a fresh token.
curl -s -X POST http://localhost:8000/auth/login \
  -H "content-type: application/json" \
  -d '{"email":"you@example.com","password":"a-strong-password"}'
```

Then onboard a vendor and its policy (the policy is the source of truth — there is
no seeded/demo data; everything lives in your database), and process an invoice:

```bash
TOKEN="<access_token from verify/login>"
AUTH="Authorization: Bearer $TOKEN"

# 1. Create a vendor with a policy.
VID=$(curl -s -X POST http://localhost:8000/vendors -H "$AUTH" \
  -H "content-type: application/json" \
  -d '{"canonical_name":"Microsoft Corporation","aliases":["Microsoft","MSFT"]}' \
  | python -c "import sys,json;print(json.load(sys.stdin)['id'])")

# 2. Upload the vendor's policy (the decision LLM judges invoices against this).
curl -s -X POST http://localhost:8000/vendors/$VID/documents -H "$AUTH" \
  -H "content-type: application/json" \
  -d '{"filename":"policy.txt","text":"Payment terms 2/10 Net 30. Invoices must not exceed $5,000. All invoices in USD."}'

# 3. Process an invoice — judged against the policy you just uploaded.
curl -s -X POST http://localhost:8000/invoices/process -H "$AUTH" \
  -H "content-type: application/json" \
  -d '{"raw_text":"Microsoft\nInvoice Number: INV-1\nInvoice Date: 2026-06-01\nPayment Terms: 2/10 Net 30\nGrand Total: $1,250.00"}'
# → {"decision":"auto_approve","status":"approved", ...}
```

> Programmatic and MCP clients use **API keys** (`Authorization: Bearer ap_<prefix>.<secret>`),
> which a logged-in user mints at `POST /api-keys`. There is no shared admin token.

#### Multi-file invoices (scans, multi-page PDFs, attachments)

An invoice can be supplied as **one or more files** — a scan split into per-page
images, a multi-page PDF, or an invoice plus supporting attachments — all
extracted together as a single invoice. Use the `files` array (each entry is a
base64 file with an optional `content_type` and `filename`); `raw_text` and a
single `file_base64` still work and are combined with `files` if all are given.
`content_type` is optional — it's sniffed from the file's magic bytes when omitted.

```bash
curl -s -X POST http://localhost:8000/invoices/process \
  -H "Authorization: Bearer ap_<prefix>.<secret>" \
  -H "content-type: application/json" \
  -d '{
        "files": [
          {"file_base64": "<page1-base64>", "content_type": "image/png", "filename": "page1.png"},
          {"file_base64": "<page2-base64>", "content_type": "image/jpeg", "filename": "page2.jpg"}
        ]
      }'
```

Limits are configurable: `AP_MAX_FILES_PER_INVOICE` (default 10),
`AP_MAX_FILE_BYTES` (default 10 MiB per file), and `AP_MAX_EXTRACTION_IMAGES`
(default 16 image parts — PDF pages + images — sent to the model per extraction).
Malformed, oversized, too-many, or unsupported files return **422**; the same
`files` array is available on `/invoices/ingest` and the `extract_invoice_fields`
and `process_invoice_text` MCP tools.

### Full stack with Docker

```bash
export AP_API_KEY_PEPPER=$(python -c "import secrets; print(secrets.token_urlsafe(48))")
export AP_ANTHROPIC_API_KEY=sk-ant-...   # optional; enables the LLM extractor
docker compose up -d
```

API → `http://localhost:8000` · MCP → `http://localhost:8080` · OpenAPI docs → `/docs`.

## Project layout

```
src/ap_invoice/
  core/        config, logging, security (API-key hashing), enums
  db/          async engine/session, declarative base
  models/      SQLAlchemy models (orgs, keys, vendors, policies, invoices, audit)
  schemas/     Pydantic request/response + tool I/O schemas
  services/    the 5 tools (pure), policy engine, orchestrator, ingestion, auth
  api/         FastAPI app, dependencies, routes
  mcp/         FastMCP server exposing the tools
alembic/       migrations
tests/         unit/ (no DB) + integration/ (Postgres)
docs/          full documentation
```

## Documentation

Full docs live in [`docs/`](./docs):

- [Architecture](./docs/architecture.md) · [Data Model](./docs/data-model.md) · [Configuration](./docs/configuration.md)
- [REST API Reference](./docs/api-reference.md) · [MCP Tools](./docs/mcp-tools.md) · [Policy Engine](./docs/policy-engine.md)
- [Deployment](./docs/deployment.md) · [Contributing](./CONTRIBUTING.md) · [Security](./SECURITY.md)

## License

[Apache-2.0](./LICENSE).
