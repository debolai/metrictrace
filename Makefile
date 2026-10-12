# Every operation runs through make so local, CI and AWS use the same commands.
# Targets are added phase by phase; each one is safe to run twice.

UV_RUN := uv run --frozen
# Settings for the committed fixture (own bucket, offline); see tests/fixture.env.
FIXTURE_ENV := $(shell grep -v '^\s*\#' tests/fixture.env | xargs)
# sqlfluff renders the dbt project to lint it; it needs the profile's variables but
# never connects, so placeholder values are enough.
SQLFLUFF_ENV := DBT_TARGET=local DBT_DUCKDB_PATH=:memory: LAKE_BUCKET=lint AWS_REGION=us-east-1 \
	SIM_START_DATE=2018-02-01 SIM_END_DATE=2018-08-29 DBT_S3_KEY_ID=lint \
	DBT_S3_SECRET=lint DBT_S3_ENDPOINT=localhost:9000 DBT_S3_USE_SSL=false

.DEFAULT_GOAL := help
.PHONY: help install hooks lint format check-env test-unit duckdb-extensions up down clean-lake test-lake \
	download-data ingest backfill test-ingestion fixtures \
	fixture-lake dbt-build dbt-build-fixture dbt-docs test-dbt

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
		env $(SQLFLUFF_ENV) $(UV_RUN) sqlfluff lint dbt; \
	else \
		echo "sqlfluff: no SQL under dbt/ yet (added in Phase 3)"; \
	fi

format: ## Apply ruff formatting and safe lint fixes (remaining findings: make lint)
	$(UV_RUN) ruff format .
	$(UV_RUN) ruff check --fix --exit-zero --quiet .
	$(UV_RUN) ruff format --quiet .

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

download-data: ## Fetch the public Olist dataset into OLIST_SOURCE_PATH (no account needed)
	$(UV_RUN) metrictrace download-data

ingest: ## Land one simulated day: make ingest DATE=2017-03-01 [SOURCE=olist|fx|holidays]
	@test -n "$(DATE)" || (echo "usage: make ingest DATE=YYYY-MM-DD" && exit 2)
	$(UV_RUN) metrictrace ingest --date $(DATE) --source $(or $(SOURCE),all)

backfill: ## Land a range of days: make backfill FROM=2017-01-01 TO=2017-01-31
	@test -n "$(FROM)" -a -n "$(TO)" || (echo "usage: make backfill FROM=YYYY-MM-DD TO=YYYY-MM-DD" && exit 2)
	$(UV_RUN) metrictrace ingest --from $(FROM) --to $(TO) --source $(or $(SOURCE),all)

fixtures: ## Rebuild data/fixtures and golden values from the full download (deterministic)
	$(UV_RUN) python -m tests.fixtures.build_fixtures
	$(UV_RUN) python -m tests.oracle.compute_golden
	$(UV_RUN) python -m tests.oracle.compute_golden --source $(or $(OLIST_SOURCE_PATH),data/raw_download) \
		--fx-dir data/api_cache/frankfurter --start 2017-01-01 --as-of 2017-06-30 \
		--out data/fixtures/golden_aws.json

test-ingestion: ## Ingestion unit tests plus the Phase 2 gate against the running stack
	$(UV_RUN) pytest tests/unit/ingestion tests/unit/test_pipeline.py tests/integration/test_ingestion.py

fixture-lake: ## Replay the whole committed fixture into its own bucket (offline)
	env $(FIXTURE_ENV) $(UV_RUN) metrictrace init-lake
	env $(FIXTURE_ENV) $(UV_RUN) metrictrace ingest --window

dbt-build: ## dbt source freshness + build on the lake in .env, then publish artifacts
	$(UV_RUN) metrictrace transform

dbt-build-fixture: fixture-lake ## Phase 3 gate: dbt build on the fixture lake
	env $(FIXTURE_ENV) $(UV_RUN) metrictrace transform

dbt-docs: ## Generate the dbt docs site (dbt/target/index.html)
	$(UV_RUN) metrictrace dbt docs generate

test-dbt: ## dbt integration tests: fixture lake, build, artifacts and mart totals
	$(UV_RUN) pytest tests/integration/test_dbt.py
