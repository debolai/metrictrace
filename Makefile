# Every operation runs through make so local, CI and AWS use the same commands.
# Targets are added phase by phase; each one is safe to run twice.

UV_RUN := uv run --frozen

.DEFAULT_GOAL := help
.PHONY: help install hooks lint format check-env test-unit duckdb-extensions up down clean-lake test-lake

help: ## List available targets
	@grep -E '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  %-14s %s\n", $$1, $$2}'

install: ## Install pinned dependencies from uv.lock and the DuckDB extensions
	uv sync --frozen
	$(MAKE) duckdb-extensions

duckdb-extensions: ## Download DuckDB extensions once; run time never downloads
	$(UV_RUN) python -c "from metrictrace.lake import install_extensions; install_extensions()"

hooks: install ## Install the pre-commit hooks
	$(UV_RUN) pre-commit install

lint: ## ruff, mypy --strict and sqlfluff
	$(UV_RUN) ruff check .
	$(UV_RUN) ruff format --check .
	$(UV_RUN) mypy
	@if find dbt -name '*.sql' 2>/dev/null | grep -q .; then \
		$(UV_RUN) sqlfluff lint dbt; \
	else \
		echo "sqlfluff: no SQL under dbt/ yet (added in Phase 3)"; \
	fi

format: ## Apply ruff formatting and safe lint fixes
	$(UV_RUN) ruff check --fix .
	$(UV_RUN) ruff format .

check-env: ## Validate configuration without starting anything
	$(UV_RUN) python -m metrictrace.config

test-unit: ## Unit tests with coverage
	$(UV_RUN) pytest tests/unit --cov --cov-report=term-missing

up: ## Start the local stack and create the lake bucket
	docker compose up -d --wait rustfs
	docker compose run --rm lake-init

down: ## Stop the local stack (lake data is kept)
	docker compose down

clean-lake: ## Stop the stack and DELETE all local lake data
	docker compose down --volumes

test-lake: ## Lake unit tests plus integration tests against the running stack
	$(UV_RUN) pytest tests/unit/test_lake.py tests/integration/test_lake.py
