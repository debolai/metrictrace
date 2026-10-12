select
    order_id || '-' || order_item_id as order_item_key,
    order_id,
    order_item_id,
    product_id,
    seller_id,
    purchase_date,
    is_cancelled,
    category,
    seller_state,
    customer_state,
    price,
    freight_value,
    price_usd,
    freight_value_usd
from {{ ref('int_order_items_enriched') }}
