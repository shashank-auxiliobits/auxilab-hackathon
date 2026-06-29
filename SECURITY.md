# Security Policy

## Reporting a vulnerability

Please **do not** open a public issue for security vulnerabilities. Instead, use
GitHub's private vulnerability reporting on this repository
(**Security → Report a vulnerability**) at
<https://github.com/AuxiLabs/auxilab-mcp-ap-invoice/security/advisories/new>,
with details and, if possible, a reproduction. We aim to acknowledge reports
within 3 business days.

## Security model

- **Authentication:** users sign up with email + password (verified by an emailed
  one-time code) and log in for a short-lived session **JWT** signed with
  `AP_JWT_SECRET`. Passwords and OTPs are stored only as Argon2 hashes; OTPs
  expire, are single-use, and lock out after repeated failures.
- **API keys** are random secrets; only an Argon2 hash (mixed with a server-side
  `AP_API_KEY_PEPPER`) is stored. The plaintext is shown once at creation. Keys
  can be expired and revoked, and are issued self-service by a logged-in user.
- **Tenant isolation:** every query is scoped by `organization_id`; one org can
  never read another's vendors, invoices, or audit trail.
- **Audit trail:** `processing_events` is append-only — the system of record for
  every AP decision.
- **Rate limiting** is applied per client (`AP_RATE_LIMIT`).

## Operational guidance

- Always set a strong, unique `AP_API_KEY_PEPPER` and `AP_JWT_SECRET` in
  production — the app refuses to start without them (there are no defaults).
- In production, deliver OTPs over real email (`AP_EMAIL_BACKEND=smtp`); the app
  refuses to boot with the `console` backend in production/staging.
- Terminate TLS in front of the API and MCP server.
- Rotating the pepper invalidates all existing API keys — plan a re-issue.
- Restrict CORS (`AP_CORS_ALLOW_ORIGINS`) and place the MCP server behind network
  controls appropriate to your agents.
