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
  per-organization authentication.
- Alembic migrations, Docker multi-stage image + Compose stack, GitHub Actions CI
  (lint, type-check, unit + integration tests), and full documentation.
