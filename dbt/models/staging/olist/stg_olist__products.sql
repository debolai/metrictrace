-- Categories are translated to English. A category with no translation, or no
-- category at all, becomes 'unknown', with flags saying which case it was.
select
    products.product_id,
    products.product_category_name as category_name_pt,
    coalesce(translation.product_category_name_english, 'unknown') as category,
    products.product_category_name is null as is_missing_category,
    products.product_category_name is not null
    and translation.product_category_name is null as is_untranslated,
    cast(products.product_name_lenght as integer) as name_length,
    cast(products.product_description_lenght as integer) as description_length,
    cast(products.product_photos_qty as integer) as photos_qty,
    products.product_weight_g as weight_g,
    products.product_length_cm as length_cm,
    products.product_height_cm as height_cm,
    products.product_width_cm as width_cm,
    products.drop_date
from {{ source('olist', 'products') }} as products
left join {{ source('olist', 'product_category_name_translation') }} as translation
    on products.product_category_name = translation.product_category_name
