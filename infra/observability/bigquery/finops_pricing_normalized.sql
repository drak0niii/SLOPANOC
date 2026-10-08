-- v1. Account prices and list prices are separate; observed snapshots are not
-- proof of prices before export activation. Older schemas require reviewed v1 view
-- compatibility projection, not silent omission of financial fields.
SELECT _PARTITIONTIME AS source_partition_time, billing_account_id,
  STRUCT(service.id AS id) AS service, STRUCT(sku.id AS id) AS sku,
  pricing_unit, account_currency_code,
  CAST(currency_conversion_rate AS BIGNUMERIC) AS currency_conversion_rate,
  pricing_as_of_time, export_time,
  STRUCT(list_price.aggregation_info AS aggregation_info,
    ARRAY(SELECT AS STRUCT CAST(t.start_usage_amount AS BIGNUMERIC) AS start_usage_amount,
      CAST(t.pricing_unit_quantity AS BIGNUMERIC) AS pricing_unit_quantity,
      t.usd_amount, t.account_currency_amount FROM UNNEST(list_price.tiered_rates) t) AS tiered_rates) AS list_price,
  IF(billing_account_price IS NULL, NULL,
    STRUCT(billing_account_price.aggregation_info AS aggregation_info,
      ARRAY(SELECT AS STRUCT CAST(t.start_usage_amount AS BIGNUMERIC) AS start_usage_amount,
        CAST(t.pricing_unit_quantity AS BIGNUMERIC) AS pricing_unit_quantity,
        t.usd_amount, t.account_currency_amount FROM UNNEST(billing_account_price.tiered_rates) t) AS tiered_rates)) AS billing_account_price,
  ARRAY(SELECT AS STRUCT m.consumption_model_id,
    STRUCT(m.list_price.aggregation_info AS aggregation_info,
      ARRAY(SELECT AS STRUCT CAST(t.start_usage_amount AS BIGNUMERIC) AS start_usage_amount,
        CAST(t.pricing_unit_quantity AS BIGNUMERIC) AS pricing_unit_quantity,
        t.usd_amount, t.account_currency_amount FROM UNNEST(m.list_price.tiered_rates) t) AS tiered_rates) AS list_price,
    IF(m.billing_account_price IS NULL, NULL,
      STRUCT(m.billing_account_price.aggregation_info AS aggregation_info,
        ARRAY(SELECT AS STRUCT CAST(t.start_usage_amount AS BIGNUMERIC) AS start_usage_amount,
          CAST(t.pricing_unit_quantity AS BIGNUMERIC) AS pricing_unit_quantity,
          t.usd_amount, t.account_currency_amount FROM UNNEST(m.billing_account_price.tiered_rates) t) AS tiered_rates)) AS billing_account_price
    FROM UNNEST(consumption_model_prices) m) AS consumption_model_prices
FROM `__PRICING_TABLE__`
