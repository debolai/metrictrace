-- Grain is (review_id, order_id): some review_id values cover several orders.
-- Only exact duplicate rows are dropped.
select
    review_id,
    order_id,
    cast(review_score as integer) as review_score,
    review_comment_title,
    review_comment_message,
    cast(review_creation_date as timestamp) as created_at,
    cast(review_answer_timestamp as timestamp) as answered_at,
    drop_date
from {{ source('olist', 'order_reviews') }}
qualify row_number() over (partition by _record_hash order by drop_date) = 1
