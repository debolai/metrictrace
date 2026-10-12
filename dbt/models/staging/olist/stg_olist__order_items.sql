select
    order_id,
    cast(order_item_id as integer) as order_item_id,
    product_id,
    seller_id,
    cast(shipping_limit_date as timestamp) as shipping_limit_at,
    price,
    freight_value,
    drop_date
from {{ source('olist', 'order_items') }}
