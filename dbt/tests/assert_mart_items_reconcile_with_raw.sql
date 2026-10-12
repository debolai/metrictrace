-- Item value in the marts must equal the raw item value for every current order: a
-- missing customer, product or order row would silently drop items from the marts.
{{ config(severity='warn') }}

with raw_items as (
    select
        items.order_id,
        sum(items.price) as items_value
    from {{ source('olist', 'order_items') }} as items
    inner join {{ ref('int_orders_current') }} as orders on items.order_id = orders.order_id
    group by 1
),

mart_items as (
    select
        order_id,
        sum(price) as items_value
    from {{ ref('fct_order_items') }}
    group by 1
)

select
    coalesce(raw_items.order_id, mart_items.order_id) as order_id,
    raw_items.items_value as raw_items_value,
    mart_items.items_value as mart_items_value
from raw_items
full outer join mart_items on raw_items.order_id = mart_items.order_id
where abs(coalesce(raw_items.items_value, 0) - coalesce(mart_items.items_value, 0)) > 0.005
