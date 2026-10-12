# MetricTrace

A governed metrics platform for the Olist Brazilian e-commerce marketplace, with an LLM agent that answers
metric questions and explains why a metric changed. The full design is in
[`MetricTrace — Build Specification v2.md`](MetricTrace%20—%20Build%20Specification%20v2.md); deviations are
recorded in [`docs/decisions/`](docs/decisions/).

## Quick start (development)

```bash
uv sync                      # install pinned dependencies
cp .env.example .env         # then fill in the values marked "set me"
make check-env               # validate configuration without starting anything
make lint                    # ruff, mypy --strict, sqlfluff
make test-unit
```

## Data licence

The Olist dataset is licensed CC BY-NC-SA 4.0, so this project is non-commercial.
