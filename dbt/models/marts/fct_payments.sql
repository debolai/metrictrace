select
    payments.order_id || '-' || payments.payment_sequential as payment_key,
    payments.order_id,
    payments.payment_sequential,
    payments.payment_type,
    payments.payment_installments,
    payments.payment_value,
    orders.purchase_date,
    orders.is_cancelled,
    customers.state as customer_state
from {{ ref('stg_olist__payments') }} as payments
inner join {{ ref('int_orders_current') }} as orders on payments.order_id = orders.order_id
inner join {{ ref('stg_olist__customers') }} as customers
    on orders.customer_id = customers.customer_id
