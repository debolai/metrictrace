-- One row per person. Location comes from their latest order, because the same
-- person can appear with different cities or states.
select
    customers.customer_unique_id,
    min(orders.purchase_date) as first_order_date,
    count(*) as order_count,
    arg_max(customers.state, orders.purchased_at) as state,
    arg_max(customers.city, orders.purchased_at) as city,
    arg_max(customers.zip_code_prefix, orders.purchased_at) as zip_code_prefix
from {{ ref('stg_olist__customers') }} as customers
inner join {{ ref('int_orders_current') }} as orders
    on customers.customer_id = orders.customer_id
group by 1
