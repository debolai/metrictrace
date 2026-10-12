select
    sellers.seller_id,
    sellers.state,
    {{ brazil_region('sellers.state') }} as region,
    sellers.city,
    sellers.zip_code_prefix,
    geo.latitude,
    geo.longitude
from {{ ref('stg_olist__sellers') }} as sellers
left join {{ ref('stg_olist__geolocation') }} as geo
    on sellers.zip_code_prefix = geo.zip_code_prefix
qualify row_number() over (partition by sellers.seller_id order by sellers.drop_date desc) = 1
