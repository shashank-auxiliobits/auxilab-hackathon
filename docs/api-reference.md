# REST API Reference

Base URL: `http://localhost:8000`. Interactive OpenAPI docs are served at
`/docs` (Swagger UI) and `/redoc`.

## Authentication

Tenant endpoints accept **either** a session token (JWT, from login) **or** an
**organization API key**, sent as a bearer token:

```
Authorization: Bearer <jwt-or-api-key>
```

(`X-API-Key: <key>` is also accepted for API keys.) Humans get a session token by
registering and verifying their email; programmatic / MCP clients use an API key
that a logged-in user mints at `POST /api-keys`. There is no admin token.

Errors use a consistent envelope:

```json
{ "error": { "code": "not_found", "detail": "Vendor ... not found." } }
```

## Health
| Method | Path | Auth | Description |
|--------|------|------|-------------|
| GET | `/health/live` | none | liveness |
| GET | `/health/ready` | none | readiness (checks DB) |

## Auth (public)
| Method | Path | Description |
|--------|------|-------------|
| POST | `/auth/register` | create an account (org + owner); emails an OTP |
| POST | `/auth/verify` | verify the email with its OTP → returns a session token |
| POST | `/auth/resend` | resend a verification OTP |
| POST | `/auth/login` | email + password → returns a session token |
| GET | `/auth/me` | the authenticated user + organization (session token) |

## API keys (session token)
| Method | Path | Description |
|--------|------|-------------|
| POST | `/api-keys` | issue a key for your org (plaintext returned once) |
| GET | `/api-keys` | list your org's keys (metadata only) |
| DELETE | `/api-keys/{key_id}` | revoke a key |

## Vendors (API key)
| Method | Path | Description |
|--------|------|-------------|
| POST | `/vendors` | create vendor (optional initial policy) |
| GET | `/vendors` | list (paginated; `?q=` name filter) |
| GET | `/vendors/{id}` | get vendor + active policy |
| PATCH | `/vendors/{id}` | update vendor |
| DELETE | `/vendors/{id}` | delete vendor |
| POST | `/vendors/{id}/policies` | create a new policy version |
| GET | `/vendors/{id}/policies` | list policy versions |
| GET | `/vendors/{id}/policies/active` | active policy |

## Invoices (API key)
| Method | Path | Description |
|--------|------|-------------|
| POST | `/invoices` | create from known fields |
| POST | `/invoices/ingest` | ingest text or a file (`file_base64`+`content_type`) → GLM OCR & store |
| POST | `/invoices/process` | **ingest + run the full LLM decision pipeline** |
| POST | `/invoices/{id}/process` | re-run the pipeline on an existing invoice |
| POST | `/invoices/{id}/status` | set status (approve / hold / flag / reject) |
| GET | `/invoices` | list (`?status=`, `?vendor_id=`, paginated) |
| GET | `/invoices/stats` | aggregated counts by status + totals |
| GET | `/invoices/{id}` | detail (with line items) |
| GET | `/invoices/{id}/events` | **audit trail** |
| DELETE | `/invoices/{id}` | delete |

## Policy documents & rules (API key)
RAG policy onboarding — see [Policy Documents, RAG & Autonomy](./policy-rag.md).

| Method | Path | Description |
|--------|------|-------------|
| POST | `/vendors/{id}/documents` | attach a policy doc → embed + compile structured rules |
| GET | `/vendors/{id}/documents` | list policy documents |
| GET | `/vendors/{id}/rules` | list compiled rules (`?status=`) |
| POST | `/vendors/{id}/rules/{rule_id}/approve` | approve a rule (only approved rules are enforced) |
| POST | `/vendors/{id}/rules/{rule_id}/reject` | reject a rule |
| GET | `/vendors/{id}/policy-search?q=` | semantic search over the vendor's policy docs |

## Tools (API key)
Stateless and DB-backed variants of the five tools.

| Method | Path | Description |
|--------|------|-------------|
| POST | `/tools/extract` | Invoice Field Extractor |
| POST | `/tools/payment-terms` | Payment Terms Calculator |
| POST | `/tools/completeness` | Invoice Completeness Checker |
| POST | `/tools/normalise-vendor` | Vendor Name Normaliser (against the org's master) |
| POST | `/tools/detect-duplicates` | Duplicate Invoice Detector (against the org's invoices) |

---

## Walkthrough (curl)

```bash
BASE=http://localhost:8000

# 1. Register an account (creates your organization). The OTP is emailed —
#    with AP_EMAIL_BACKEND=console it is printed to the API server log.
curl -s -X POST $BASE/auth/register -H 'content-type: application/json' \
  -d '{"email":"you@example.com","password":"a-strong-password"}'

# 2. Verify the email with the logged code → returns a session token.
TOKEN=$(curl -s -X POST $BASE/auth/verify -H 'content-type: application/json' \
  -d '{"email":"you@example.com","code":"123456"}' \
  | python -c "import sys,json;print(json.load(sys.stdin)['access_token'])")
AUTH="Authorization: Bearer $TOKEN"

# (optional) Mint an API key for programmatic / MCP clients:
#   curl -s -X POST $BASE/api-keys -H "$AUTH" -H 'content-type: application/json' \
#     -d '{"name":"default"}'   # → {"api_key":"ap_<prefix>.<secret>", ...}

# 3. Onboard a vendor with a policy
curl -s -X POST $BASE/vendors -H "$AUTH" -H 'content-type: application/json' -d '{
  "canonical_name":"Microsoft Corporation",
  "aliases":["MSFT","Microsoft"],
  "status":"active",
  "policy":{"payment_terms":"2/10 Net 30","auto_approve_max_amount":"5000","requires_review_above_amount":"10000"}
}'

# 4. Process an invoice end-to-end
curl -s -X POST $BASE/invoices/process -H "$AUTH" -H 'content-type: application/json' -d '{
  "raw_text":"Microsoft\nInvoice Number: INV-7001\nInvoice Date: 2026-06-01\nPayment Terms: 2/10 Net 30\nGrand Total: $1,250.00",
  "actor":"agent:demo"
}'
# → {"decision":"auto_approve","status":"approved", ...}

# 5. Inspect the audit trail
INV_ID=...   # from the previous response's "invoice_id"
curl -s $BASE/invoices/$INV_ID/events -H "$AUTH"
```
