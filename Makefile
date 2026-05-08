-include .env
export

UV := $(shell command -v uv 2> /dev/null)

.DEFAULT_GOAL := help

.PHONY: help install lint check test coverage docs-strict run run-force stop docs docs-build clean e2e

help: ## Show this help
	@grep -E '^[a-zA-Z0-9_-]+:.*?## .*$$' Makefile | \
		awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-18s\033[0m %s\n", $$1, $$2}'

install: ## Install dependencies (uv sync)
	@$(UV) sync

lint: ## ruff format + ruff check --fix + mypy + pyright
	@$(UV) run ruff format .
	@$(UV) run ruff check --fix .
	@$(UV) run mypy src chap_client/src tests
	@$(UV) run pyright

check: ## Read-only equivalent of `make lint` (used by CI)
	@$(UV) run ruff format --check .
	@$(UV) run ruff check .
	@$(UV) run mypy src chap_client/src tests
	@$(UV) run pyright

test: ## Run pytest
	@$(UV) run pytest -q

coverage: ## Run pytest with branch coverage; fails under 75% (used by CI)
	@$(UV) run pytest --cov=chap_scheduler --cov=chap_client --cov-report=term-missing --cov-fail-under=75

docs-strict: ## Build docs with --strict so broken cross-refs / warnings fail (used by CI)
	@$(UV) run mkdocs build --strict

run: ## Start the stack (docker compose up --build)
	docker compose up --build

run-force: ## Full rebuild: tear down containers + volumes, rebuild without cache, restart
	docker compose down -v
	docker compose build --no-cache
	docker compose up

stop: ## docker compose down
	docker compose down

e2e: ## End-to-end: save block, trigger flow, poll, dump artifact (requires the three stacks running + chap route patched; see docs/local-stack-runbook.md)
	@PREFECT_API_URL="$${PREFECT_API_URL:-http://127.0.0.1:9090/prefect/api}" \
		$(UV) run python scripts/e2e.py

docs: ## Serve mkdocs-material docs locally with live reload
	@$(UV) run mkdocs serve

docs-build: ## Build the static docs site to ./site
	@$(UV) run mkdocs build

clean: ## Remove caches and build artifacts
	@find . -type f -name "*.pyc" -delete
	@find . -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null || true
	@rm -rf .pytest_cache .ruff_cache .mypy_cache .pyright .coverage htmlcov coverage.xml dist build site *.egg-info
