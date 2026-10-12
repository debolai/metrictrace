# 0004 — dbt decisions and findings from building Phase 3

- **Status:** accepted
- **Date:** 2026-10-12

## Findings that correct the spec

1. **Payment gaps come from installment interest, not vouchers.** The spec asked for the gap
   between payments and items plus freight to be measured before choosing a tolerance. On the
   full replay window, 99.6% of orders match to the cent. Of the 375 that do not, 308 were paid
   in several installments; only 11 involve vouchers. Per purchase day, the payments-to-items
   ratio stays between 0.9969 and 1.0074 over all 602 days. The reconciliation test therefore
   allows ±2% per day (`payment_reconciliation_tolerance`). That is about three times the worst
   real day, and the `duplicate_payments` incident (ratio about 2.0) breaks it.
2. **dbt model contracts are not enforced for dbt-duckdb `external` models.** The external
   materialization never calls the contract checks. Following the spec's fallback, a generic
   test `matches_declared_columns` compares each mart's actual columns and types with its YAML,
   at error severity.

## Decisions

3. **dbt runs in a subprocess with an explicit environment.** On import, and again on every
   `dbtRunner.invoke`, dbt-core 1.12 loads the first `.env` found from the working directory
   upwards into `os.environ`. In this repo that leaked every setting (secrets included) into the
   process, and an empty `AWS_PROFILE=` broke botocore. `metrictrace transform` therefore runs
   dbt with these settings:
   - **Process:** a subprocess, started from a temporary directory outside the repository.
   - **Environment:** only dbt's variables, plus `PATH`/`HOME`/locale and any non-empty `AWS_*`
     variables (so IRSA works on EKS).

   The CLI never imports dbt, and a unit test checks that nothing else leaks into the
   environment.
4. **DuckDB reuses HTTP connections** (`httpfs_connection_caching = true`, in the dbt profile
   and in `lake.connect`).
   - **Default behaviour:** DuckDB opens a new TCP connection for every request. A build reads
     about 1,900 small `_meta` files and hundreds of Parquet partitions, which left about 1,800
     sockets in `TIME_WAIT` and overwhelmed Docker Desktop's port forwarder.
   - **Symptom:** "Server returned nothing" errors that cascade through the shared transaction,
     so retries cannot help.
   - **Ruled out:** RustFS and SeaweedFS failed identically, so the server is not the cause.
   - **Result:** with caching on, 96 of 96 stress queries and 8 of 8 back-to-back builds
     succeeded, and builds got about 2× faster. On S3 it also avoids a TLS handshake per request.

   Disabling HTTP keep-alive instead makes every request fail against RustFS.
5. **A scratch DuckDB file instead of `:memory:`.** dbt uses `dbt/target/metrictrace.duckdb`, and
   `metrictrace transform` deletes it before each run. Runs still start clean (no persistent
   database, as the spec requires), and `dbt docs generate` can see the relations the build
   created, so the docs catalog is complete.
6. **`stg_meta__ingestion` is a table, not a view,** so the roughly 1,900 small JSON records are
   read once per run instead of once per downstream query.
7. **Query retries for transient object-storage errors** (`retries.query_attempts: 3` on
   `IOException`), mainly for S3 throttling on AWS.
8. **The row-count z-score floors the spread at the square root of the mean.** Daily row counts
   are roughly Poisson. On quiet sources, a run of similar days otherwise gives a tiny standard
   deviation, and ordinary days look anomalous. Remaining warnings on the fixture are real:
   reviews ramping up as they start to arrive, and days with about twice the usual orders.
9. **Delivery and first-order logic live in small intermediate models** (`int_order_delivery`,
   `int_customer_orders`). The dbt unit tests can then cover them with one or two inputs each,
   instead of mocking every input of `fct_orders`.
10. **Fixture builds use their own bucket** (`tests/fixture.env`, `metrictrace-fixture`), so
    `make dbt-build-fixture` never mixes fixture and full-data drops.
11. **dbt-duckdb can register outputs in Glue itself** (`glue_register`). The spec keeps the
    crawler, but this is the simpler fallback if the AWS parity test finds a type mismatch.
