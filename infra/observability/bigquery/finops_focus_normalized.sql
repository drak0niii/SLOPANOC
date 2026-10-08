-- Optional supplementary v1. Never UNION this source into Detailed spend.
SELECT BillingAccountId, BillingCurrency, BillingPeriodStart, BillingPeriodEnd,
  ChargePeriodStart, ChargePeriodEnd, ServiceName, SkuId, x_ExportTime,
  CAST(BilledCost AS BIGNUMERIC) AS BilledCost,
  CAST(EffectiveCost AS BIGNUMERIC) AS EffectiveCost,
  CAST(ListCost AS BIGNUMERIC) AS ListCost,
  CAST(ContractedCost AS BIGNUMERIC) AS ContractedCost
FROM `__FOCUS_TABLE__`
