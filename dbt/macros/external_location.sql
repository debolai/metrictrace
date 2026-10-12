{#- Marts land at marts/<model>/<model>.parquet: one folder per model, so the Glue
    crawler makes one table per model and Cube can glob the folder. -#}
{%- macro external_location(relation, config) -%}
  {%- set format = config.get('format', 'parquet') -%}
  {{- adapter.external_root() }}/{{ relation.identifier }}/{{ relation.identifier }}.{{ format }}
{%- endmacro -%}
