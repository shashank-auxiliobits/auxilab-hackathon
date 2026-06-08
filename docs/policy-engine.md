# Decision Engine

> **Superseded.** The decision is now made by an LLM, not a deterministic rule
> engine. See [`services/llm_decision.py`](../src/ap_invoice/services/llm_decision.py):
> the configured LLM (Claude / GPT / local) reads the vendor policy retrieved
> from the RAG plus the deterministic helper-tool outputs (completeness,
> duplicate detection, payment terms, vendor recognition) and the approved
> compiled rules, and returns approve/flag/hold/reject with a confidence score.
> The sections below describe the inputs it still consumes; they are now
> *evidence for* the LLM rather than a deterministic rule set.

The decision engine turns the outputs of the individual tools into a single,
explainable verdict, grounded in the vendor's retrieved policy.

## Inputs

- a **policy snapshot** (flattened from the vendor's active policy, or defaults),
- the invoice **amount**,
- the **completeness** result,
- the **duplicate** result,
- the **payment-terms** result, and
- whether the **vendor was recognised**.

## Checks and severities

Each check produces a `PolicyCheck` with a severity: `info`, `warning`, or
`critical`.

| Check | Critical when | Warning when |
|-------|---------------|--------------|
| Duplicate | exact duplicate found | near-duplicate found |
| Completeness | action is *Return to Vendor* | below the policy minimum / *Hold* |
| Vendor | — | vendor not recognised |
| Payment terms | — | terms unparseable, or invoice past due |
| Amount | — | above the manual-review threshold, or amount missing |

## Decision rules

The decision is driven by the most severe outcome:

1. **Any critical failure** →
   - exact duplicate → **`reject`** (hard fail — never pay twice),
   - otherwise → **`flag`** (likely invalid).
2. **Any warning** → **`hold`** (a human should review).
3. **All clear** →
   - amount ≤ `auto_approve_max_amount` → **`auto_approve`**,
   - amount > `auto_approve_max_amount` → **`hold`**,
   - no `auto_approve_max_amount` configured → **`hold`** (conservative default —
     nothing auto-approves unless a vendor explicitly opts in).

`requires_review_above_amount` forces a `hold` regardless of the auto-approve
limit, for high-value invoices.

## Decision → invoice status

| Decision | Invoice status |
|----------|----------------|
| `auto_approve` | `approved` |
| `hold` | `held` |
| `flag` | `flagged` |
| `reject` | `rejected` |

## Output

A `PolicyEvaluation` with the `decision`, a `confidence` score, the full list of
`checks`, the human-readable `reasons` for any failures, and a one-line
`summary`. The orchestrator persists this and records it (plus every upstream
check) in the [audit trail](./data-model.md#processing_events-append-only-audit-trail).

## Tuning per vendor

Everything is configured on the **vendor policy** (versioned), so each supplier
can have its own thresholds:

- `auto_approve_max_amount` — raise to auto-approve more, lower to review more.
- `requires_review_above_amount` — hard ceiling for auto-approval.
- `mandatory_fields` / `min_completeness_score` — what "complete" means.
- `amount_tolerance_pct` / `duplicate_lookback_days` — duplicate sensitivity.

Changing a policy creates a new version; invoices processed earlier remain
explainable against the version that was active at the time.
