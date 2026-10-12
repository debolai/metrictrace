-- Items with their order's state, product category, seller and customer location, and
-- prices converted to USD at the purchase-day rate.
select
    items.order_id,
    items.order_item_id,
    items.product_id,
    items.seller_id,
    orders.purchase_date,
    orders.is_cancelled,
    products.category,
    sellers.state as seller_state,
    customers.state as customer_state,
    items.price,
    items.freight_value,
    items.price * fx.usd_per_brl as price_usd,
    items.freight_value * fx.usd_per_brl as freight_value_usd
from {{ ref('stg_olist__order_items') }} as items
inner join {{ ref('int_orders_current') }} as orders on items.order_id = orders.order_id
inner join {{ ref('stg_olist__customers') }} as customers
    on orders.customer_id = customers.customer_id
left join {{ ref('stg_olist__products') }} as products on items.product_id = products.product_id
left join {{ ref('stg_olist__sellers') }} as sellers on items.seller_id = sellers.seller_id
left join {{ ref('int_fx_daily_filled') }} as fx on orders.purchase_date = fx.rate_date
