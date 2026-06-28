"""Local end-to-end demo against a running API.

Run the API first (``make run-api`` / ``make setup``), then:

    uv run python scripts/demo.py

It provisions a fresh organization and a vendor, then demonstrates that the
**vendor policy (stored in the vector DB) is the single source of truth**:

* no policy on file        → the invoice holds (can't be verified)
* upload a policy          → invoices are judged against the policy text
* update (replace) policy  → the SAME kind of invoice gets a new decision
* exact duplicate          → rejected by the DB guardrail

Uses the configured LLM (Claude/GPT) for extraction + decisions, so it needs a
provider key in .env. Provisions its own org + API key directly in the database.
"""

from __future__ import annotations

import asyncio
import sys
import uuid

import httpx

from ap_invoice.core.config import get_settings
from ap_invoice.core.security import generate_api_key
from ap_invoice.db.session import dispose_engine, session_scope
from ap_invoice.models.organization import ApiKey, Organization

POLICY_V1 = """ACME SUPPLY CO — VENDOR POLICY (v1)
Payment terms are Net 30.
Invoices must not exceed $5,000.
A valid purchase order number is required on every invoice.
All invoices must be issued in USD.
"""
POLICY_V2 = """ACME SUPPLY CO — VENDOR POLICY (v2, updated)
Payment terms are Net 30.
Invoices must not exceed $1,000.
A valid purchase order number is required on every invoice.
All invoices must be issued in USD.
"""

# Used in step 3 (no policy on file). Distinct amount so it isn't a near-duplicate
# of the compliant invoice processed later.
NO_POLICY = (
    "Acme Supply Co\nInvoice Number: INV-NP\nPO Number: PO-7000\nInvoice Date: 2026-06-01\n"
    "Payment Terms: Net 30\nCurrency: USD\nGrand Total: USD 3,300.00"
)
# Compliant under v1 ($2,400 ≤ $5,000, has PO, USD, Net 30, fully itemised).
COMPLIANT = (
    "Acme Supply Co\nInvoice Number: INV-OK\nPO Number: PO-7001\nInvoice Date: 2026-06-01\n"
    "Payment Terms: Net 30\nCurrency: USD\nSubtotal: 2300.00\nTax: 100.00\n"
    "Grand Total: USD 2,400.00"
)
OVER_CAP_V1 = (
    "Acme Supply Co\nInvoice Number: INV-BIG\nPO Number: PO-7009\nInvoice Date: 2026-06-01\n"
    "Payment Terms: Net 30\nCurrency: USD\nGrand Total: USD 9,000.00"
)
# Compliant under v1 but OVER the v2 cap ($1,800 > $1,000).
AFTER_UPDATE = (
    "Acme Supply Co\nInvoice Number: INV-AFTER\nPO Number: PO-7002\nInvoice Date: 2026-06-02\n"
    "Payment Terms: Net 30\nCurrency: USD\nGrand Total: USD 1,800.00"
)
UNKNOWN = (
    "Globex Industries\nInvoice Number: INV-2001\nPO Number: PO-1\nInvoice Date: 2026-06-01\n"
    "Currency: USD\nGrand Total: USD 500.00"
)


async def _provision_org_key() -> str:
    """Create a fresh org + API key directly in the DB; return the plaintext key."""
    async with session_scope() as db:
        org = Organization(name="Demo Co", slug=f"demo-{uuid.uuid4().hex[:8]}")
        db.add(org)
        await db.flush()
        generated = generate_api_key()
        db.add(
            ApiKey(
                organization_id=org.id,
                name="demo",
                prefix=generated.prefix,
                key_hash=generated.key_hash,
            )
        )
        await db.flush()
    await dispose_engine()
    return generated.full_key


def main() -> None:
    settings = get_settings()
    base = f"http://127.0.0.1:{settings.api_port}"
    if settings.is_production:
        sys.exit("scripts/demo.py uses sample data; do not run it against production.")
    if not settings.llm_available:
        sys.exit(f"LLM provider '{settings.llm_provider}' is not configured in .env.")

    with httpx.Client(base_url=base, timeout=120) as c:
        try:
            c.get("/health/ready").raise_for_status()
        except Exception as exc:
            sys.exit(f"API not reachable at {base} ({exc}). Start it with: make run-api")

        print(f"\nAP Invoice Intelligence — policy-as-source-of-truth demo ({base})\n" + "=" * 64)

        # 1. Provision org + key (directly in the DB; no admin endpoint)
        key = asyncio.run(_provision_org_key())
        auth = {"Authorization": f"Bearer {key}"}
        print(f"1. org + API key created  ({key.split('.')[0]}…)")

        # 2. Vendor onboarded — no policy yet
        vid = c.post(
            "/vendors",
            headers=auth,
            json={
                "canonical_name": "Acme Supply Co",
                "aliases": ["ACME", "Acme Supply"],
                "status": "active",
            },
        ).json()["id"]
        print("2. vendor 'Acme Supply Co' onboarded (no policy yet)")

        def process(label: str, text: str, **extra: object) -> str:
            r = c.post("/invoices/process", headers=auth, json={"raw_text": text, **extra}).json()
            reason = f"  ({r['reasons'][0][:80]})" if r.get("reasons") else ""
            print(f"   {label:<34} → {r['decision']:<12} [{r['status']}]{reason}")
            return str(r["decision"])

        def upload_policy(text: str, *, replace: bool) -> None:
            c.post(
                f"/vendors/{vid}/documents",
                headers=auth,
                json={"filename": "policy.txt", "text": text, "compile": False, "replace": replace},
            ).raise_for_status()

        # 3. No policy → hold
        print("3. process an invoice with NO policy on file:")
        process("compliant-looking invoice", NO_POLICY)

        # 4. Upload policy v1 → invoices judged against it
        upload_policy(POLICY_V1, replace=False)
        print("4. uploaded policy v1 (cap $5,000) → judged against policy text:")
        process("compliant $2,400 (PO, USD, Net 30)", COMPLIANT)
        process("$9,000 over the $5,000 cap", OVER_CAP_V1)

        # 5. UPDATE the policy (replace) → behaviour changes dynamically
        upload_policy(POLICY_V2, replace=True)
        print("5. UPDATED policy → v2 (cap lowered to $1,000), same vector store:")
        process("$1,800 — was fine under v1, now over cap", AFTER_UPDATE)

        # 6. Guardrails: duplicate (DB) and unknown vendor (no policy)
        print("6. guardrails:")
        process("exact duplicate of INV-OK", COMPLIANT)
        process("unknown vendor (no policy)", UNKNOWN, auto_onboard=True)

        # 7. RAG policy search reflects the CURRENT (v2) policy
        hits = c.get(
            f"/vendors/{vid}/policy-search", headers=auth, params={"q": "maximum invoice amount"}
        ).json()
        if hits:
            snippet = hits[0]["text"].split("\n")[0]
            print(f"7. policy-search now returns the v2 policy → '{snippet}'")

        print("\nDemo complete. The policy in the vector DB drove every decision.\n")


if __name__ == "__main__":
    main()
