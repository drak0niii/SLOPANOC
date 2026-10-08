# Provider-free integration contract. Merge outputs into the existing owned
# service template only after exact-operation approval; never import/adopt here.
locals {
  collector_release = jsondecode(file("${path.module}/../collector/version.json"))
  collector = {
    name  = "otel-collector"
    image = local.collector_release.production_image
    args  = ["--config=${var.collector_config_mount}"]
    env = {
      GOOGLE_CLOUD_PROJECT = var.project_id
      SLOPANOC_ENVIRONMENT = var.environment
      OTLP_PORT            = "4318"
      HEALTH_PORT          = "13133"
      SELF_METRICS_PORT    = "8888"
    }
    resources = { cpu = "1", memory = "256Mi", cpu_idle = false }
    startup_probe = {
      http_get          = { port = 13133, path = "/" }
      period_seconds    = 5
      failure_threshold = 12
    }
  }
  application_environment = {
    SLOPANOC_OBSERVABILITY_ENABLED       = "true"
    SLOPANOC_OTEL_ENABLED                = "true"
    SLOPANOC_OTEL_EXPORTER_MODE          = "otlp"
    SLOPANOC_OTEL_ENVIRONMENT            = var.environment
    SLOPANOC_OTEL_CLOUD_RUN_SIDECAR      = "true"
    SLOPANOC_OTEL_EXPORTER_OTLP_ENDPOINT = "http://127.0.0.1:4318"
  }
}
