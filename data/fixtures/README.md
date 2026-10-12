# Fixture dataset

A small, deterministic slice of the Olist data that every pull request replays end to end, offline.

| File | Contents |
| --- | --- |
| `olist/*.csv` | 2,000 orders purchased 2018-02-01 to 2018-06-30, with every row they reference, in the source's own CSV layout |
| `api/` | Recorded Frankfurter (FX) and Nager.Date (holiday) responses for 2018 |
| `fixture.json` | Seed, replay window (to 2018-08-29, a 60-day tail), oracle as-of dates, row counts, the quirks forced into the sample, and known gaps |
| `golden.json` | Catalog metrics from the independent oracle at 2018-04-30 and 2018-08-29 |
| `golden_aws.json` | The same metrics over the full download, 2017-01-01 to 2017-06-30, for the AWS parity test |

Rebuild everything with `make fixtures` (needs the full download and network access). The build is
deterministic: the same download produces identical files. Never edit golden values by hand; they come
only from `tests/oracle/compute_golden.py`.

Licence: CC BY-NC-SA 4.0, see `LICENSE`.
