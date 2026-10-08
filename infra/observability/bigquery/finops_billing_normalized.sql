-- v1. Reviewed source binding replaces __DETAILED_TABLE__; never a browser input.
-- Detailed export is usage_start_time partitioned. Explicit predicates in consumer
-- queries preserve pruning. Credits remain an array: no cost-multiplying UNNEST join.
SELECT
  billing_account_id, STRUCT(service.id AS id) AS service,
  STRUCT(sku.id AS id) AS sku, STRUCT(project.id AS id) AS project,
  STRUCT(resource.global_name AS global_name, resource.name AS name) AS resource,
  STRUCT(location.region AS region) AS location,
  usage_start_time, usage_end_time, export_time, STRUCT(invoice.month AS month) AS invoice, cost_type,
  CAST(cost AS BIGNUMERIC) AS cost, currency,
  CAST(cost_at_list AS BIGNUMERIC) AS cost_at_list, cost_at_effective_price_default, cost_at_list_consumption_model,
  CAST(currency_conversion_rate AS BIGNUMERIC) AS currency_conversion_rate,
  STRUCT(CAST(usage.amount AS BIGNUMERIC) AS amount, usage.unit AS unit,
    CAST(usage.amount_in_pricing_units AS BIGNUMERIC) AS amount_in_pricing_units,
    usage.pricing_unit AS pricing_unit) AS usage,
  ARRAY(SELECT AS STRUCT CAST(c.amount AS BIGNUMERIC) AS amount, c.type, c.id
    FROM UNNEST(credits) AS c) AS credits,
  STRUCT(adjustment_info.id AS id, adjustment_info.type AS type,
    adjustment_info.mode AS mode) AS adjustment_info,
  STRUCT(price.list_price AS list_price, price.effective_price AS effective_price,
    price.effective_price_default AS effective_price_default,
    price.list_price_consumption_model AS list_price_consumption_model,
    price.tier_start_amount AS tier_start_amount, price.unit AS unit,
    price.pricing_unit_quantity AS pricing_unit_quantity) AS price,
  STRUCT(consumption_model.id AS id) AS consumption_model
FROM `__DETAILED_TABLE__`
