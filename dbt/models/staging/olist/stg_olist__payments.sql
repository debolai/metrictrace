select
    order_id,
    cast(payment_sequential as integer) as payment_sequential,
    payment_type,
    cast(payment_installments as integer) as payment_installments,
    payment_value,
    drop_date
from {{ source('olist', 'order_payments') }}
