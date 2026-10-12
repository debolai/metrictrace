-- Data health per daily source per simulated day. Expected drops run from the replay
-- start to the latest day any source was ingested (the pipeline's clock), so a day with
-- no record at all shows up as missing. Feeds the investigation engine.
{% set daily_sources = [
    'olist/orders',
    'olist/order_items',
    'olist/order_payments',
    'olist/order_reviews',
    'olist/customers',
    'olist/sellers',
    'olist/products',
    'fx',
] %}

with clock as (
    select max(drop_date) as last_drop_date
    from {{ ref('stg_meta__ingestion') }}
),

expected as (
    select
        sources.source,
        cast(calendar_days.calendar_day as date) as drop_date
    from (
        values
        {% for source in daily_sources -%}
            ('{{ source }}'){{ "," if not loop.last }}
        {% endfor -%}
    ) as sources (source)
    cross join generate_series(
        cast('{{ env_var("SIM_START_DATE") }}' as date),
        (select last_drop_date from clock),
        interval 1 day
    ) as calendar_days (calendar_day)
),

received as (
    select * from {{ ref('stg_meta__ingestion') }}
    where drop_date is not null
),

order_lateness as (
    select
        drop_date,
        avg(case when cast(purchased_at as date) < drop_date then 1.0 else 0.0 end)
            as late_arrival_share
    from {{ ref('stg_olist__order_versions') }}
    group by 1
),

untranslated as (
    select
        drop_date,
        avg(case when is_untranslated then 1.0 else 0.0 end) as untranslated_category_share
    from {{ ref('stg_olist__products') }}
    group by 1
),

joined as (
    select
        expected.source,
        expected.drop_date,
        received.source is not null as is_received,
        received.status,
        coalesce(received.row_count, 0) as row_count,
        coalesce(received.quarantine_count, 0) as quarantine_count,
        coalesce(received.input_rows, 0) as input_rows,
        case when expected.source = 'olist/orders' then lateness.late_arrival_share end
            as late_arrival_share,
        case
            when expected.source = 'olist/products'
                then untranslated.untranslated_category_share
        end as untranslated_category_share,
        case when expected.source = 'fx' then fx.is_filled end as fx_is_filled,
        case when expected.source = 'fx' then fx.days_since_observed end as fx_days_since_observed
    from expected
    left join received
        on expected.source = received.source and expected.drop_date = received.drop_date
    left join order_lateness as lateness on expected.drop_date = lateness.drop_date
    left join untranslated on expected.drop_date = untranslated.drop_date
    left join {{ ref('int_fx_daily_filled') }} as fx on expected.drop_date = fx.rate_date
),

scored as (
    select
        *,
        avg(row_count) over last_28_drops as prior_mean_row_count,
        stddev_samp(row_count) over last_28_drops as prior_stddev_row_count,
        count(*) over last_28_drops as prior_days
    from joined
    window last_28_drops as (
        partition by source order by drop_date rows between 28 preceding and 1 preceding
    )
)

select
    source,
    drop_date,
    is_received,
    not is_received as is_missing,
    status,
    status = 'failed' as is_failed,
    row_count,
    quarantine_count,
    input_rows,
    -- Row counts are roughly Poisson, so the spread is floored at sqrt(mean): on quiet
    -- sources a run of similar days would otherwise make any normal day look anomalous.
    case
        when prior_days >= 7 and prior_mean_row_count > 0
            then
                (row_count - prior_mean_row_count)
                / greatest(prior_stddev_row_count, sqrt(prior_mean_row_count))
    end as row_count_zscore,
    late_arrival_share,
    untranslated_category_share,
    fx_is_filled,
    fx_days_since_observed
from scored
