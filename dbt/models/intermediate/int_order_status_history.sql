-- One row per order state, valid from its drop date until the next version's drop date
-- (exclusive). The current state has no valid_to.
select
    order_id,
    order_status,
    drop_date as valid_from,
    lead(drop_date) over (partition by order_id order by drop_date) as valid_to,
    lead(drop_date) over (partition by order_id order by drop_date) is null as is_current
from {{ ref('stg_olist__order_versions') }}
