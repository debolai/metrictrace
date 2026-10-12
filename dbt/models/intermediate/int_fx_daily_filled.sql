-- A rate for every calendar day ingested. Weekends and ECB holidays carry the last
-- published rate forward; is_filled and days_since_observed say when that happened.
with calendar as (
    select cast(calendar_day as date) as rate_date
    from generate_series(
        (select min(rate_date) from {{ ref('stg_fx__rates') }}),
        (
            select max(drop_date) from {{ ref('stg_meta__ingestion') }}
            where source = 'fx'
        ),
        interval 1 day
    ) as calendar_days (calendar_day)
),

joined as (
    select
        calendar.rate_date,
        rates.usd_per_brl,
        rates.eur_per_brl,
        case when rates.rate_date is not null then calendar.rate_date end as observed_on
    from calendar
    left join {{ ref('stg_fx__rates') }} as rates on calendar.rate_date = rates.rate_date
)

select
    rate_date,
    last_value(usd_per_brl ignore nulls) over by_day as usd_per_brl,
    last_value(eur_per_brl ignore nulls) over by_day as eur_per_brl,
    observed_on is null as is_filled,
    rate_date - last_value(observed_on ignore nulls) over by_day as days_since_observed
from joined
window by_day as (order by rate_date rows between unbounded preceding and current row)
