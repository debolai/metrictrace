# MetricTrace

A governed metrics platform for the Olist Brazilian e-commerce marketplace, with an LLM agent that answers
metric questions and explains why a metric changed. The full design is in
[`MetricTrace — Build Specification v2.md`](MetricTrace%20—%20Build%20Specification%20v2.md); deviations are
recorded in [`docs/decisions/`](docs/decisions/).

## Quick start (development)

```bash
make install                 # pinned dependencies + DuckDB extensions (one-time download)
cp .env.example .env         # then fill in the values marked "set me"
make check-env               # validate configuration without starting anything
make up                      # RustFS object store + lake bucket (console: http://localhost:9001)
make download-data           # public Olist dataset into data/raw_download (no Kaggle account needed)
make backfill FROM=2017-01-01 TO=2017-01-31   # replay daily drops into the lake
make lint                    # ruff, mypy --strict, sqlfluff
make test-unit               # no network, no running services
make test-lake               # lake integration tests against the running stack
make test-ingestion          # ingestion tests, replaying the committed fixture offline
```

`make down` stops the stack and keeps the data; `make clean-lake` deletes the local lake.

## Data licence

The Olist dataset is licensed CC BY-NC-SA 4.0, so this project is non-commercial.
