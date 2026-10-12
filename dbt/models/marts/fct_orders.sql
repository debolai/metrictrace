with items as (
    select
        order_id,
        count(*) as item_count,
        sum(price) as items_value,
        sum(freight_value) as freight_value,
        sum(price_usd) as items_value_usd,
        sum(freight_value_usd) as freight_value_usd
    from {{ ref('int_order_items_enriched') }}
    group by 1
),

payments as (
    select
        order_id,
        sum(payment_value) as payment_value
    from {{ ref('stg_olist__payments') }}
    group by 1
)

select
    orders.order_id,
    customers.customer_unique_id,
    orders.customer_id,
    customers.state as customer_state,
    orders.order_status,
    orders.is_cancelled,
    orders.purchased_at,
    orders.purchase_date,
    orders.approved_at,
    orders.shipped_at,
    orders.delivered_at,
    orders.estimated_delivery_date,
    delivery.has_valid_delivery_date,
    delivery.delivery_days,
    delivery.is_late,
    customer_orders.is_first_order,
    coalesce(items.item_count, 0) as item_count,
    coalesce(items.items_value, 0) as items_value,
    coalesce(items.freight_value, 0) as freight_value,
    coalesce(items.items_value_usd, 0) as items_value_usd,
    coalesce(items.freight_value_usd, 0) as freight_value_usd,
    coalesce(payments.payment_value, 0) as payment_value,
    holidays.holiday_date is not null as is_holiday,
    orders.current_drop_date
from {{ ref('int_orders_current') }} as orders
inner join {{ ref('stg_olist__customers') }} as customers
    on orders.customer_id = customers.customer_id
inner join {{ ref('int_order_delivery') }} as delivery on orders.order_id = delivery.order_id
inner join {{ ref('int_customer_orders') }} as customer_orders
    on orders.order_id = customer_orders.order_id
left join items on orders.order_id = items.order_id
left join payments on orders.order_id = payments.order_id
left join {{ ref('stg_holidays') }} as holidays on orders.purchase_date = holidays.holiday_date
