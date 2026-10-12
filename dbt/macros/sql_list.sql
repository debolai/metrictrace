{#- Render a list of strings as a SQL tuple: ('a', 'b'). -#}
{%- macro sql_list(values) -%}
  ({%- for value in values -%}'{{ value }}'{{ ", " if not loop.last }}{%- endfor -%})
{%- endmacro -%}
