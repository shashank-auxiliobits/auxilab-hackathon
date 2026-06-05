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

```bash
# 1. Install dependencies
make install

# 2. Start PostgreSQL
make db-up

# 3. Configure environment
cp .env.example .env
# generate a real pepper:
python -c "import secrets; print('AP_API_KEY_PEPPER=' + secrets.token_urlsafe(48))" >> .env

# 4. Apply migrations
make migrate

# 5. Seed a demo org + API key + vendor (prints the key)
make seed

# 6. Run the API (http://127.0.0.1:8000/docs)
make run-api

# 7. In another shell, run the MCP server
make run-mcp
```

Then process your first invoice (use the API key printed by `make seed`):

```bash
curl -s -X POST http://localhost:8000/invoices/process \
  -H "Authorization: Bearer ap_<prefix>.<secret>" \
  -H "content-type: application/json" \
  -d '{"raw_text":"Microsoft\nInvoice Number: INV-1\nInvoice Date: 2026-06-01\nPayment Terms: 2/10 Net 30\nGrand Total: $1,250.00"}'
# → {"decision":"auto_approve","status":"approved", ...}
```

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
