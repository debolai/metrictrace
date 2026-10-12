# MetricTrace — Build Specification

Oct 12, 2026 · @Debola

## Overview

MetricTrace is a governed metrics platform for an e-commerce marketplace, with an LLM agent that answers business questions and explains why a metric changed. It runs on a laptop with Docker and on AWS with the same code, switched by one environment variable.

**Business context.** The data is the real Olist Brazilian e-commerce dataset: about 100,000 orders from 2016 to 2018 across 9 tables. It is replayed as daily file drops to mimic a live source system.

### Pain points the project solves

| Pain point | How it shows up at work | How MetricTrace addresses it |
| --- | --- | --- |
| Conflicting metric definitions | Finance, ops and marketing each report a different "revenue" | Every metric is defined once in a semantic layer, with ambiguous terms resolved explicitly |
| Untrusted LLM SQL | Text-to-SQL tools invent joins and metric logic | The agent can only query the semantic layer through structured requests, never raw SQL |
| Slow root-cause analysis | A KPI drops and someone spends a day tracing it | A deterministic investigation engine decomposes the change and checks data health |
| Silent data quality failures | Duplicates, missing drops and schema drift reach dashboards | Contract checks at ingestion, dbt tests, freshness checks and reconciliation tests |
| Late-arriving and corrected data | Historical numbers change without explanation | Append-only raw zone; history rebuilt from every version of each record |
| "Data issue or real change?" | Teams cannot tell a pipeline fault from a business shift | The agent reports both data-health evidence and business drivers, and says which explains the change |

### Outcomes

1. A data lake with raw, staging and marts zones, fed by file and API ingestion.
2. A tested dbt project with staging, intermediate and mart layers.
3. A semantic layer defining every business metric once.
4. A LangGraph agent with two modes: **Ask** (answer metric questions) and **Investigate** (explain a change).
5. An incident simulator that injects known faults, used to evaluate the agent.
6. One Helm chart that deploys to a local kind cluster and to Amazon EKS, with CI/CD in GitHub Actions.

### Non-goals

- No write actions: the agent and API are read-only.
- No real-time streaming; data arrives in daily batches.
- No multi-tenant auth; one bearer token protects the API.
- The dataset is CC BY-NC-SA 4.0, so the project stays non-commercial.

## Architecture and key decisions

The pipeline uses one SQL dialect (DuckDB) everywhere, so what CI tests is exactly what runs on AWS. Object storage is MinIO locally and S3 on AWS, accessed through the same S3 API.

### Component flow

&#91;embedded content: MetricTrace architecture · sources to UI\]

Data flows top to bottom; the agent never touches the lake's raw data or writes SQL, and every answer is logged to `_audit/`.

1. **Ingest:** Python jobs land Olist file drops and API data (FX rates, public holidays) in the raw zone as Parquet.
2. **Transform:** dbt (dbt-duckdb) reads raw Parquet, runs tests, and writes marts back to the lake as Parquet.
3. **Publish:** dbt artifacts (manifest, run results) are saved to the lake for the agent. On AWS, a Glue crawler registers the marts.
4. **Serve:** Cube exposes the semantic layer over the marts. Locally it queries DuckDB over MinIO; on AWS it queries Athena over Glue tables.
5. **Reason:** the LangGraph agent calls typed tools: Cube queries, the investigation engine, dbt artifacts and freshness checks.
6. **Deliver:** FastAPI serves the agent; Streamlit provides the chat UI.

### Technology decisions

| Concern | Local | AWS |
| --- | --- | --- |
| Object storage | MinIO | S3 (versioned, encrypted) |
| Lake layout | Parquet, Hive-style date partitions | Same |
| Transform engine | dbt-duckdb | dbt-duckdb in a Kubernetes Job |
| Catalog | None needed | Glue Data Catalog via crawler |
| Semantic layer | Cube, DuckDB driver over MinIO | Cube, Athena driver |
| Orchestration | `make pipeline` and Docker Compose | Kubernetes CronJob |
| Agent | LangGraph; Anthropic or OpenAI via LangChain | Same |
| Agent state | In-memory checkpointer; audit log in the lake | Same, in S3 |
| API and UI | FastAPI, Streamlit | Same, behind LoadBalancer Services |
| Containers | Docker Compose, then kind | EKS (managed node group) |
| Infrastructure as code | None | Terraform with an S3 state backend |
| Secrets | `.env` file | Secrets Manager synced to a Kubernetes Secret; IRSA for AWS access |
| CI/CD | GitHub Actions | GitHub Actions with OIDC to AWS |

The reasoning behind each choice is in **Design decisions and reasoning**, below.

**Late-arriving data.** The raw zone is append-only, partitioned by ingestion date. dbt rebuilds history from all record versions using window functions, instead of dbt snapshots, because snapshots would need a persistent database the Kubernetes Job does not keep.

**Scale.** Marts are full-refresh tables. The dataset is small enough that a full rebuild takes under a few minutes; incremental models are a stretch goal.

## Design decisions and reasoning

Every choice favours fewer moving parts and identical behaviour on a laptop and on AWS. The last column says what would change the answer, so the decision can be revisited deliberately rather than by drift.

### Data platform

| Decision | Alternatives considered | Why this choice | Trade-off accepted | Revisit when |
| --- | --- | --- | --- | --- |
| DuckDB as the only transform engine (dbt-duckdb) | Snowflake or BigQuery; Athena via dbt-athena; Spark | One SQL dialect from laptop to AWS, so CI tests exactly the production SQL. No warehouse bill; runs in any container | Single-node compute; one writer at a time | Data grows past what one node handles, or many teams write models concurrently |
| Plain Parquet lake, no table format | Apache Iceberg; Delta Lake | DuckDB, Athena, Glue and pandas all read it with no extra catalog service. Full rebuilds make merge transactions unnecessary | No table-level time travel or schema evolution | Incremental models or concurrent writers are added |
| Append-only raw zone; history rebuilt in SQL | dbt snapshots; a CDC tool | Raw is the source of truth and every mart can be rebuilt from it. Works with short-lived Kubernetes Jobs that keep no database | Each run reads every record version | Rebuild time becomes a problem |
| Full-refresh marts | Incremental models | Simplest correct option at about 100,000 orders; no merge-logic bugs | Build time grows with data | A full build takes more than about 15 minutes |
| Contracts at ingestion, with quarantine | Validation only in dbt | Bad rows stop before they reach models, and the failure reason is stored beside the data | Two validation layers to maintain | Not expected |
| Replay of a real dataset | Synthetic data generator; a live API | Real distributions and real quirks, plus realistic late-arriving records | History is fixed to 2016–2018 | A suitable live public source appears |

### Semantic layer and agent

| Decision | Alternatives considered | Why this choice | Trade-off accepted | Revisit when |
| --- | --- | --- | --- | --- |
| Cube as the semantic layer | dbt Semantic Layer (MetricFlow); LookML; a home-built YAML-to-SQL layer | Open source; generates correct SQL for both DuckDB and Athena; its metadata and query APIs suit agents | A second modelling language beside dbt, and one more service | dbt Cloud is available, or MetricFlow supports both engines |
| Agent queries the semantic layer, never SQL | Text-to-SQL over the marts | The model cannot reinvent metric logic; every query is small, validated JSON | Questions outside the catalog cannot be answered | Users need open exploration beyond defined metrics |
| Deterministic investigation engine | Let the LLM reason over raw query results | Root-cause numbers are reproducible and unit-tested; the LLM only narrates | Analysis methods are fixed (contribution share, shift-share) | New analysis types are needed; add them as engine functions |
| Post-generation grounding check | Rely on prompt instructions alone | Prompts reduce invented numbers; only a check prevents them reaching users | Occasional rewrites add latency | Not expected |
| LangGraph | A plain LangChain agent; a custom loop | Explicit nodes, bounded retries, pauses for clarification and checkpointing | Framework learning curve | Not expected |
| Provider-agnostic LLM settings | A single vendor SDK | Switch Anthropic or OpenAI by environment variable; a fake provider for tests | Provider-specific features are not used | One provider's feature becomes essential |
| In-memory checkpointer plus S3 audit log | Postgres checkpointer on RDS | No database to run or pay for; the audit trail is still durable | Open conversations are lost on a pod restart | The API runs several replicas or needs long sessions |

### Infrastructure and delivery

| Decision | Alternatives considered | Why this choice | Trade-off accepted | Revisit when |
| --- | --- | --- | --- | --- |
| Kubernetes CronJob for orchestration | Airflow; Dagster; Step Functions | One linear daily pipeline does not justify a scheduler platform | No run history UI; basic retry and backfill tooling | Pipelines branch or multiply (Dagster is the stretch goal) |
| One Helm chart for kind and EKS | Separate manifests per environment | The same deployment artifact is tested locally, in CI and in production | Helm templating adds complexity | Not expected |
| EKS rather than ECS Fargate | ECS; App Runner | The brief requires Kubernetes, and the skills carry across clouds | Fixed control-plane cost (about 70 USD per month) | Kubernetes is no longer a requirement |
| Public subnets, no NAT gateway by default | Private subnets with a NAT gateway | Avoids the NAT gateway's fixed hourly cost for a demo | Nodes have public IPs, limited by security groups | Any use beyond a demo: set `enable_nat=true` |
| IRSA and GitHub OIDC | Static access keys | No long-lived AWS credentials in pods or in GitHub | More IAM setup | Not expected |
| Terraform applied from the laptop; app deployed by CD | Terraform applied in CI | Infrastructure changes are rare and deliberate; CI needs fewer permissions | A manual step | A team shares the environment |
| Glue crawler registers marts | Glue tables defined in Terraform | No schema duplicated outside dbt | The crawler may infer types differently from dbt | The AWS parity test finds a type mismatch; then define tables explicitly |

### Testing

| Decision | Alternatives considered | Why this choice | Trade-off accepted | Revisit when |
| --- | --- | --- | --- | --- |
| Independent pandas oracle for golden values | Snapshot the pipeline's own output | A bug cannot pass by agreeing with itself | Oracle code to maintain | Not expected |
| Committed fixture; no network in CI | Download from Kaggle in CI | Fast, repeatable and needs no credentials | The fixture may miss rare cases | An incident reveals a gap; add rows that reproduce it |
| Fake LLM on pull requests; real LLM nightly | Real LLM on every pull request | Pull-request checks are deterministic and free; quality is still tracked daily | A prompt regression can surface up to a day late | Eval cost drops enough to run on every merge |

## Repository layout and conventions

One monorepo holds all code, models and infrastructure. Every operation runs through a `make` target, so local, CI and AWS use the same commands.

```
metrictrace/
├── src/metrictrace/
│   ├── config.py            # pydantic-settings; APP_ENV switches local/aws
│   ├── lake.py              # LakeClient (fsspec/s3fs) + DuckDB connection factory
│   ├── ingestion/           # olist_drops.py, fx_rates.py, holidays.py, contracts.py
│   ├── pipeline.py          # CLI: ingest -> dbt build -> publish artifacts
│   ├── semantic/            # Cube client, metric catalog loader
│   ├── investigation/       # decomposition, data-health checks, ranking
│   ├── agent/               # LangGraph graph, tools, prompts, grounding check
│   ├── incidents/           # fault-injection scenarios
│   ├── audit.py             # audit log writer
│   └── api/                 # FastAPI app
├── ui/                      # Streamlit app
├── dbt/                     # dbt project (models, tests, macros, profiles.yml)
├── cube/model/              # Cube YAML data models
├── data/fixtures/           # small deterministic sample + golden values
├── tests/                   # unit/, integration/, e2e/, evals/
├── deploy/
│   ├── docker/              # Dockerfiles: app, pipeline, ui
│   ├── helm/metrictrace/    # chart + values-local.yaml + values-aws.yaml
│   └── kind/                # kind cluster config, MinIO manifest
├── infra/terraform/         # bootstrap/ (state bucket), envs/aws/, modules/
├── .github/workflows/       # ci.yml, cd.yml, nightly-evals.yml
├── docker-compose.yml
├── Makefile
├── .env.example
└── pyproject.toml + uv.lock
```

### Conventions

- **Python 3.12**, managed with `uv`. All dependencies pinned in `uv.lock`.
- **Typing:** full type hints, `mypy --strict` on `src/`. Pydantic models for every tool input and output.
- **Style:** `ruff` for lint and format; `sqlfluff` with the DuckDB dialect and dbt templater for SQL.
- **No hidden I/O:** all paths, buckets and endpoints come from `config.py`, never from literals in code.
- **Logging:** structured JSON logs with a `run_id` on every line.
- **Commits:** Conventional Commits; branch protection on `main` requires CI to pass.

## Configuration, credentials and environments

One variable, `APP_ENV=local|aws`, selects the environment. The engineer only fills in `.env` (local) or Terraform variables and GitHub secrets (AWS); no code changes are needed.

### Environment variables (`.env.example`)

| Variable | Required | Local value | AWS value | Notes |
| --- | --- | --- | --- | --- |
| `APP_ENV` | Yes | `local` | `aws` | Switches lake, Cube driver and secrets source |
| `LAKE_BUCKET` | Yes | `metrictrace-lake` | Terraform output | One bucket; zones are prefixes |
| `LAKE_ENDPOINT_URL` | Local only | `http://minio:9000` | Empty | Empty means real AWS S3 |
| `MINIO_ROOT_USER`, `MINIO_ROOT_PASSWORD` | Local only | Engineer sets | Not used | Also used as S3 keys for MinIO |
| `AWS_REGION` | AWS | Not used | e.g. `eu-west-1` |  |
| `AWS_PROFILE` | AWS, laptop only | Not used | Engineer's profile | Pods use IRSA instead |
| `KAGGLE_USERNAME`, `KAGGLE_KEY` | Optional | Engineer sets | Not used | Or download the zip manually into `data/raw_download/` |
| `LLM_PROVIDER` | Yes | `anthropic` or `openai` | Same | fake is allowed in tests only and rejected when APP\_ENV=aws |
| `LLM_MODEL` | Yes | Engineer sets | Same | Never hardcoded |
| `ANTHROPIC_API_KEY` / `OPENAI_API_KEY` | One required | Engineer sets | Secrets Manager |  |
| `CUBE_API_SECRET` | Yes | Random string | Secrets Manager | Signs Cube JWTs |
| `API_BEARER_TOKEN` | Yes | Random string | Secrets Manager | Protects FastAPI |
| `LANGSMITH_API_KEY`, `LANGSMITH_PROJECT` | Optional | Engineer sets | Secrets Manager | Tracing switches off when empty |
| `SIM_START_DATE` | Yes | `2017-01-01` | Same | First day of the replay |

### Rules for the code

- `config.py` validates all variables at startup and fails with a clear message naming any missing one.
- `make check-env` runs that validation without starting anything.
- Secrets never appear in logs, images, Helm values or Terraform state outputs.
- On AWS, pods get S3, Glue and Athena access through IRSA (IAM Roles for Service Accounts), never static keys.
- Secrets Manager holds the API keys. `make aws-sync-secrets` copies them into a Kubernetes Secret. External Secrets Operator is a stretch goal.

### GitHub configuration (AWS deploys only)

| Name | Type | Purpose |
| --- | --- | --- |
| `AWS_DEPLOY_ROLE_ARN` | Secret | Role assumed through GitHub OIDC; created by Terraform |
| `AWS_REGION` | Variable | Deploy region |
| `ECR_REGISTRY` | Variable | Terraform output |
| `EKS_CLUSTER_NAME` | Variable | Terraform output |
| `ANTHROPIC_API_KEY` or `OPENAI_API_KEY` | Secret | Nightly live evals only |

## Phases 1–2: Data aggregation and data lake

Ingestion turns the static Olist download into realistic daily drops, adds two public APIs, validates everything against contracts and lands it append-only in the lake.

### Sources

| Source | Access | Tables or endpoint | Arrival pattern |
| --- | --- | --- | --- |
| Olist (Kaggle `olistbr/brazilian-ecommerce`) | Kaggle API or manual zip | orders, order\_items, order\_payments, order\_reviews, customers, sellers, products, product\_category\_name\_translation, geolocation | Replayed daily |
| Frankfurter (ECB rates) | Public, no key | `GET /{start}..{end}?from=BRL&to=USD,EUR` | Daily; no weekend rates |
| Nager.Date | Public, no key | `GET /api/v3/PublicHolidays/{year}/BR` | Once per year |

### Replay rules (`ingestion/olist_drops.py`)

The replay creates the late-arriving and corrected records analytics engineers deal with in real life.

1. On a simulated day D, emit orders purchased on D with status `approved` and empty delivery dates, plus their items and payments.
2. On the order's delivered date, emit a **new version** of the order with status `delivered` and delivery timestamps filled.
3. Cancelled and unavailable orders get a new version on their last known timestamp.
4. Reviews are emitted on `review_creation_date`, so they arrive days after the order.
5. Customers, sellers and products are emitted the first time an order references them.
6. Geolocation is loaded once as reference data, deduplicated to one row per zip-code prefix.
7. Every emitted row gets `_ingested_at`, `_source_file` and `_record_hash` columns.

### Lake layout

| Prefix | Contents |
| --- | --- |
| `raw/olist/<table>/ingest_date=YYYY-MM-DD/` | Append-only Parquet drops |
| `raw/fx/ingest_date=YYYY-MM-DD/` | FX rates as returned |
| `raw/holidays/year=YYYY/` | Holiday list |
| `quarantine/<source>/ingest_date=YYYY-MM-DD/` | Rows that failed contracts, with the reason |
| `marts/<model>/` | dbt output Parquet |
| `_meta/ingestion/` | One JSON record per drop: row counts, checksum, contract result |
| `_meta/dbt/run_id=<id>/` | `manifest.json`, `run_results.json`, `sources.json` |
| `_audit/agent/date=YYYY-MM-DD/` | One JSONL line per agent answer |

### Ingestion requirements

- **Contracts:** a pandera schema per raw table checks columns, types, nullability, allowed values and keys. Failing rows go to `quarantine/`. A drop fails entirely when more than 5% of rows fail.
- **Idempotency:** re-running a date overwrites only that date's partition and updates its `_meta` record.
- **API resilience:** retries with exponential backoff (tenacity), a 10-second timeout and cached responses. CI uses recorded responses, never the network.
- **CLI:** `metrictrace ingest --date 2017-03-01`, `--from/--to` for backfills, `--source olist|fx|holidays|all`.
- **Local infrastructure:** Docker Compose runs MinIO plus a `minio-init` container that creates the bucket.

### Done when

- [ ] `make up && make backfill FROM=2017-01-01 TO=2017-01-31` lands 31 daily partitions per Olist table.
- [ ] Re-running a date produces identical checksums.
- [ ] A corrupted test file lands in `quarantine/` and the `_meta` record shows the failure.
- [ ] Unit and integration tests pass with no network access.

## Phase 3: dbt models

dbt-duckdb reads raw Parquet straight from the lake and writes marts back as Parquet, with tests at every layer. One profile has two targets, `local` and `aws`, which differ only in S3 settings.

### Profile and sources

- `profiles.yml` lives in `dbt/` and reads every value with `env_var()`. The `local` target sets the MinIO endpoint, path-style URLs and no SSL; the `aws` target uses the AWS credential chain so IRSA works.
- Sources are declared as external Parquet globs with Hive partitioning, e.g. `raw/olist/orders/*/*.parquet`.
- Source freshness uses `_ingested_at`: warn after 1 day, error after 2 days, measured against wall-clock time, since ingestion stamps each row with the real run time. Lateness in simulated business time is tracked separately in mart\_data\_health\_daily.

### Models

| Layer | Model | Grain | Key logic |
| --- | --- | --- | --- |
| Staging (views) | `stg_olist__order_versions` | Order version | Rename, cast types, keep `_ingested_at` |
|  | `stg_olist__order_items`, `stg_olist__payments` | Item, payment line | Cast, rename |
|  | `stg_olist__reviews` | Review | Deduplicate repeated `review_id` values, keeping the latest |
|  | `stg_olist__customers`, `__sellers`, `__products` | Entity | Translate product categories to English; default `unknown` |
|  | `stg_fx__rates`, `stg_holidays` | Day | Cast and pivot |
| Intermediate (views) | `int_orders_current` | Order | Latest version per `order_id` by `_ingested_at` |
|  | `int_order_status_history` | Order status period | `valid_from` / `valid_to` built with window functions |
|  | `int_fx_daily_filled` | Day | Forward-fill weekend and holiday gaps |
|  | `int_order_items_enriched` | Item | Join product, seller, FX |
| Marts (external Parquet) | `fct_orders` | Order | Status, totals in BRL and USD, delivery days, `is_late`, `is_cancelled`, `is_first_order` |
|  | `fct_order_items`, `fct_payments`, `fct_reviews` | Item, payment, review | Joined keys and measures |
|  | `dim_customers` | `customer_unique_id` | First order date, state, city |
|  | `dim_sellers`, `dim_products` | Entity | Region, category |
|  | `dim_date` | Day | Weekday, month, holiday flag and name |
|  | `mart_data_health_daily` | Source × ingest date | Row counts, quarantine counts, late-arrival counts; feeds the investigation engine |

### Real data quirks to handle and document

- Some `review_id` values repeat across orders.
- A few orders have no payment rows; payment totals can differ from item price plus freight because of vouchers.
- Some product categories have no English translation.
- Geolocation has many rows per zip prefix.

### Tests

- **Generic:** `unique`, `not_null`, `relationships` and `accepted_values` on every key and status column.
- **Unit tests (dbt 1.8+):** latest-version selection, FX forward-fill, `is_late` logic and status history boundaries, each with hand-written input rows.
- **Reconciliation (singular tests):** mart item totals equal raw item totals for current orders; payment totals match item totals within a documented tolerance.
- **Schema checks:** each mart's columns and types are declared in YAML and checked by a test, so a renamed column fails the build.
- **Exposures:** the Cube semantic layer and the agent are declared as dbt exposures, so lineage reaches the consumer.

### Done when

- [ ] `make dbt-build` passes on the fixture data and on a full backfill.
- [ ] `make dbt-docs` generates the docs site with lineage to the exposures.
- [ ] Marts appear under `marts/` and `run_results.json` under `_meta/dbt/`.

## Phase 4: Semantic layer (Cube)

Cube defines every metric once over the dbt marts and is the agent's only route to data. The agent reads the catalog from `/v1/meta` and runs structured JSON queries through `/v1/load`; it never writes SQL.

### Environment-neutral model files

- Cube YAML models in `cube/model/` reference tables through a Jinja function, e.g. `sql: "{{ source('fct_orders') }}"`, defined in `cube/model/globals.py`.
- When `APP_ENV=local`, the function returns `SELECT * FROM read_parquet('s3://<bucket>/marts/fct_orders/*.parquet')` and Cube's DuckDB driver reads MinIO through its S3 settings.
- When `APP_ENV=aws`, it returns `SELECT * FROM <glue_database>.fct_orders` and the Athena driver runs the query.
- Measure and dimension SQL stays plain (column names, `CASE`, arithmetic) so Cube can generate correct SQL for both engines.
- **Verify first:** before coding, confirm the Jinja `globals.py` mechanism and the DuckDB driver's S3 environment variable names in the docs for the pinned Cube version.

### Metric catalog

| Metric | Definition | Type | Unit |
| --- | --- | --- | --- |
| `gmv` | Sum of item price for non-cancelled orders | Sum | BRL (also USD) |
| `revenue_incl_freight` | Item price plus freight, non-cancelled | Sum | BRL (also USD) |
| `payments_collected` | Sum of payment value | Sum | BRL |
| `orders` | Count of distinct orders | Count | Orders |
| `aov` | `gmv / orders` | Ratio | BRL |
| `cancellation_rate` | Cancelled orders / all orders | Ratio | % |
| `on_time_delivery_rate` | Delivered on or before the estimate / delivered orders | Ratio | % |
| `avg_delivery_days` | Mean days from purchase to customer delivery | Average | Days |
| `avg_review_score` | Mean review score | Average | 1–5 |
| `negative_review_rate` | Reviews scored 1–2 / all reviews | Ratio | % |
| `active_sellers` | Distinct sellers with at least one order | Count | Sellers |
| `repeat_customer_rate` | Customers with 2+ orders / customers | Ratio | % |

**Dimensions:** purchase date (day, week, month), customer state, seller state, product category, payment type, order status and holiday flag. All metrics use purchase date as their time dimension, and this is stated in each metric's description.

### Governance metadata

Each measure carries Cube `meta` fields the agent reads: `description`, `unit`, `synonyms`, `higher_is_better`, `owner` and `decomposable_by` (the dimensions the investigation engine may split it by). Agents query **views** only, never raw cubes, so internal joins stay hidden.

**Ambiguous terms.** `cube/ambiguous_terms.yaml` maps words like "revenue" or "sales" to candidate metrics. The agent either asks the user to choose or uses the listed default (`gmv`) and says so in the answer.

### Done when

- [ ] Every metric in the catalog returns the golden value on the fixture data (see Testing).
- [ ] `/v1/meta` lists every metric with complete `meta` fields; a test fails if any field is missing.
- [ ] The same Cube query returns matching results from the local stack and from AWS (post-deploy smoke test).

## Phases 5–6: Investigation engine and LangGraph agent

All numbers come from deterministic Python and Cube; the LLM only classifies intent, maps words to metrics, fills structured query parameters and writes the narrative. A grounding check rejects any answer containing a number no tool returned.

### Investigation engine (`investigation/`, no LLM)

Input: a metric, a current period, a comparison period and optional filters. Output: a typed `Evidence` object.

1. **Measure the change:** query both periods through Cube; compute absolute and percentage change.
2. **Test significance:** compare the change with the previous 8 periods of the same length. A z-score below 2 is labelled "within normal range".
3. **Decompose:** for each dimension in the metric's `decomposable_by`, compute each segment's share of the change. Sums and counts use contribution share. Ratios use shift-share, splitting the change into mix, rate and interaction effects (formula below). Rank dimensions by how concentrated the change is and keep the top 3 segments.
4. **Check data health for the current period:** source freshness, failed or warning dbt tests on models upstream of the metric (from `manifest.json` lineage), row-count anomalies and quarantine counts in `mart_data_health_daily`, the share of late-arriving records, and FX gaps.
5. **Classify:** `data_issue`, `business_change`, `mixed` or `no_significant_change`, using written rules. For example, a failed upstream test or missing drop in the period makes it `data_issue` or `mixed`.

Shift-share for a ratio metric R = Σ wᵢ rᵢ, where w is a segment's weight and r its rate, period a to b:

```latex
\Delta R = \underbrace{\sum_i (w_{b,i}-w_{a,i})\,r_{a,i}}_{\text{mix}} + \underbrace{\sum_i w_{a,i}\,(r_{b,i}-r_{a,i})}_{\text{rate}} + \underbrace{\sum_i (w_{b,i}-w_{a,i})(r_{b,i}-r_{a,i})}_{\text{interaction}}
```

### Agent tools (Pydantic input and output for each)

| Tool | Purpose |
| --- | --- |
| `list_metrics` | Catalog from Cube `/v1/meta` |
| `describe_metric` | Definition, unit, owner, allowed dimensions |
| `query_metrics` | Runs a validated `CubeQuery` and returns rows plus the generated SQL for audit |
| `investigate_change` | Calls the investigation engine; returns `Evidence` |
| `get_data_health` | Freshness, test results and anomalies for a date range |
| `get_lineage` | Upstream dbt models and sources for a metric |

### Graph nodes

- `classify_intent` routes to ask, investigate, catalog (definitions) or out-of-scope.
- `resolve_metrics` maps the user's words to catalog metrics using synonyms. Ambiguous terms go to `clarify`, which pauses the graph with a question, unless a default is configured.
- `plan_query` (ask mode) has the LLM produce a `CubeQuery`. `validate_query` checks deterministically that every member exists in a view, the date range is within the data, and the row limit is 500 or less. Invalid queries return to `plan_query` with the error, at most 2 times.
- `run_investigation` (investigate mode) validates the periods and calls the engine.
- `compose_answer` writes the answer from tool results only, stating the metric definition, period and data freshness.
- `grounding_check` extracts every number, percentage and date from the draft and matches it against tool results within rounding tolerance. Numbers from the user's question, the requested periods and response metadata are allowed. Failures go back to `compose_answer` with the offending numbers, at most 2 times. After that, the agent returns the result table with no narrative.
- `finalize` attaches metadata (dbt run id, data as-of date, Cube queries, prompt version) and writes the audit record.

### Agent requirements

- Prompts live in versioned files under `agent/prompts/`; the version is logged with every answer.
- LLM calls have a 60-second timeout and one retry; structured-output parse failures retry once with the parse error.
- The agent has no write tools. Underlying cubes are marked non-public, so the agent's Cube token can query views only.
- LangSmith tracing turns on only when its key is set.

### Done when

- [ ] Unit tests cover each engine step with hand-computed expected values, including the shift-share split.
- [ ] Graph routing tests pass with a fake LLM.
- [ ] A draft containing an invented number is always caught by `grounding_check`.

## Phase 7: API, UI and incident simulator

FastAPI wraps the agent and the deterministic engine; Streamlit is a thin client that calls only the API.

### API endpoints

| Method and path | Auth | Purpose |
| --- | --- | --- |
| `GET /health` | None | Process is up (liveness probe) |
| `GET /ready` | None | Lake, Cube and LLM settings reachable (readiness probe) |
| `GET /metrics/catalog` | Bearer | Metric catalog with definitions |
| `POST /ask` | Bearer | Body: `question`, optional `session_id`. Returns answer, result rows, metadata |
| `POST /investigate` | Bearer | Body: `metric`, `current_period`, `comparison_period`, optional `filters`. Returns `Evidence` and narrative |
| `POST /clarify` | Bearer | Resumes a paused graph with the user's choice |
| `GET /audit/{answer_id}` | Bearer | Full audit record for one answer |

**Response metadata on every answer:** `answer_id`, `data_as_of`, `dbt_run_id`, `metric_definitions`, `cube_queries`, `prompt_version`, `grounding_status`.

### UI (Streamlit)

- **Ask tab:** chat box, answer, result table, and an expander with metric definitions and the Cube query used.
- **Investigate tab:** pick a metric and two periods; shows the verdict, a waterfall of segment contributions, and a data-health checklist.
- **Header badge:** data freshness, turning amber when sources are stale.
- **Incidents panel (local only):** buttons to inject and clear simulator scenarios for demos.

### Incident simulator (`incidents/`)

Each scenario changes one day's raw drop in a known way and stores the expected verdict. They power demos and the agent evaluation suite.

| Scenario | Injected fault | Expected verdict |
| --- | --- | --- |
| `duplicate_payments` | Payment rows duplicated for one day | `data_issue` (reconciliation test fails) |
| `missing_drop` | One day's orders drop skipped | `data_issue` (freshness, row-count anomaly) |
| `category_drift` | A product category renamed upstream | `data_issue` (accepted-values test fails) |
| `fx_outage` | FX API returns no data for a week | `data_issue` on USD metrics only |
| `regional_shift` | Real orders shifted from one state to another | `business_change` (concentrated in customer state) |
| `delivery_delay` | Delivery dates pushed back for one seller state | `business_change` on `on_time_delivery_rate` |

### Done when

- [ ] OpenAPI schema is generated and committed; a test fails when it drifts.
- [ ] Each endpoint has request validation and error tests (400, 401, 422, 503).
- [ ] Every scenario can be injected and cleared with `make incident SCENARIO=<name>` and `make incident-clear`.

## Testing and validation strategy

Every pull request runs the full pipeline on a small committed fixture dataset with no network and no real LLM. Numbers are checked against golden values from an independent oracle, so a bug cannot hide by agreeing with itself.

### Fixture dataset and oracle

- `data/fixtures/` holds about 500 orders across 60 days, sampled with a fixed seed by `make fixtures`. It deliberately includes the real quirks: a repeated `review_id`, an order with no payment and an untranslated category.
- Recorded FX and holiday API responses sit beside it.
- `tests/oracle/compute_golden.py` computes every catalog metric with plain pandas from the fixture CSVs and writes `golden.json`. **It must not import any project code. The same script run over the full download writes golden\_full.json for the AWS parity test.**
- `LLM_PROVIDER=fake` loads a scripted test model that returns canned structured outputs. It is rejected when `APP_ENV=aws`.

### Test layers

| Layer | What it proves | Tools | When it runs |
| --- | --- | --- | --- |
| Unit | Functions behave on hand-written inputs | pytest | Every PR |
| Property-based | Contributions sum to the total change; shift-share parts sum to ΔR; the number extractor finds every number | Hypothesis | Every PR |
| Ingestion contracts | Bad rows are quarantined; replay is idempotent | pytest, pandera, MinIO container | Every PR |
| dbt | Keys, relationships, logic, reconciliation, schema | `dbt build` on fixture | Every PR |
| Semantic parity | Each Cube metric equals the oracle's golden value | pytest against Cube | Every PR |
| Agent graph | Routing, retries, clarification, grounding rejection | pytest with fake LLM | Every PR |
| API | Validation, auth, error codes, OpenAPI drift | pytest, httpx | Every PR |
| End to end | Compose stack: ingest, build, Cube, `/ask` returns a grounded answer | pytest, Docker Compose | Every PR |
| Kubernetes smoke | Chart installs on kind with fixture data and the fake LLM; probes pass; one `/ask` succeeds | kind, Helm | Every PR |
| Agent evals | Real LLM quality on golden questions and incident scenarios | pytest, LangSmith optional | Nightly, and manually |
| AWS smoke | Deployed stack is healthy; 5 Cube queries match the full-data golden values | pytest | After each deploy |

### Agent evaluation suite

- **Golden questions:** about 30 questions in `tests/evals/questions.yaml`, each with the expected metrics, dimensions, period and numeric answer.
- **Incident scenarios:** all 6 simulator scenarios, each with the expected verdict and top driver.
- **Pass thresholds:** metric selection at least 90%; numeric answers correct at least 90%; zero ungrounded numbers shipped; at least 5 of 6 scenario verdicts correct.
- Results are written to `evals/report.md` and kept as a CI artifact, so quality changes are visible over time.

### Quality gates

- Line coverage at least 85% on `src/` (the UI is excluded).
- `ruff`, `mypy --strict`, `sqlfluff` and `helm lint` all clean.
- No high or critical vulnerabilities from Trivy or `pip-audit`.

## DevOps: Git, Docker, Kubernetes, AWS and CI/CD

The same images and Helm chart run on Docker Compose, a local kind cluster and EKS. Terraform builds AWS from the laptop; GitHub Actions tests every change and deploys `main` through OIDC.

### Git workflow

- Trunk-based: short-lived branches, pull requests into `main`, branch protection requiring CI.
- `pre-commit` hooks: ruff, sqlfluff, `terraform fmt` and gitleaks (secret scanning).
- Releases are semantic-version tags; images are tagged with the Git SHA and the release tag.

### Docker images

| Image | Contents | Notes |
| --- | --- | --- |
| `metrictrace-app` | FastAPI, agent, investigation engine | Multi-stage, `uv` install, non-root user, healthcheck |
| `metrictrace-pipeline` | Ingestion CLI, dbt project, incident simulator | Runs as a Job or CronJob |
| `metrictrace-ui` | Streamlit | Calls the API only |
| `metrictrace-cube` | Official Cube image (pinned) plus `cube/model/` | Models baked in for reproducibility |

Docker Compose runs MinIO, `minio-init`, Cube, API and UI; the pipeline runs on demand under a `jobs` profile.

### Kubernetes (one Helm chart)

- **Deployments:** api, ui, cube, each with liveness and readiness probes plus CPU and memory requests and limits.
- **CronJob:** the daily pipeline. A simulated clock in `_meta/sim_clock.json` decides which day to replay next.
- **Job:** a one-off backfill, started with `make k8s-backfill`.
- **ServiceAccounts:** separate ones for pipeline, cube and api, each annotated with its IRSA role on AWS.
- **Config:** a ConfigMap for settings; secrets come from a Kubernetes Secret created outside the chart.
- `values-local.yaml`: images loaded into kind, MinIO deployed from `deploy/kind/`, access by port-forward.
- `values-aws.yaml`: ECR images, LoadBalancer Services for api and ui limited to the engineer's IP with `loadBalancerSourceRanges`.

### AWS infrastructure (Terraform)

| Module | Resources |
| --- | --- |
| `bootstrap` | State bucket with versioning; S3 native state locking |
| `network` | VPC across 2 AZs, public subnets only by default; `enable_nat` variable for private nodes |
| `eks` | EKS cluster (official `terraform-aws-modules/eks`, pinned), managed node group of 2 small spot instances, access entries |
| `ecr` | 4 repositories, scan on push, keep the last 10 images |
| `lake` | S3 bucket: versioning, encryption, public access blocked, lifecycle on `_audit/` |
| `catalog` | Glue database, crawler on `marts/`, Athena workgroup with a results bucket and a 1 GB per-query scan limit |
| `iam` | IRSA roles (pipeline: lake read-write and start crawler; cube: marts read, Athena, Glue read; api: lake read and audit write) and the GitHub OIDC deploy role |
| `secrets` | Secrets Manager entries; the engineer sets values with the AWS CLI so they never enter Terraform state |
| `budget` | AWS Budget with an email alert at a threshold the engineer sets |

**Teardown order matters:** `make aws-down` uninstalls the Helm release first, so AWS deletes the load balancers, then runs `terraform destroy`.

### CI/CD workflows (GitHub Actions)

1. **`ci.yml`** on every pull request and push: lint and type checks, unit and property tests, the integration stack (MinIO, ingest fixture, dbt build, Cube parity, end-to-end), Docker builds with Trivy scans, `pip-audit`, `helm lint` plus a kind smoke install, and `terraform fmt`, `validate` and `tflint` (no AWS credentials needed).
2. **`cd.yml`** on `main` after CI passes: assume the deploy role through OIDC, push SHA-tagged images to ECR, then `helm upgrade --install` behind a GitHub environment that needs manual approval. AWS smoke tests run next, and a failure triggers `helm rollback`.
3. **`nightly-evals.yml`** on a schedule: runs the agent evaluation suite with the real LLM against the fixture stack and uploads the report.

Terraform apply stays a manual `make aws-up` from the engineer's laptop, so infrastructure changes are deliberate.

## Build order and rules for the coding LLM

Build one phase at a time and do not start the next until the phase's gate command passes. Give this whole document to the coding LLM, then prompt it phase by phase: "Implement Phase N. Stop when its gate passes."

### Phases and gates

| Phase | Build | Gate (must pass) |
| --- | --- | --- |
| 0 | Repo scaffold, `pyproject.toml`, Makefile, `config.py`, `.env.example`, pre-commit, lint job in `ci.yml` | `make lint` and `make check-env` |
| 1 | Docker Compose with MinIO, `LakeClient`, DuckDB connection factory | `make up` then `make test-lake` |
| 2 | Ingestion, contracts, replay, fixtures, oracle and `golden.json` | `make test-ingestion` and `make fixtures` |
| 3 | dbt project | `make dbt-build` on fixture data |
| 4 | Cube models, `globals.py`, metric catalog | `make test-semantic` (golden parity) |
| 5 | Investigation engine | `make test-investigation` |
| 6 | LangGraph agent, tools, grounding check, fake LLM | `make test-agent` |
| 7 | FastAPI, Streamlit, incident simulator, audit log | `make test-api` and `make e2e` |
| 8 | Dockerfiles; full stack in Compose | `make e2e` against containers |
| 9 | Helm chart, kind config | `make kind-smoke` |
| 10 | Terraform modules and `envs/aws` | `make tf-validate` (no AWS credentials needed) |
| 11 | `ci.yml`, `cd.yml`, `nightly-evals.yml` | CI green on a pull request |
| 12 | Eval suite, README, architecture notes, runbook | `make evals` with a real LLM key |

### Rules

1. **Pin, then verify.** Use the latest stable release of each dependency at build time and lock it. Before using dbt-duckdb, Cube, LangGraph, LangChain or Terraform module APIs, read the docs for the pinned version; never write an API from memory.
2. **Spike the unknowns first.** Where this spec says "verify", write a small passing test proving the mechanism works before building on it.
3. **Never weaken a test to pass.** No skipping, `xfail`, loosened tolerances or edited golden values. Fix the root cause. Never change the oracle to match project output.
4. **No placeholders.** No `TODO`, `pass` bodies or fake data outside `tests/` and `data/fixtures/`.
5. **No hardcoded settings.** Buckets, endpoints, model names and secrets come only from `config.py`.
6. **Metric logic lives in one place.** Business metrics are defined in Cube on top of dbt marts. Python never recomputes a catalog metric, except the oracle.
7. **Tests ship with code.** Every module arrives with its tests in the same phase.
8. **Idempotent commands.** Running any `make` target twice gives the same result.
9. **Record deviations.** Any change to this spec is written as a short decision record in `docs/decisions/`.

## Engineer setup, cost and risks

The engineer supplies credentials and runs a handful of `make` targets; everything else is automated.

### Local setup

- [ ] Install Docker, `uv`, kind, kubectl and Helm.
- [ ] Copy `.env.example` to `.env`; set MinIO credentials, `LLM_PROVIDER`, `LLM_MODEL`, the LLM API key, `CUBE_API_SECRET` and `API_BEARER_TOKEN`.
- [ ] Set `KAGGLE_USERNAME` and `KAGGLE_KEY`, or download the Olist zip into `data/raw_download/`.
- [ ] Run `make check-env`, `make up`, `make backfill FROM=2017-01-01 TO=2017-12-31`, then `make dbt-build`.
- [ ] Open the UI at `http://localhost:8501`.
- [ ] Optional Kubernetes parity: `make kind-up` then `make kind-deploy`.

### AWS setup

- [ ] Configure an AWS CLI profile with rights to create the resources above.
- [ ] Run `make aws-bootstrap` to create the Terraform state bucket.
- [ ] Fill `infra/terraform/envs/aws/terraform.tfvars`: region, `allowed_cidr` (your IP), budget email and amount.
- [ ] Run `make aws-up`, then `make aws-put-secrets` (prompts for keys and stores them in Secrets Manager).
- [ ] Copy Terraform outputs into GitHub secrets and variables (see Configuration).
- [ ] Merge to `main` to deploy, then run `make k8s-backfill` against EKS.
- [ ] Run `make aws-down` when finished.

### Approximate AWS cost

While the stack is running, expect roughly 3–5 USD per day. The EKS control plane is about 0.10 USD per hour and dominates, with small spot nodes, 2 load balancers and minor S3, Glue and Athena charges at this data size. These are estimates from memory; confirm them in the AWS Pricing Calculator for your region. The default design has no NAT gateway, which would add a fixed hourly charge.

### Risks and fallbacks

| Risk | Effect | Mitigation | Fallback |
| --- | --- | --- | --- |
| Cube's Jinja globals or DuckDB S3 settings differ in the pinned version | Local semantic layer cannot read marts | Spike test in Phase 4 | Publish step copies marts into a local DuckDB file that Cube opens directly |
| dbt-duckdb credential chain does not pick up IRSA | Pipeline cannot read S3 on EKS | Spike test in Phase 10 | Pipeline fetches temporary credentials with boto3 and passes them to DuckDB |
| DuckDB and Athena differ in rounding or timestamps | AWS numbers differ slightly from local | Marts cast explicit types (UTC timestamps, doubles); AWS parity smoke test | Document tolerance per metric |
| Kaggle download unavailable | No source data | Manual zip path | Fixture data for demos |
| LLM output varies between runs | Inconsistent answers | Grounding check, eval thresholds, low temperature | Return result table without narrative |
| Public subnets for nodes | Larger attack surface | Security groups and `loadBalancerSourceRanges` limited to your IP | Set `enable_nat=true` for private nodes |
| Forgotten running stack | Ongoing charges | AWS Budget alert | `make aws-down` |
