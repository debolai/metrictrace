-- Orders per person (customer_unique_id), in purchase order. The first order is the
-- earliest in the replay; history before the replay window is not visible.
select
    orders.order_id,
    customers.customer_unique_id,
    row_number() over (
        partition by customers.customer_unique_id
        order by orders.purchased_at, orders.order_id
    ) as customer_order_number,
    customer_order_number = 1 as is_first_order
from {{ ref('int_orders_current') }} as orders
inner join {{ ref('stg_olist__customers') }} as customers
    on orders.customer_id = customers.customer_id
