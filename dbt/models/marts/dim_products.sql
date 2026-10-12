select
    product_id,
    category,
    category_name_pt,
    is_untranslated,
    is_missing_category,
    name_length,
    description_length,
    photos_qty,
    weight_g,
    length_cm,
    height_cm,
    width_cm
from {{ ref('stg_olist__products') }}
qualify row_number() over (partition by product_id order by drop_date desc) = 1
