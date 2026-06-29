# Contributing

Thanks for your interest in improving AP Invoice Intelligence! This guide gets
you set up and explains the workflow.

## Development setup

Prerequisites: [`uv`](https://docs.astral.sh/uv/) and Docker.

```bash
git clone https://github.com/AuxiLabs/auxilab-mcp-ap-invoice
cd auxilab-mcp-ap-invoice

make install            # create the venv and install deps (incl. dev)
make db-up              # start PostgreSQL in Docker

cp .env.example .env
python -c "import secrets; print('AP_API_KEY_PEPPER=' + secrets.token_urlsafe(48))" >> .env

make migrate            # apply migrations
make run-api            # http://127.0.0.1:8000/docs
```

## Quality gates

All of these must pass before a PR is merged (CI enforces them):

```bash
make lint        # ruff
make format      # ruff format + autofix
make typecheck   # mypy (strict)
make test        # unit tests (no DB needed)
make test-int    # integration tests (needs `make db-up` + a test DB)
```

The integration tests use a dedicated `ap_invoice_test` database. Create it once:

```bash
docker compose exec postgres createdb -U ap ap_invoice_test
```

## Conventions

- **Type everything.** `mypy` runs in strict mode.
- **Keep the service layer pure.** Tools in `services/` operate on Pydantic
  schemas, not the ORM — that keeps them unit-testable and reusable by REST, MCP,
  and the orchestrator.
- **Decisions stay deterministic.** New policy logic goes in `policy_engine.py`
  and must be reproducible and covered by unit tests.
- **Never touch lazy ORM relationships in async handlers** — query explicitly
  (see `_get_active_policy`) or you'll hit `MissingGreenlet`.
- **Schema changes** require an Alembic migration: `make revision m="..."`, then
  verify `alembic check` reports no drift.
- **Add tests** for new behaviour (unit for logic, integration for endpoints).

## Pull requests

1. Branch off `main`.
2. Make focused changes with tests and docs.
3. Ensure all quality gates pass.
4. Open a PR describing the change and the motivation.

By contributing you agree your contributions are licensed under the project's
[Apache-2.0](./LICENSE) license.
