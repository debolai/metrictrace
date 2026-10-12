# 0006 — Semantic layer decisions and findings from building Phase 4

- **Status:** accepted
- **Date:** 2026-10-12

## Verified against the docs for Cube v1.8.3 (spec rule 2)

1. **Jinja and Python:** functions registered on a `TemplateContext` in `model/globals.py` are
   callable from the YAML model. A value that fills a whole YAML value needs no `safe` filter.
2. **DuckDB driver:** S3 settings come from `CUBEJS_DB_DUCKDB_S3_ACCESS_KEY_ID`, `_SECRET_ACCESS_KEY`,
   `_ENDPOINT`, `_REGION`, `_USE_SSL` and `_URL_STYLE`. `CUBEJS_DB_DUCKDB_S3_USE_CREDENTIAL_CHAIN`
   exists for AWS.
3. **Athena driver and IRSA:** with no `CUBEJS_AWS_KEY`/`_SECRET`, the driver uses the AWS SDK's
   default credential chain, and the docs name EKS with IRSA explicitly. No static keys are
   needed. The query-results location is `CUBEJS_AWS_S3_OUTPUT_LOCATION`.
4. **Cache and queue without Cube Store:** production mode defaults to Cube Store. No
   pre-aggregations are used, so `CUBEJS_CACHE_AND_QUEUE_DRIVER=memory` and Cube Store is not
   deployed.

## Decisions and findings

5. **Private cubes are named `base_*`; public views carry the clean names** (`sales`, `orders`,
   `payments`, `reviews`). Cubes and views share one namespace, and a measure named `orders`
   inside SQL resolved to the view `orders` until same-cube references used `{CUBE.member}`.
6. **Ratios are built from additive components.** Every ratio and average has a numerator and
   a denominator, published as `kind: component` measures and named in the metric's `meta`. A
   ratio is then always exactly numerator / denominator at any grain, and shift-share can use
   the components directly.
7. **`aov` is a cross-cube measure:** `base_order_items.gmv / base_orders.non_cancelled_orders`.
   It cannot live in the items cube, because some non-cancelled orders have no items (1 in the
   fixture, 2 in January 2017). Cube resolves the join without fan-out, and `aov` matches the
   oracle exactly.
8. **`returning_customer_rate` is not decomposable.** One person can order from several states
   in a period, so per-state counts of people do not add up to the total. The spec had listed
   customer state; the gate's additivity test would fail on it.
9. **Cube camelCases `meta` keys** in `/v1/meta` (`higherIsBetter`, `decomposableBy`) and
   prefixes titles with the view name. The catalog converts keys back to snake_case and uses
   `shortTitle`.
10. **The refresh key reads `mart_dbt_run`,** a one-row mart holding the dbt invocation id. The
    same SQL works on DuckDB and Athena. Cube checks a SQL refresh key at most every 10 seconds
    on DuckDB and every 2 minutes on Athena, so after a build its answers *converge* within
    that interval rather than instantly. The gate test asserts exactly that: it polls until
    Cube serves the new build, within 60 seconds.
11. **Production Cube does not hot-reload model files.** A model change needs a Cube restart.
    The recorded `/v1/meta` used by the unit tests is checked against the live model by an
    integration test (`make record-cube-meta` re-records it).
12. **Identifiers interpolated into SQL are validated** (`^[a-z0-9][a-z0-9_.-]{0,62}$`) in
    `globals.py`, so the mart, bucket and Glue database names cannot inject SQL.
13. **Service secrets must be at least 32 characters.** PyJWT warns that shorter HS256 keys
    are weak (RFC 7518 §3.2), and configuration validation now enforces this length for
    `CUBE_API_SECRET` and `API_BEARER_TOKEN`.
14. **CI runs the Compose stack** (RustFS and Cube) instead of GitHub service containers,
    because Cube needs the model directory mounted, which service containers cannot do.
