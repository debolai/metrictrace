-- customer_id is issued per order; customer_unique_id identifies the person.
select
    customer_id,
    customer_unique_id,
    customer_zip_code_prefix as zip_code_prefix,
    customer_city as city,
    customer_state as state,
    drop_date
from {{ source('olist', 'customers') }}
