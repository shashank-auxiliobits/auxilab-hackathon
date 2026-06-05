# Data Model

All tables use UUID primary keys and (except the audit trail) `created_at` /
`updated_at` timestamps maintained by the database.

```
organizations ──1:N── api_keys
      │
      ├──1:N── vendors ──1:N── vendor_policies   (versioned)
      │            │
      │            └──1:N── invoices ──1:N── invoice_line_items
      │                          │
      └──1:N──────────────────── processing_events   (append-only audit)
```

## organizations
The tenant root. Everything is scoped to an organization.

| Column | Type | Notes |
|--------|------|-------|
| `id` | UUID | PK |
| `name` | str(255) | |
| `slug` | str(120) | unique, URL-safe |
| `is_active` | bool | inactive orgs are rejected at auth |

## api_keys
Per-organization keys. Only an Argon2 hash of the secret is stored.

| Column | Type | Notes |
|--------|------|-------|
| `organization_id` | UUID | FK → organizations (cascade) |
| `name` | str(255) | label |
| `prefix` | str(16) | public, unique — used to look up the candidate before hash verification |
| `key_hash` | str(255) | Argon2 hash of `secret + pepper` |
| `last_used_at` / `expires_at` / `revoked_at` | datetime? | lifecycle |

The plaintext key (`ap_<prefix>.<secret>`) is returned **once** at creation.

## vendors
A supplier in the organization's vendor master.

| Column | Type | Notes |
|--------|------|-------|
| `organization_id` | UUID | FK |
| `canonical_name` | str(255) | unique per org |
| `display_name` | str? | |
| `aliases` | JSONB list | alternate spellings used by the normaliser |
| `tax_id`, `email` | str? | |
| `status` | enum | `active` / `onboarding` / `inactive` |

## vendor_policies (versioned)
The rules governing a vendor's invoices. A new version is created on every change
(`is_active` marks the current one); old versions are retained for reproducibility.

| Column | Type | Notes |
|--------|------|-------|
| `vendor_id` | UUID | FK; unique with `version` |
| `version` | int | increments per change |
| `is_active` | bool | exactly one active per vendor |
| `payment_terms` | str(64) | e.g. `2/10 Net 30` |
| `currency` | str(3) | ISO-4217 |
| `mandatory_fields` | JSONB list | required for completeness |
| `min_completeness_score` | numeric | threshold to "process" |
| `auto_approve_max_amount` | numeric? | clean invoices ≤ this auto-approve |
| `requires_review_above_amount` | numeric? | always hold above this |
| `amount_tolerance_pct` | numeric | near-duplicate amount tolerance (default 5%) |
| `duplicate_lookback_days` | int | duplicate search window |
| `allow_early_payment_discount` | bool | |
| `terms_and_conditions` | JSONB | freeform structured T&Cs |
| `effective_from` / `effective_to` | date? | |

## invoices
An invoice moving through the pipeline.

| Column | Type | Notes |
|--------|------|-------|
| `organization_id` | UUID | FK, indexed |
| `vendor_id` | UUID? | set once the vendor is resolved |
| `raw_vendor_name` | str? | as it appeared on the invoice |
| `invoice_number`, `invoice_date`, `due_date` | | extracted header fields |
| `currency`, `subtotal`, `tax`, `grand_total`, `payment_terms` | | |
| `raw_text` | text? | original document text |
| `source` | str? | e.g. `api`, `agent` |
| `idempotency_key` | str? | unique per org — safe re-ingestion |
| `fingerprint` | str? | vendor+number+amount hash, indexed |
| `status` | enum | `received` → `extracted` → … → `approved`/`held`/`flagged`/`rejected`/`paid` |
| `recommended_action` | enum? | the policy decision |
| `completeness_score` | numeric? | |
| `extraction_source` | enum? | `llm` / `deterministic` / `hybrid` / `manual` |
| `extraction_confidence` | JSONB | per-field confidence |
| `extra_metadata` | JSONB | arbitrary |

## invoice_line_items
| Column | Type |
|--------|------|
| `invoice_id` | UUID (FK, cascade) |
| `line_number` | int |
| `description` | text? |
| `quantity`, `unit_price`, `line_total` | numeric? |

## processing_events (append-only audit trail)
One immutable row per pipeline step and decision. Never updated or deleted.

| Column | Type | Notes |
|--------|------|-------|
| `organization_id` | UUID | FK, indexed |
| `invoice_id` | UUID? | FK, indexed |
| `event_type` | enum | `ingested`, `extracted`, `vendor_matched`, `duplicate_check`, `completeness_check`, `payment_terms_calculated`, `policy_evaluated`, `decision`, `status_changed`, `note` |
| `actor` | str | e.g. `agent:claude`, `system`, `user:alice@co` |
| `tool_name` | str? | which tool produced it |
| `decision` | str? | for decision events |
| `message` | text? | human-readable summary |
| `details` | JSONB | full structured result for reproducibility |
| `created_at` | datetime | indexed |
