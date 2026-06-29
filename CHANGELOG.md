# Changelog

All notable changes to this project are documented here. The format is based on
[Keep a Changelog](https://keepachangelog.com/) and this project adheres to
[Semantic Versioning](https://semver.org/).

## [Unreleased]

### Added
- **Richer MCP toolset for agents (10 → 19 tools).** New read tools let an agent
  query any invoice data: `get_invoice` (full detail + line items),
  `get_invoice_audit_trail` (the "why" behind a decision), `search_invoices`
  (status/vendor/number/amount-range/date-range/text), `get_vendor_policy`, and
  `search_vendor_policy` (RAG). New analytics tools: `spend_analytics`
  (by vendor/month), `payables_aging`, `discount_opportunities`, and
  `automation_metrics` (touchless rate). Backed by reusable org-scoped queries in
  `services/reporting.py`.
- **Self-service authentication.** Users register with **email + password**,
  verify their email with a one-time code (OTP), and log in for a session token
  (JWT) — no shared admin token. New `/auth/register`, `/auth/verify`,
  `/auth/resend`, `/auth/login`, and `/auth/me` endpoints. Tenant endpoints accept
  a session token **or** an API key; API keys are now **self-service** at
  `/api-keys` (create/list/revoke) for the logged-in user's organization.
- Pluggable email delivery (`AP_EMAIL_BACKEND`): `console` (default — logs the
  OTP, works out of the box) or `smtp`. New auth settings: `AP_JWT_SECRET`,
  `AP_JWT_EXPIRE_MINUTES`, `AP_PASSWORD_MIN_LENGTH`, `AP_OTP_LENGTH`,
  `AP_OTP_TTL_MINUTES`, `AP_OTP_MAX_ATTEMPTS`. Migration adds `users` and
  `email_verifications` tables.
- **Multi-file invoice ingestion.** `/invoices/process`, `/invoices/ingest`, and
  the `extract_invoice_fields` / `process_invoice_text` MCP tools now accept a
  `files` array — several pages or attachments extracted together as one invoice
  — alongside the existing `raw_text` and single `file_base64`. `content_type` is
  optional and inferred from each file's magic bytes when omitted.
- Configurable upload limits: `AP_MAX_FILE_BYTES` (per-file size),
  `AP_MAX_FILES_PER_INVOICE` (file count), and `AP_MAX_EXTRACTION_IMAGES`
  (total image parts sent to the vision model per extraction).

### Security & hardening (audit remediation)
- **Auth abuse protection:** dedicated per-endpoint rate limits on `/auth/login`,
  `/auth/verify`, `/auth/resend`, `/auth/register`; only the most recently issued
  OTP stays live (prior unconsumed codes are invalidated); the failed-OTP attempt
  cap is now incremented atomically in SQL (no lost-update bypass under concurrency).
- **Email is sent after the DB commit** (FastAPI background task), so a recipient
  can never receive a code for state that rolled back.
- **Fail-fast config:** `AP_EMAIL_BACKEND=smtp` without `AP_SMTP_HOST` is rejected at
  startup; docker-compose now boots out of the box (development + console email).
- **Bounded inputs:** `max_length` on policy text and `raw_text`; `/tools/extract`
  routes through the shared decoder (size/type caps, 422/503 instead of 500).
- **MCP robustness:** `status` filters are validated against the enum and UUIDs are
  guarded in `list_invoices`/`search_invoices`/`update_invoice_status`/`get_invoice`
  (clean `ToolError` instead of a silent empty result or 500).
- **Analytics correctness:** `spend_analytics` groups by the canonical vendor (joined
  from the vendor master), so OCR name variants no longer split a vendor's spend.

### Changed (production hardening)
- **`AP_API_KEY_PEPPER` and `AP_JWT_SECRET` are now required** (no insecure defaults);
  the app fails fast at startup if they are unset (pepper ≥ 16, JWT secret ≥ 32 chars).
- In production/staging the app **refuses `AP_EMAIL_BACKEND=console`** — a real SMTP
  server must be configured so verification emails are delivered.
- **Removed all hardcoded demo data.** `scripts/seed.py` no longer seeds a sample
  vendor/policy or fixed credentials — it bootstraps a real owner account from
  `--email`/`--password` (or `AP_SEED_*`). `scripts/demo.py` (sample-data walkthrough)
  refuses to run against a production deployment. Vendor/policy/invoice data comes
  only from the database.

### Fixed
- Bad, oversized, too-many, or unsupported file uploads now return **422**, and an
  unavailable LLM provider returns **503**, instead of a generic **500** (the
  extraction error was previously an uncaught `RuntimeError`).

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
