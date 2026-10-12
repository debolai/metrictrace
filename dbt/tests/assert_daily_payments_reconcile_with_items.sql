-- Per purchase day, payments on non-cancelled orders must match items plus freight
-- within the measured tolerance (installment interest and vouchers keep the real ratio
-- within 0.9969-1.0074; decision 0004). Duplicated payments push it far outside.
{{ config(severity='warn') }}

select
    purchase_date,
    sum(payment_value) as payment_value,
    sum(items_value + freight_value) as items_and_freight_value,
    sum(payment_value) / sum(items_value + freight_value) as ratio
from {{ ref('fct_orders') }}
where not is_cancelled and item_count > 0
group by 1
having
    abs(sum(payment_value) / sum(items_value + freight_value) - 1)
    > {{ var('payment_reconciliation_tolerance') }}
