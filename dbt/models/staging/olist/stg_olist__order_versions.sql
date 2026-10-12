-- One row per order per drop: the order's end-of-day state on that simulated day.
select
    order_id,
    customer_id,
    order_status,
    cast(order_purchase_timestamp as timestamp) as purchased_at,
    cast(order_approved_at as timestamp) as approved_at,
    cast(order_delivered_carrier_date as timestamp) as shipped_at,
    cast(order_delivered_customer_date as timestamp) as delivered_at,
    cast(order_estimated_delivery_date as date) as estimated_delivery_date,
    drop_date,
    _ingested_at as ingested_at,
    _record_hash as record_hash
from {{ source('olist', 'orders') }}
