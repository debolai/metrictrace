# 0005 — Apache Airflow orchestrates the pipeline

- **Status:** accepted
- **Date:** 2026-10-12
- **Supersedes:** the spec's Kubernetes CronJob and one-off backfill Job.

## Context

The spec orchestrated the daily pipeline with a Kubernetes CronJob, on the grounds that one
linear pipeline does not justify a scheduler platform. For a portfolio aimed at analytics
engineering roles, the orchestrator itself is worth showing: Airflow is the scheduler most
analytics teams run, and it adds run history, retries and backfills in a UI.

## Decision

- **Airflow everywhere.** Docker Compose locally (LocalExecutor), and on kind and EKS through
  the official Apache Airflow Helm chart as a dependency of the MetricTrace chart
  (KubernetesExecutor).
- **One DAG, `metrictrace_daily`:** `ingest` → `transform` → `start_glue_crawler` (AWS only).
  - On Kubernetes each step is a `KubernetesPodOperator` task on the existing pipeline image.
  - `max_active_runs=1` replaces the CronJob's `concurrencyPolicy: Forbid`.
  - Backfills trigger the same DAG with a date range in the run configuration
    (`make airflow-backfill`).
- **dbt runs as one task, not one task per model (no astronomer-cosmos).** The build is a
  single DuckDB session in which staging and intermediate models are views over each other.
  Per-model pods would each start an empty database. Per-model lineage is shown in the dbt docs
  site instead.
- **Larger nodes.** EKS nodes move from t3.medium to t3.large spot, for Airflow's scheduler,
  API server and Postgres. The cost estimate rises to roughly 5–7 USD per day while running.
  Airflow's Postgres uses a small EBS volume, so the EKS module adds the EBS CSI driver add-on.
- **Versions** of Airflow, its Helm chart and the provider packages are pinned and verified
  against their docs when Phases 8 and 9 are built (spec rule 1).

## Consequences

- Phases 8 and 9 gain a DAG run in their gates (Compose end-to-end, kind smoke).
- `make pipeline` keeps running the same steps without Airflow, for quick local work.
- Revisit if Airflow's footprint outweighs its value, or if asset-based orchestration
  (Dagster) becomes the standard to show.
