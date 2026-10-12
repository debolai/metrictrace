-- One row per published rate date: units of USD and EUR per 1 BRL.
select
    cast(rate_date as date) as rate_date,
    max(rate) filter (where currency = 'USD') as usd_per_brl,
    max(rate) filter (where currency = 'EUR') as eur_per_brl,
    max(drop_date) as drop_date
from {{ source('reference', 'fx_rates') }}
where base = 'BRL'
group by 1
