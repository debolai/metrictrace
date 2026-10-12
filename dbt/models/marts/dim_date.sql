with calendar as (
    select cast(calendar_day as date) as date_day
    from generate_series(
        cast('{{ env_var("SIM_START_DATE") }}' as date),
        cast('{{ env_var("SIM_END_DATE") }}' as date),
        interval 1 day
    ) as calendar_days (calendar_day)
)

select
    calendar.date_day,
    cast(date_trunc('week', calendar.date_day) as date) as week_start,
    cast(date_trunc('month', calendar.date_day) as date) as month_start,
    year(calendar.date_day) as calendar_year,
    month(calendar.date_day) as calendar_month,
    isodow(calendar.date_day) as iso_weekday,
    dayname(calendar.date_day) as weekday_name,
    isodow(calendar.date_day) >= 6 as is_weekend,
    holidays.holiday_date is not null as is_holiday,
    holidays.holiday_name
from calendar
left join {{ ref('stg_holidays') }} as holidays on calendar.date_day = holidays.holiday_date
