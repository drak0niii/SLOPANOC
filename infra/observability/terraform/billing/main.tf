# Provider-free, inert binding contract. Resource mutation is deliberately deferred
# until exact destinations/IAM/state owners and operation approval are available.
locals {
  definitions = {
    finops_billing_normalized  = replace(file("${path.module}/../../bigquery/finops_billing_normalized.sql"), "__DETAILED_TABLE__", var.detailed_table)
    finops_pricing_normalized  = replace(file("${path.module}/../../bigquery/finops_pricing_normalized.sql"), "__PRICING_TABLE__", var.pricing_table)
    finops_resource_cost_daily = replace(file("${path.module}/../../bigquery/finops_resource_cost_daily.sql"), "__BILLING_VIEW__", var.billing_view)
    finops_focus_normalized    = replace(file("${path.module}/../../bigquery/finops_focus_normalized.sql"), "__FOCUS_TABLE__", var.focus_table)
  }
}
