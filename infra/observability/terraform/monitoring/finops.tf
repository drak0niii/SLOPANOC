locals {
  finops_policies = {
    for policy in jsondecode(file("${path.module}/../../monitoring/finops_alert_policies.json")) : policy.policy_id => policy
  }
}

# Local definitions only. Apply requires exact user approval, including disabled rules.
resource "google_monitoring_alert_policy" "finops" {
  for_each              = local.finops_policies
  project               = var.project_id
  display_name          = "${var.environment}: ${each.value.displayName}"
  combiner              = "OR"
  enabled               = false
  notification_channels = []
  user_labels           = merge(each.value.userLabels, { environment = var.environment })
  documentation {
    content   = each.value.documentation.content
    mime_type = "text/markdown"
  }
  conditions {
    display_name = each.value.conditions[0].displayName
    condition_prometheus_query_language {
      query               = replace(each.value.conditions[0].conditionPrometheusQueryLanguage.query, "__ENVIRONMENT__", var.environment)
      duration            = each.value.conditions[0].conditionPrometheusQueryLanguage.duration
      evaluation_interval = "60s"
    }
  }
}
