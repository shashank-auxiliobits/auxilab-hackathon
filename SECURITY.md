# Security Policy

AP Invoice Intelligence handles financial documents and lets AI agents take
actions against them, so security is a first-class design goal — not a bolt-on.
This document describes the security model, the controls in place, how they map
to recognized standards, and where responsibility sits with the operator.

> **Honesty note.** No software is "100% secure." This page describes the
> controls the codebase implements and the assumptions behind them. Items marked
> **Operator** are your responsibility to configure for a given deployment.

## Reporting a vulnerability

Please **do not** open a public issue for security vulnerabilities. Instead, use
GitHub's private vulnerability reporting on this repository
(**Security → Report a vulnerability**) at
<https://github.com/AuxiLabs/auxilab-mcp-ap-invoice/security/advisories/new>,
with details and, if possible, a reproduction. We aim to acknowledge reports
within 3 business days and to coordinate disclosure.

## Threat model

**Assets:** vendor master data, invoices and line items, per-vendor policies, the
append-only audit trail, API keys, user credentials, and the integrity of the
auto-approval decision.

**Primary actors / threats:**
- A malicious or compromised **tenant user** trying to read or affect another
  tenant's data.
- A malicious **vendor** embedding instructions in a policy document to subvert
  the AI decision (prompt injection) so their invoices auto-approve.
- A malicious **invoice** (text/file) crafted to inject instructions or exhaust
  resources.
- **Credential theft** (API keys, session tokens, passwords).
- **Replay / double-payment** via duplicate invoices.

**Trust boundaries:** the network edge (TLS — operator), the API/MCP
authentication layer, the per-organization tenant boundary, and the boundary
between *trusted system instructions* and *untrusted policy/invoice content* fed
to the LLM.

## Controls

### Authentication & sessions
- Users authenticate with **email + password**; the email is verified with a
  single-use, time-limited **OTP** before login is allowed.
- Passwords and OTPs are stored only as **Argon2** hashes, peppered with a
  server-side secret (`AP_API_KEY_PEPPER`). Plaintext is never stored or logged.
- OTPs **expire** (`AP_OTP_TTL_MINUTES`), are **single-use**, and **lock out**
  after `AP_OTP_MAX_ATTEMPTS` failed guesses; the failed-attempt counter is
  persisted even on the error response so the cap cannot be bypassed by retry.
- Login issues a short-lived **HS256 JWT** signed with `AP_JWT_SECRET`
  (`AP_JWT_EXPIRE_MINUTES`), carrying only the user and organization id.
- **API keys** (for programmatic/MCP clients) are random secrets; only an Argon2
  hash (peppered) is stored, the plaintext is shown once, and keys can be
  **expired and revoked**. Keys are issued self-service by a logged-in user —
  there is **no shared admin token**.
- Account-enumeration is avoided on OTP resend (always `202`).

### Authorization & multi-tenancy
- Every authenticated request resolves to exactly one `Organization`.
- **Every** vendor/invoice/policy/audit query is scoped by `organization_id`;
  one tenant can never read or mutate another's data. Cross-tenant isolation is
  covered by an integration test.

### LLM / prompt-injection safety (defense in depth)
1. **Upload-time screening:** vendor policy text is screened by
   [`policy_guardrails.screen_policy_text`](src/ap_invoice/services/policy_guardrails.py)
   and **rejected with HTTP 422** before it is ever stored or embedded if it
   contains content addressed to the model (role redefinition, "ignore previous
   instructions", control-disabling commands, prompt-leak attempts, role markup).
2. **Decision-time hardening:** the decision prompt wraps policy and invoice in
   `<vendor_policy>` / `<invoice_fields>` delimiters and instructs the model to
   treat them as **untrusted data**, ignore embedded directives, flag tampered
   policies, and **independently verify** compliance rather than obey any text.
3. **Deterministic guardrails outside the model:** exact-duplicate detection and
   "no policy on file → hold" are enforced in code, not by the LLM, so they
   cannot be talked around.

These map to the OWASP LLM Top 10 (see below) and are regression-guarded by tests
(`test_safety.py`, `test_policy_guardrails.py`, and the end-to-end acceptance
test).

### Input validation & resource limits
- All request bodies are validated by strict Pydantic models (`extra="forbid"`).
- Uploaded files are size-capped (`AP_MAX_FILE_BYTES`), count-capped
  (`AP_MAX_FILES_PER_INVOICE`), and the total images sent to the vision model are
  bounded (`AP_MAX_EXTRACTION_IMAGES`). Malformed/oversized/unsupported uploads
  return **422**, not a 500.
- **Rate limiting** is applied per client (`AP_RATE_LIMIT`).

### Secrets & configuration
- `AP_API_KEY_PEPPER` and `AP_JWT_SECRET` are **required with no defaults** — the
  app refuses to start without them, so insecure placeholders cannot ship.
- In production/staging the app refuses the `console` email backend (OTPs must be
  delivered over real SMTP).
- `.env` is git-ignored; no secrets are committed. Secrets should be injected
  from your orchestrator's secret store (**Operator**).

### Integrity & auditability
- `processing_events` is an **append-only** audit trail — the system of record
  for every extraction, vendor match, policy evaluation, decision, and status
  change.
- **Duplicate / double-payment protection:** invoices are fingerprinted
  (vendor + number + amount) and checked for exact and near duplicates; exact
  duplicates are hard-rejected.

## Standards alignment

### OWASP API Security Top 10 (2023)
| Risk | How it is addressed |
|------|---------------------|
| API1 Broken Object Level Authorization | Every object query scoped by `organization_id`; tenant-isolation test |
| API2 Broken Authentication | Argon2-hashed credentials, email verification, signed short-lived JWTs, revocable API keys, required secrets |
| API3 Broken Object Property Level Authorization | Response models are explicit Pydantic schemas; requests are `extra="forbid"` |
| API4 Unrestricted Resource Consumption | Per-client rate limiting; file size/count/image caps; bounded LLM tokens & timeouts |
| API5 Broken Function Level Authorization | Tenant endpoints require a valid token; key-management/auth routes are separated |
| API6 Unrestricted Access to Sensitive Business Flows | Auto-approval is gated by policy + deterministic duplicate/no-policy guardrails |
| API7 SSRF | No user-controlled outbound URL fetching; LLM/SMTP endpoints are server-configured |
| API8 Security Misconfiguration | Required secrets (fail-fast), prod refuses console email, CORS allow-list, structured logs |
| API9 Improper Inventory Management | Versioned OpenAPI at `/docs`, documented config, CHANGELOG |
| API10 Unsafe Consumption of APIs | LLM tool-calls are schema-validated; provider errors degrade to a 503, never silent |

### OWASP Top 10 for LLM Applications
| Risk | How it is addressed |
|------|---------------------|
| LLM01 Prompt Injection | Upload-time screener + decision-time untrusted-data delimiting/hardening + out-of-model deterministic guardrails (defense in depth) |
| LLM02 Sensitive Information Disclosure | System prompt forbids revealing instructions; policy text screened for prompt-leak attempts |
| LLM04 Data/Model Poisoning | Policy text is screened before it is embedded into the vendor's RAG store |
| LLM05 Improper Output Handling | The model only returns a constrained, schema-validated tool result (decision/confidence/reasons); it cannot execute code or free-form actions |
| LLM06 Excessive Agency | The model decides; it does not move money. Status changes and payments are explicit, audited, human-reviewable actions |
| LLM08 Vector/Embedding Weaknesses | Retrieval is by a vendor the caller's organization owns (org-scoped vendor checks gate every policy/vendor route), so one tenant's retrieval cannot reach another's policy chunks |
| LLM10 Unbounded Consumption | Bounded tokens, timeouts, file/image caps, and rate limits |

## Cryptography summary
- **Password / API-key / OTP hashing:** Argon2 (argon2-cffi) with a server-side
  pepper; constant-time verification; automatic rehash on parameter upgrades.
- **Session tokens:** JWT HS256 with a required ≥32-char secret and short expiry.
- **In transit:** TLS is expected to be terminated in front of the API and MCP
  servers (**Operator**).

## Operator hardening checklist
- [ ] Set strong, unique `AP_API_KEY_PEPPER` and `AP_JWT_SECRET` from a secret
      store (the app will not start otherwise).
- [ ] Run with `AP_ENVIRONMENT=production` and `AP_EMAIL_BACKEND=smtp` (+ SMTP
      credentials) so OTPs are delivered.
- [ ] Terminate TLS in front of the API and MCP servers.
- [ ] Restrict CORS (`AP_CORS_ALLOW_ORIGINS`) to known origins.
- [ ] Place the MCP server behind network controls appropriate to your agents.
- [ ] Tune `AP_RATE_LIMIT` and the file/image caps for your traffic.
- [ ] Rotate the pepper only deliberately — it invalidates all existing API keys
      and passwords; plan a re-issue/reset.
- [ ] Keep dependencies patched (`uv lock` is pinned; CI runs on every change).

## Out of scope / residual risk (be honest)
- **TLS, network segmentation, WAF, and secret storage** are the operator's
  responsibility.
- The LLM is **probabilistic**: prompt-injection defenses are defense-in-depth,
  not a proof. Keep human review for flagged/held invoices and high-value
  approvals; the deterministic guardrails (duplicates, no-policy-hold) exist
  precisely because the model can be wrong or manipulated.
- The bundled embedder is a deterministic **local** stub for offline/dev use;
  swap in a vetted hosted embedding provider for production retrieval quality.
- No built-in refresh tokens, MFA beyond email verification, or per-user roles
  within an organization yet — the data model leaves room to add them.
