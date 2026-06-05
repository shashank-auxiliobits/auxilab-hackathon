"""Seed a demo organization, API key, and a sample vendor with a policy.

Usage (with the database running and migrated):

    uv run python scripts/seed.py

Prints the new organization's API key (shown only once) so you can immediately
call the API or MCP server.
"""

from __future__ import annotations

import asyncio
import uuid
from decimal import Decimal

from ap_invoice.core.enums import VendorStatus
from ap_invoice.core.security import generate_api_key
from ap_invoice.db.session import dispose_engine, session_scope
from ap_invoice.models.organization import ApiKey, Organization
from ap_invoice.models.vendor import Vendor, VendorPolicy


async def seed() -> None:
    async with session_scope() as db:
        org = Organization(name="Demo Organization", slug=f"demo-{uuid.uuid4().hex[:8]}")
        db.add(org)
        await db.flush()

        generated = generate_api_key()
        db.add(
            ApiKey(
                organization_id=org.id,
                name="demo-key",
                prefix=generated.prefix,
                key_hash=generated.key_hash,
            )
        )

        vendor = Vendor(
            organization_id=org.id,
            canonical_name="Microsoft Corporation",
            aliases=["MSFT", "Microsoft", "Microsoft Corp"],
            status=VendorStatus.ACTIVE,
        )
        db.add(vendor)
        await db.flush()
        db.add(
            VendorPolicy(
                vendor_id=vendor.id,
                version=1,
                is_active=True,
                payment_terms="2/10 Net 30",
                auto_approve_max_amount=Decimal("5000"),
                requires_review_above_amount=Decimal("10000"),
            )
        )
        await db.flush()

        print("\n=== Seed complete ===")
        print(f"Organization: {org.name}  (slug={org.slug})")
        print(f"Vendor:       {vendor.canonical_name}  (auto-approve <= 5000)")
        print("\nAPI key (store it now — it is not recoverable):")
        print(f"  {generated.full_key}\n")
        print("Try it:")
        print(
            "  curl -s -X POST http://localhost:8000/invoices/process \\\n"
            f'    -H "Authorization: Bearer {generated.full_key}" \\\n'
            '    -H "content-type: application/json" \\\n'
            '    -d \'{"raw_text":"Microsoft\\nInvoice Number: INV-1\\n'
            "Invoice Date: 2026-06-01\\nPayment Terms: 2/10 Net 30\\n"
            "Grand Total: $1,250.00\"}'\n"
        )

    await dispose_engine()


if __name__ == "__main__":
    asyncio.run(seed())
