-- One row identifying the dbt run that built the marts. Cube's refresh key reads it, so
-- cached results are dropped as soon as a new build lands (same SQL on DuckDB and Athena).
select
    '{{ invocation_id }}' as invocation_id,
    cast(current_timestamp as timestamp) as built_at
