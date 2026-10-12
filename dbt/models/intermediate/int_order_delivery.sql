-- Delivery attributes per order. Dates are compared, not timestamps, because the
-- estimated delivery date is a calendar day.
select
    order_id,
    order_status = 'delivered' and delivered_at is not null as has_valid_delivery_date,
    case
        when order_status = 'delivered' and delivered_at is not null
            then cast(delivered_at as date) - purchase_date
    end as delivery_days,
    case
        when order_status = 'delivered' and delivered_at is not null
            then cast(delivered_at as date) > estimated_delivery_date
    end as is_late
from {{ ref('int_orders_current') }}
