"""Bootstrap the first account (organization + pre-verified owner) + an API key.

Creates a *real* owner from values you provide — there is no hardcoded demo data
and no sample vendor/policy (those are created through the API like any other
tenant data). Use this once to get a foothold; everything else comes from the DB.

Usage (database running and migrated):

    uv run python scripts/seed.py --email you@example.com [--password ...] [--org "Acme AP"]

Values may also come from the environment: AP_SEED_EMAIL, AP_SEED_PASSWORD,
AP_SEED_ORG. If no password is given, a strong one is generated and printed once.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import secrets

from ap_invoice.core.security import generate_api_key, hash_password
from ap_invoice.db.session import dispose_engine, session_scope
from ap_invoice.models.organization import ApiKey, Organization
from ap_invoice.models.user import User
from ap_invoice.services.accounts import _slugify, get_user_by_email


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Create the first owner account + API key.")
    parser.add_argument("--email", default=os.environ.get("AP_SEED_EMAIL"))
    parser.add_argument("--password", default=os.environ.get("AP_SEED_PASSWORD"))
    parser.add_argument("--org", default=os.environ.get("AP_SEED_ORG"))
    return parser.parse_args()


async def seed(email: str, password: str, org_name: str) -> None:
    async with session_scope() as db:
        if await get_user_by_email(db, email) is not None:
            raise SystemExit(f"An account already exists for {email}.")

        org = Organization(name=org_name, slug=_slugify(org_name))
        db.add(org)
        await db.flush()

        db.add(
            User(
                organization_id=org.id,
                email=email.strip().lower(),
                password_hash=hash_password(password),
                is_email_verified=True,
            )
        )
        generated = generate_api_key()
        db.add(
            ApiKey(
                organization_id=org.id,
                name="bootstrap-key",
                prefix=generated.prefix,
                key_hash=generated.key_hash,
            )
        )
        await db.flush()

    await dispose_engine()

    print("\n=== Account created ===")
    print(f"Organization: {org_name}")
    print("Login (already email-verified):")
    print(f"  email:    {email}")
    print(f"  password: {password}")
    print("\nAPI key for programmatic / MCP use (store it — not recoverable):")
    print(f"  {generated.full_key}\n")
    print("Log in for a session token:")
    print(
        "  curl -s -X POST http://localhost:8000/auth/login \\\n"
        '    -H "content-type: application/json" \\\n'
        f'    -d \'{{"email":"{email}","password":"<password>"}}\'\n'
    )


def main() -> None:
    args = _parse_args()
    if not args.email:
        raise SystemExit("Provide --email (or AP_SEED_EMAIL). No default — bootstrap a real owner.")
    password = args.password or secrets.token_urlsafe(16)
    org_name = (args.org or "").strip() or f"{args.email.split('@')[0]}'s workspace"
    asyncio.run(seed(args.email, password, org_name))


if __name__ == "__main__":
    main()
