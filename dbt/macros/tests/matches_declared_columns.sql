{#- Schema check: the model's actual columns and types must equal the columns declared
    (with data_type) in its YAML. dbt-duckdb does not enforce model contracts for
    external materializations, so this test does it instead. Returns one row per
    missing, extra or mistyped column. -#}
{% test matches_declared_columns(model) %}
  {%- if not execute -%}
    select 1 as column_name where false
  {%- else -%}
    {%- set node = graph.nodes.values()
        | selectattr("resource_type", "equalto", "model")
        | selectattr("name", "equalto", model.identifier)
        | first -%}
    {%- set declared = [] -%}
    {%- for name, column in node.columns.items() -%}
      {%- do declared.append((name | lower, (column.data_type or "") | upper)) -%}
    {%- endfor -%}
    {%- set actual = [] -%}
    {%- for column in adapter.get_columns_in_relation(model) -%}
      {%- do actual.append((column.name | lower, column.dtype | upper)) -%}
    {%- endfor -%}
    with declared (column_name, data_type) as (
      {%- if declared %} values {% for name, type in declared -%}
        ('{{ name }}', '{{ type }}'){{ ", " if not loop.last }}
      {%- endfor %}{% else %} select null, null where false{% endif %}
    ),
    actual (column_name, data_type) as (
      values {% for name, type in actual -%}
        ('{{ name }}', '{{ type }}'){{ ", " if not loop.last }}
      {%- endfor %}
    )
    select
      coalesce(declared.column_name, actual.column_name) as column_name,
      declared.data_type as declared_type,
      actual.data_type as actual_type
    from declared
    full outer join actual on declared.column_name = actual.column_name
    where declared.column_name is null
       or actual.column_name is null
       or declared.data_type <> actual.data_type
  {%- endif -%}
{% endtest %}
