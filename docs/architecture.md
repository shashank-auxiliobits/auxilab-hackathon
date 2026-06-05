# Architecture

## Overview

AP Invoice Intelligence is a **headless, multi-tenant backend**. It has two
network surfaces over one shared core:

- a **REST API** (FastAPI) for systems integration and administration, and
- an **MCP server** (FastMCP) so AI agents can call the same capabilities as tools.

Both surfaces authenticate with the same per-organization API keys and delegate
to the same pure service layer and deterministic policy engine.

```
                ┌───────────────────────────────────────────────┐
   AI agent ───►│  MCP server (streamable-HTTP / stdio)          │
                │   tools: extract / normalise / duplicates /    │
                │          payment-terms / completeness /        │
                │          process_invoice / list_vendors        │
                └───────────────┬───────────────────────────────┘
                                │
   Systems  ───►┌───────────────┴───────────────────────────────┐
   & admins     │  REST API (FastAPI)                            │
                │   /admin /vendors /invoices /tools /health     │
                └───────────────┬───────────────────────────────┘
                                │  shared
                ┌───────────────┴───────────────────────────────┐
                │  Service layer (pure, deterministic)           │
                │   extraction (hybrid LLM + regex)              │
                │   vendor normaliser · duplicate detector       │
                │   payment terms · completeness                 │
                │   policy engine · orchestrator                 │
                └───────────────┬───────────────────────────────┘
                                │  SQLAlchemy 2.0 (async)
                ┌───────────────┴───────────────────────────────┐
                │  PostgreSQL                                    │
                │   orgs · api_keys · vendors · policies ·       │
                │   invoices · line_items · processing_events    │
                └───────────────────────────────────────────────┘
```

## Layers

### 1. Service layer (`ap_invoice/services/`)

The heart of the system. Every tool is a **pure function over plain Pydantic
schemas** (`schemas/tools.py`) — no framework, no database coupling — so it is
trivially unit-testable and reusable from REST, MCP, and the orchestrator.

- `extraction/` — hybrid invoice field extractor. `engine.py` chooses the
  strategy: `llm` (Anthropic), `deterministic` (regex/heuristics), or `hybrid`
  (LLM with a deterministic backfill, and a guaranteed fallback if the LLM is
  unavailable).
- `vendor_normaliser.py` — fuzzy vendor matching (rapidfuzz) with
  legal-suffix-insensitive normalisation.
- `duplicate_detector.py` — exact + near-duplicate detection with fuzzy vendor
  matching and an amount tolerance.
- `payment_terms.py` — parses `Net 30`, `2/10 Net 30`, `Due on Receipt`, `EOM`, …
- `completeness.py` — mandatory-field validation and a recommended action.
- `policy_engine.py` — combines the checks into an auditable decision.
- `orchestrator.py` — the end-to-end pipeline that runs the checks against the
  database, persists the verdict, and writes the audit trail.

### 2. Data layer (`ap_invoice/db/`, `ap_invoice/models/`)

SQLAlchemy 2.0 async models with typed `Mapped[...]` columns. Enum columns use a
portable VARCHAR-backed type (`db.base.str_enum`) that round-trips to the Python
enum. Schema changes are managed with **Alembic** (async `env.py`); the app never
calls `create_all` in production.

### 3. API layer (`ap_invoice/api/`)

FastAPI app factory (`main.create_app`) with dependency-injected sessions and
auth, structured request-scoped logging, rate limiting, and consistent JSON
error envelopes.

### 4. MCP layer (`ap_invoice/mcp/`)

A FastMCP server exposing the tools. Each call authenticates from the request's
`Authorization` header (or `AP_MCP_API_KEY` for stdio) and is scoped to the
caller's organization.

## Request flow: processing an invoice

1. Agent (or a client) submits raw invoice text.
2. **Extract** → structured fields with per-field confidence.
3. **Normalise vendor** against the org's vendor master → sets `vendor_id`.
4. Resolve the vendor's **active policy** (versioned).
5. **Completeness** check against the policy's mandatory fields.
6. **Duplicate** check against the org's recent invoices.
7. **Payment terms** parsed; due date computed.
8. **Policy engine** combines everything → `auto_approve` / `hold` / `flag` /
   `reject`.
9. The invoice's status + recommended action are persisted, and a
   `ProcessingEvent` is written for **every step** (immutable audit trail).

## Key design decisions

- **Deterministic decisions.** The agent orchestrates; the *verdict* is
  rule-based so it is explainable, reproducible, and auditable — essential for
  finance/compliance.
- **Hybrid extraction.** LLM accuracy where a key is configured, with a
  deterministic fallback that runs offline and in CI.
- **Per-org API keys, tenant isolation.** Every query is scoped by
  `organization_id`; keys are Argon2-hashed with a server-side pepper.
- **Versioned policies.** Policies are never edited in place, so a historical
  decision can be reproduced against the exact policy that was active.
- **Append-only audit trail.** `processing_events` rows are never updated or
  deleted.
- **Idempotent ingestion.** An optional `idempotency_key` makes re-submitting the
  same document a no-op.
