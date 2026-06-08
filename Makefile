.DEFAULT_GOAL := help
SHELL := /bin/bash
UV := uv

.PHONY: help install db-up db-down db-logs migrate revision lint format typecheck test test-cov test-int run-api run-mcp docker-build docker-up docker-down clean

help: ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | sort | \
		awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-14s\033[0m %s\n", $$1, $$2}'

setup: ## One-command local setup + seed + run tests + live demo (first-run "test everything")
	./scripts/setup.sh --all

install: ## Install all dependencies (incl. dev) into a uv-managed venv
	$(UV) sync --extra dev

db-up: ## Start the local Postgres container
	docker compose up -d postgres

db-down: ## Stop the local Postgres container
	docker compose stop postgres

db-logs: ## Tail Postgres logs
	docker compose logs -f postgres

migrate: ## Apply all migrations to head
	$(UV) run alembic upgrade head

seed: ## Seed a demo org, API key, and vendor (prints the API key)
	$(UV) run python scripts/seed.py

demo: ## Run the local end-to-end demo against a running API (needs: make run-api)
	$(UV) run python scripts/demo.py

revision: ## Autogenerate a migration (usage: make revision m="message")
	$(UV) run alembic revision --autogenerate -m "$(m)"

lint: ## Run ruff lint checks
	$(UV) run ruff check src tests

format: ## Auto-format with ruff
	$(UV) run ruff format src tests
	$(UV) run ruff check --fix src tests

typecheck: ## Run mypy in strict mode
	$(UV) run mypy src

test: ## Run unit tests
	$(UV) run pytest -m "not integration"

test-int: ## Run integration tests (requires Postgres running)
	$(UV) run pytest -m integration

test-cov: ## Run the full suite with coverage
	$(UV) run pytest --cov --cov-report=term-missing

run-api: ## Run the REST API locally with autoreload
	$(UV) run uvicorn ap_invoice.api.main:app --reload --host 127.0.0.1 --port 8000

run-mcp: ## Run the MCP server locally
	$(UV) run ap-invoice-mcp

docker-build: ## Build the production image
	docker compose build

docker-up: ## Start the full stack (Postgres + API + MCP)
	docker compose up -d

docker-down: ## Stop the full stack
	docker compose down

clean: ## Remove caches and build artifacts
	rm -rf .pytest_cache .mypy_cache .ruff_cache htmlcov .coverage coverage.xml dist build
	find . -type d -name __pycache__ -prune -exec rm -rf {} +
