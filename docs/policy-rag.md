# Policy Documents, RAG & Autonomy

Vendors can attach free-form policy documents (contracts / T&Cs). The system uses
an LLM (with RAG retrieval over the document) to **compile** them into structured,
typed rules — and then **enforces only the structured rules deterministically**.

> **The split that matters:** intelligence at *onboarding* time, deterministic
> enforcement at *decision* time. The raw document is never fed to an LLM when
> deciding an invoice. This keeps decisions reproducible, auditable, and safe from
> prompt injection (invoices are attacker-controlled documents).

## Flow

```
Vendor attaches policy doc (text)
        │  POST /vendors/{id}/documents
        ▼
[chunk + embed]  → policy_chunks (RAG retrieval)
[policy compiler: LLM (or deterministic fallback)] → policy_rules (status: proposed)
        │  human/vendor reviews
        ▼  POST /vendors/{id}/rules/{rule_id}/approve
   approved rules
        │
Invoice arrives → process pipeline → [deterministic engine enforces approved rules]
        ▼
   approve / hold / flag / reject  (audited)
```

## Rule types compiled from a document

| Rule type | Enforcement |
|---|---|
| `max_invoice_amount` | invoice over the cap → **critical** (flag) |
| `line_item_price_cap` | a matching line over its cap → **critical** (flag) |
| `require_field` | required field missing → **warning** (hold) |
| `allowed_payment_terms` | terms not in the allowed set → **warning** (hold) |
| `requires_purchase_order` | no PO referenced → **warning** (hold) |
| `currency` | wrong currency → **warning** (hold) |
| `custom` | not auto-enforceable → **warning** (hold for human review) |

Only **approved** rules are enforced; `proposed`/`rejected` rules are ignored.

## Embeddings & retrieval

- Documents are chunked and embedded; embeddings are stored as JSON float arrays
  and ranked by cosine similarity **in application code** (the candidate set is
  scoped per vendor, so it stays small). No extra infrastructure.
- The default embedder is a **deterministic, offline** hashing embedder (no API
  calls) — fine for local dev/tests and keyword-overlap retrieval. Swap in a
  hosted embedding model + `pgvector` for large-scale semantic retrieval; the
  `retrieve_chunks` / `embed_document` interface is unchanged.
- `GET /vendors/{id}/policy-search?q=...` exposes retrieval for ad-hoc clause lookup.

## Autonomy (touchless processing) — no payment

The goal is to auto-process the clean majority and only surface true exceptions.
The levers, all of which keep decisions deterministic:

- **Auto-onboarding** (`auto_onboard`, default on): an unrecognised vendor is
  auto-created as `onboarding` with a conservative default policy (no auto-approve
  limit), so processing doesn't halt — the invoice still **holds** for review
  until the vendor is trusted, rather than being auto-approved.
- **Confidence gating**: for LLM/hybrid extraction, if any key field's confidence
  is below `AP_MIN_EXTRACTION_CONFIDENCE` (default 0.6), the invoice is held for
  review instead of auto-approved.
- **Status transitions**: an agent (or human) sets the final status —
  `approved` / `held` / `flagged` / `rejected` — via
  `POST /invoices/{id}/status` or the MCP `update_invoice_status` tool. Every
  transition is written to the audit trail with the actor and a note.

> **Payment is intentionally out of scope.** The agent can approve/flag/hold/reject
> and update status; it does not move money. Payment execution can be added later
> behind the same deterministic + audited model.

## Why not let RAG/LLM make the decision?

Because it would undo the system's core value. An LLM judging compliance over the
invoice + retrieved policy is (a) non-reproducible (fails audit), (b) weak at
exact numeric/threshold checks, and (c) a prompt-injection hole — a malicious
invoice could instruct the model to approve itself. Compiling to deterministic
rules gives the flexibility of natural-language policies with the safety of
rule-based enforcement.
