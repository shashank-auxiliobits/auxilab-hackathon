# REST API Reference

Base URL: `http://localhost:8000`. Interactive OpenAPI docs are served at
`/docs` (Swagger UI) and `/redoc`.

## Authentication

Most endpoints require an **organization API key** sent as a bearer token:

```
Authorization: Bearer ap_<prefix>.<secret>
```

(`X-API-Key: <key>` is also accepted.) Provisioning endpoints under `/admin`
require the **admin token** instead:

```
X-Admin-Token: <AP_ADMIN_TOKEN>
```

Errors use a consistent envelope:

```json
{ "error": { "code": "not_found", "detail": "Vendor ... not found." } }
```

## Health
| Method | Path | Auth | Description |
|--------|------|------|-------------|
| GET | `/health/live` | none | liveness |
| GET | `/health/ready` | none | readiness (checks DB) |

## Admin (admin token)
| Method | Path | Description |
|--------|------|-------------|
| POST | `/admin/organizations` | create an organization |
| GET | `/admin/organizations` | list organizations |
| POST | `/admin/organizations/{org_id}/api-keys` | issue a key (plaintext returned once) |
| GET | `/admin/organizations/{org_id}/api-keys` | list keys (metadata only) |
| DELETE | `/admin/organizations/{org_id}/api-keys/{key_id}` | revoke a key |

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
| POST | `/invoices/ingest` | ingest raw text → extract & store (`?engine=`) |
| POST | `/invoices/process` | **ingest + run the full policy pipeline** |
| POST | `/invoices/{id}/process` | re-run the pipeline on an existing invoice |
| GET | `/invoices` | list (`?status=`, `?vendor_id=`, paginated) |
| GET | `/invoices/{id}` | detail (with line items) |
| GET | `/invoices/{id}/events` | **audit trail** |
| DELETE | `/invoices/{id}` | delete |

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
ADMIN="X-Admin-Token: $AP_ADMIN_TOKEN"

# 1. Create an organization
ORG=$(curl -s -X POST $BASE/admin/organizations -H "$ADMIN" \
  -H 'content-type: application/json' \
  -d '{"name":"Acme Co","slug":"acme-co"}')
ORG_ID=$(echo "$ORG" | python -c "import sys,json;print(json.load(sys.stdin)['id'])")

# 2. Issue an API key (save the plaintext — shown once)
KEY=$(curl -s -X POST $BASE/admin/organizations/$ORG_ID/api-keys -H "$ADMIN" \
  -H 'content-type: application/json' -d '{"name":"default"}' \
  | python -c "import sys,json;print(json.load(sys.stdin)['api_key'])")
AUTH="Authorization: Bearer $KEY"

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
