# Security Policy

## Reporting a vulnerability

Please **do not** open a public issue for security vulnerabilities. Instead,
email the maintainers at `security@your-org.example` with details and, if
possible, a reproduction. We aim to acknowledge reports within 3 business days.

## Security model

- **API keys** are random secrets; only an Argon2 hash (mixed with a server-side
  `AP_API_KEY_PEPPER`) is stored. The plaintext is shown once at creation. Keys
  can be expired and revoked.
- **Tenant isolation:** every query is scoped by `organization_id`; one org can
  never read another's vendors, invoices, or audit trail.
- **Admin endpoints** (`/admin/*`) are gated by a separate `AP_ADMIN_TOKEN` and
  are disabled entirely if it is unset.
- **Audit trail:** `processing_events` is append-only — the system of record for
  every AP decision.
- **Rate limiting** is applied per client (`AP_RATE_LIMIT`).

## Operational guidance

- Always set a strong, unique `AP_API_KEY_PEPPER` and `AP_ADMIN_TOKEN` in
  production; never ship the development defaults.
- Terminate TLS in front of the API and MCP server.
- Rotating the pepper invalidates all existing API keys — plan a re-issue.
- Restrict CORS (`AP_CORS_ALLOW_ORIGINS`) and place the MCP server behind network
  controls appropriate to your agents.
