-- Materialized (in memory) so the many small JSON records are read once per run.
{{ config(materialized='table') }}

-- Ingestion records: one per source per drop (daily sources), per year (holidays)
-- or once (reference tables). "partition" is a reserved word in DuckDB, so it stays quoted.
select
    source,
    "partition" as partition_key,  -- noqa: RF06
    case
        when "partition" like 'drop_date=%'  -- noqa: RF06
            then cast(substr("partition", length('drop_date=') + 1) as date)  -- noqa: RF06
    end as drop_date,
    status,
    input_rows,
    row_count,
    quarantine_count,
    run_id,
    cast(ingested_at as timestamp) as ingested_at
from {{ source('lake_meta', 'ingestion') }}
