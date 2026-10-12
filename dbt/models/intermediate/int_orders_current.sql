-- Latest version of each order. Versions are ordered by simulated drop date, never by
-- ingestion time, so re-ingesting an old date cannot override a newer state.
select
    order_id,
    customer_id,
    order_status,
    purchased_at,
    cast(purchased_at as date) as purchase_date,
    approved_at,
    shipped_at,
    delivered_at,
    estimated_delivery_date,
    order_status in {{ sql_list(var('cancelled_statuses')) }} as is_cancelled,
    drop_date as current_drop_date
from {{ ref('stg_olist__order_versions') }}
qualify row_number() over (partition by order_id order by drop_date desc) = 1
