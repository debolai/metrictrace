# 0003 — Ingestion decisions and corrections found while building Phase 2

- **Status:** accepted
- **Date:** 2026-10-12

Profiling the full Olist download and building the replay surfaced facts that the spec got wrong
or left open. Each item says what changed and why.

## Data facts that correct the spec

1. **No in-window order lacks payments.** The only order without payment rows was purchased in
   2016-09, before any replay window. The spec's fixture quirk "an order with no payment" cannot
   occur in a replay. The dbt relationship test still guards the case.
2. **Two lifecycle quirks are too rare for any one fixture window.** Only 2 orders finish as
   `approved` (both 2017), and only 2 have a milestone on an earlier *day* than purchase
   (2018-07 and 2018-08). The other 164 "before purchase" cases are earlier hours on the same day.
   Hand-written unit tests cover both rules, and `fixture.json` lists them as known gaps.
3. **The replay starts with four empty days.** Olist has no orders from 2017-01-01 to 2017-01-04.
   Those days produce `empty` drops, which is correct.
4. **Reviews can be dated before their order** (64 cases). They arrive with the order
   (purchase day) instead, so a review never arrives before its order.
5. **Geolocation has 8 zip prefixes whose median point is outside Brazil.** The contract's
   bounding-box check quarantines them, which demonstrates the quarantine path on real data.

## Decisions

6. **"Cancelled" means `canceled` or `unavailable`.** `unavailable` orders could not be fulfilled,
   so they are excluded from GMV, revenue, payments collected, AOV and active sellers, and they
   count as cancelled in `cancellation_rate`. The spec said only "non-cancelled". The oracle and
   the Phase 4 Cube models use this definition.
7. **The fixture window is 150 days** (purchases 2018-02-01 to 2018-06-30, replay to 2018-08-29),
   not 120. It is the shortest window that contains the required quirks, and it includes two
   BRL/USD moves over 4% in a week (2018-05-11..18 and 2018-06-07..14) for the `fx_outage` incident.
   It holds 2,000 orders.
8. **Anonymous download.** `kagglehub` downloads the public dataset without credentials, so
   `make download-data` needs no Kaggle account. `KAGGLE_USERNAME`/`KAGGLE_KEY` stay optional.
9. **Frankfurter moved** to `https://api.frankfurter.dev/v1` (the old domain redirects). FX is
   fetched one year at a time, and a day's drop holds that day's rates. Weekends and ECB holidays
   produce empty drops.
10. **New settings:** `OLIST_SOURCE_PATH`, `FX_API_BASE_URL`, `HOLIDAYS_API_BASE_URL`,
    `API_CACHE_DIR` and `API_OFFLINE`. With `API_OFFLINE=true` only recorded responses are read,
    and tests use the recordings in `data/fixtures/api/`. An autouse test fixture also blocks
    every non-loopback connection, so "no network in tests" is enforced, not just intended.
11. **The contract is applied iteratively.** A value that cannot be typed also makes that
    column's value checks fail without a row index in pandera. Rows with row-level failures are
    quarantined first and the rest re-validated. Only failures that persist with no failing row
    reject the whole drop. Without this, one bad price rejected the whole day (now a regression
    test).
12. **Raw files leave `drop_date` out of the file.** It lives only in the Hive partition path,
    so DuckDB's `hive_partitioning` supplies it with no conflicting column.
13. **Timestamps land at nanosecond precision.** That is how pandera stores them, and dbt staging
    casts them.
