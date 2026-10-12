{#- Brazil's five official macro-regions, by state (UF) code. -#}
{%- macro brazil_region(state) -%}
  case
    when {{ state }} in ('AC', 'AM', 'AP', 'PA', 'RO', 'RR', 'TO') then 'North'
    when {{ state }} in ('AL', 'BA', 'CE', 'MA', 'PB', 'PE', 'PI', 'RN', 'SE') then 'Northeast'
    when {{ state }} in ('DF', 'GO', 'MS', 'MT') then 'Central-West'
    when {{ state }} in ('ES', 'MG', 'RJ', 'SP') then 'Southeast'
    when {{ state }} in ('PR', 'RS', 'SC') then 'South'
  end
{%- endmacro -%}
