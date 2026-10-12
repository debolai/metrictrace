select
    reviews.review_id || '-' || reviews.order_id as review_key,
    reviews.review_id,
    reviews.order_id,
    reviews.review_score,
    reviews.review_score <= 2 as is_negative,
    reviews.created_at,
    reviews.drop_date as arrived_on,
    orders.purchase_date,
    customers.state as customer_state
from {{ ref('stg_olist__reviews') }} as reviews
inner join {{ ref('int_orders_current') }} as orders on reviews.order_id = orders.order_id
inner join {{ ref('stg_olist__customers') }} as customers
    on orders.customer_id = customers.customer_id
