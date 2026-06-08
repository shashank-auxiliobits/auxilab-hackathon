# Changelog

All notable changes to this project are documented here. The format is based on
[Keep a Changelog](https://keepachangelog.com/) and this project adheres to
[Semantic Versioning](https://semver.org/).

## [0.1.0] - 2026-06-05

Initial release.

### Added
- Multi-tenant data model: organizations, hashed API keys, vendors, **versioned
  vendor policies**, invoices, line items, and an append-only audit trail.
- Five invoice-intelligence tools as pure, unit-tested services:
  Invoice Field Extractor (hybrid LLM + deterministic), Vendor Name Normaliser,
  Duplicate Invoice Detector, Payment Terms Calculator, Invoice Completeness
  Checker.
- Deterministic, rule-based **policy engine** (auto-approve / hold / flag /
  reject) and an **orchestrator** that runs the full pipeline and writes an
  immutable audit trail.
- **REST API** (FastAPI): admin provisioning, vendor & policy CRUD, invoice
  ingestion & processing, the five tools, audit-trail retrieval, health probes;
  per-org API-key auth, rate limiting, structured logging.
- **MCP server** (FastMCP) exposing the tools over streamable-HTTP and stdio with
  per-organization authentication. Includes reporting tools (`invoice_stats`,
  `list_invoices`) so agents can query approved/flagged invoices and a REST
  `/invoices/stats` endpoint.
- Alembic migrations, Docker multi-stage image + Compose stack, GitHub Actions CI
  (lint, type-check, unit + integration tests), and full documentation.
- One-command local setup script (`scripts/setup.sh` / `make setup`): installs
  `uv` + deps, generates a `.env` with secrets, starts Postgres, runs migrations,
  and optionally seeds a demo org + API key.
- **Policy documents + RAG compiler**: attach a free-form vendor policy document;
  it is chunked, embedded (offline default embedder, JSON-stored, cosine search),
  and compiled by an LLM (deterministic fallback) into structured, typed
  `policy_rules`. Only human-approved rules are enforced — deterministically — so
  decisions stay reproducible and injection-safe.
- **Autonomy (touchless processing)**: auto-onboarding of unknown vendors
  (held, not auto-approved), confidence gating for LLM extraction, and
  agent/human status transitions (`approve` / `hold` / `flag` / `reject`) via
  REST `/invoices/{id}/status` and the MCP `update_invoice_status` tool. Payment
  is intentionally out of scope.
