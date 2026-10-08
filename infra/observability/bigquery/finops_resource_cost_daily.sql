-- v1. Resource analytical view remains warehouse-restricted, not an HTTP DTO.
SELECT DATE(usage_start_time) AS usage_date, invoice.month AS invoice_month,
  billing_account_id, project.id AS project_id, service.id AS service_id,
  sku.id AS sku_id, resource.global_name AS resource_id, currency, cost_type,
  COUNT(*) AS source_rows, SUM(cost) AS pre_credit,
  SUM(COALESCE((SELECT SUM(c.amount) FROM UNNEST(credits) c), CAST(0 AS BIGNUMERIC))) AS credits,
  SUM(cost + COALESCE((SELECT SUM(c.amount) FROM UNNEST(credits) c), CAST(0 AS BIGNUMERIC))) AS net
FROM `__BILLING_VIEW__`
GROUP BY usage_date, invoice_month, billing_account_id, project_id, service_id, sku_id, resource_id, currency, cost_type
