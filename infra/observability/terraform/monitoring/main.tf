locals {
  generated_policies = jsondecode(file("${path.module}/../../monitoring/alert_policies.json"))
  policies = {
    for policy in local.generated_policies : policy.policy_id => policy
    if length(policy.conditions) == 1
  }
  dashboard_names = toset([
    "service_health", "latency_reliability", "models_agents_tools",
    "dependencies_infrastructure", "telemetry_safety"
  ])
}

# Applying even disabled policies is a remote mutation requiring exact approval.
resource "google_monitoring_alert_policy" "sre" {
  for_each              = local.policies
  project               = var.project_id
  display_name          = "${var.environment}: ${each.value.displayName}"
  combiner              = "OR"
  enabled               = false
  user_labels           = merge(each.value.userLabels, { environment = var.environment })
  notification_channels = each.value.userLabels.notification_route == "root" ? var.notification_channels : []
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

resource "google_monitoring_dashboard" "sre" {
  for_each = local.dashboard_names
  project  = var.project_id
  dashboard_json = replace(replace(replace(replace(
    file("${path.module}/../../dashboards/${each.key}.json"),
    "__ENVIRONMENT__", var.environment), "__PROJECT_ID__", var.project_id),
  "__CLOUD_RUN_SERVICE__", var.cloud_run_service), "__CLOUD_SQL_INSTANCE__", var.cloud_sql_instance)
}
