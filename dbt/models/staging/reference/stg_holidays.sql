select
    cast(holiday_date as date) as holiday_date,
    name as holiday_name,
    local_name,
    is_global,
    types as holiday_types
from {{ source('reference', 'holidays') }}
where country_code = 'BR'
