# REVIEW ONLY. No remote apply/import/state mutation is authorized.
terraform {
  required_version = ">= 1.14, < 2.0"
  required_providers {
    google = { source = "hashicorp/google", version = "~> 7.0" }
  }
}
provider "google" {
  project = var.project_id
  region  = "europe-west4"
}
variable "project_id" { default = "pr-msn-dev-gl-slopai-01" }
variable "project_number" { default = "995771529960" }
variable "image" {
  type = string
  validation {
    condition     = can(regex("@sha256:[a-f0-9]{64}$", var.image))
    error_message = "Supply a reviewed immutable application image digest."
  }
}
variable "projector_audience" { type = string }
variable "database_secret_ids" { type = map(string) }
variable "database_secret_versions" { type = map(string) }
variable "reader_invoker" { type = string }
variable "reader_environment" {
  type = map(string)
  # Explicit verified-identity policy and immutable producer policy bindings.
}
variable "publisher_enabled" { default = false }
variable "projector_enabled" { default = false }
locals {
  prefix = "slopanoc-development"
  agent  = "service-${var.project_number}@gcp-sa-pubsub.iam.gserviceaccount.com"
  service = {
    outbox    = "${local.prefix}-outbox-publisher"
    projector = "${local.prefix}-observability-projector"
    reader    = "${local.prefix}-observability-reader"
  }
  entrypoint = {
    outbox    = "backend.observability.hybrid.production:create_publisher_app"
    projector = "backend.observability.hybrid.production:create_projector_app"
    reader    = "backend.api.observability_app:create_app"
  }
}
resource "google_service_account" "identity" {
  for_each   = toset(["outbox", "projector", "reader", "push"])
  account_id = "${local.prefix}-${each.key}"
}
resource "google_pubsub_topic" "audit" {
  name = "${local.prefix}-audit-events-v1"
  message_storage_policy { allowed_persistence_regions = ["europe-west4"] }
}
resource "google_pubsub_topic" "dlq" {
  name = "${local.prefix}-audit-dlq-v1"
  message_storage_policy { allowed_persistence_regions = ["europe-west4"] }
}
resource "google_pubsub_subscription" "inspect" {
  name                       = "${local.prefix}-audit-dlq-inspect-v1"
  topic                      = google_pubsub_topic.dlq.id
  message_retention_duration = "604800s"
  expiration_policy { ttl = "" }
}
resource "google_cloud_run_v2_service" "workload" {
  for_each            = local.service
  name                = each.value
  location            = "europe-west4"
  deletion_protection = true
  # IAM enforces invocation. Pub/Sub needs HTTPS access; no allUsers grants.
  ingress = "INGRESS_TRAFFIC_ALL"
  template {
    service_account                  = google_service_account.identity[each.key].email
    timeout                          = "30s"
    max_instance_request_concurrency = each.key == "reader" ? 4 : 16
    scaling {
      min_instance_count = each.key == "outbox" ? 1 : 0
      max_instance_count = each.key == "outbox" ? 1 : 3
    }
    volumes {
      name = "cloudsql"
      cloud_sql_instance {
        instances = ["${var.project_id}:europe-west4:sloc-anoc-sandbox01"]
      }
    }
    containers {
      image   = var.image
      command = ["python", "-m", "uvicorn"]
      args    = [local.entrypoint[each.key], "--factory", "--host", "0.0.0.0", "--port", "8080", "--no-access-log"]
      resources {
        limits   = { cpu = "1", memory = "512Mi" }
        cpu_idle = each.key != "outbox"
      }
      volume_mounts {
        name       = "cloudsql"
        mount_path = "/cloudsql"
      }
      env {
        name = each.key == "reader" ? "SLOPANOC_OBSERVABILITY_READ_DATABASE_URL" : "SLOPANOC_HYBRID_DATABASE_URL"
        value_source {
          secret_key_ref {
            secret  = var.database_secret_ids[each.key]
            version = var.database_secret_versions[each.key]
          }
        }
      }
      dynamic "env" {
        for_each = merge({
          SLOPANOC_HYBRID_ENVIRONMENT          = "development"
          SLOPANOC_HYBRID_PROJECT              = var.project_id
          SLOPANOC_HYBRID_AUDIT_TOPIC          = google_pubsub_topic.audit.id
          SLOPANOC_HYBRID_AUDIT_ENABLED        = "false"
          SLOPANOC_HYBRID_PUBLISHER_ENABLED    = tostring(each.key == "outbox" && var.publisher_enabled)
          SLOPANOC_HYBRID_PROJECTOR_ENABLED    = tostring(each.key == "projector" && var.projector_enabled)
          SLOPANOC_HYBRID_PUSH_AUDIENCE        = var.projector_audience
          SLOPANOC_HYBRID_PUSH_SERVICE_ACCOUNT = google_service_account.identity["push"].email
          SLOPANOC_OTEL_ENVIRONMENT            = "development"
        }, each.key == "reader" ? var.reader_environment : {})
        content {
          name  = env.key
          value = env.value
        }
      }
    }
  }
}
resource "google_pubsub_subscription" "push" {
  name                       = "${local.prefix}-audit-projector-v1"
  topic                      = google_pubsub_topic.audit.id
  ack_deadline_seconds       = 30
  message_retention_duration = "604800s"
  expiration_policy { ttl = "" }
  retry_policy {
    minimum_backoff = "1s"
    maximum_backoff = "60s"
  }
  dead_letter_policy {
    dead_letter_topic     = google_pubsub_topic.dlq.id
    max_delivery_attempts = 10
  }
  push_config {
    push_endpoint = "${google_cloud_run_v2_service.workload["projector"].uri}/internal/observability/events/audit"
    oidc_token {
      service_account_email = google_service_account.identity["push"].email
      audience              = var.projector_audience
    }
  }
  depends_on = [google_pubsub_topic_iam_member.dlq_agent, google_service_account_iam_member.token_creator]
}
resource "google_pubsub_topic_iam_member" "publisher" {
  topic  = google_pubsub_topic.audit.id
  role   = "roles/pubsub.publisher"
  member = "serviceAccount:${google_service_account.identity["outbox"].email}"
}
resource "google_pubsub_topic_iam_member" "dlq_agent" {
  topic  = google_pubsub_topic.dlq.id
  role   = "roles/pubsub.publisher"
  member = "serviceAccount:${local.agent}"
}
resource "google_pubsub_subscription_iam_member" "forwarder" {
  subscription = google_pubsub_subscription.push.id
  role         = "roles/pubsub.subscriber"
  member       = "serviceAccount:${local.agent}"
}
resource "google_service_account_iam_member" "token_creator" {
  service_account_id = google_service_account.identity["push"].name
  role               = "roles/iam.serviceAccountTokenCreator"
  member             = "serviceAccount:${local.agent}"
}
resource "google_cloud_run_v2_service_iam_member" "push_invoker" {
  name     = google_cloud_run_v2_service.workload["projector"].name
  location = "europe-west4"
  role     = "roles/run.invoker"
  member   = "serviceAccount:${google_service_account.identity["push"].email}"
}
resource "google_cloud_run_v2_service_iam_member" "reader_invoker" {
  name     = google_cloud_run_v2_service.workload["reader"].name
  location = "europe-west4"
  role     = "roles/run.invoker"
  member   = var.reader_invoker
}
resource "google_project_iam_member" "sql_client" {
  for_each = local.service
  project  = var.project_id
  role     = "roles/cloudsql.client"
  member   = "serviceAccount:${google_service_account.identity[each.key].email}"
  condition {
    title      = "existing-sandbox-instance-only"
    expression = "resource.name == 'projects/${var.project_id}/instances/sloc-anoc-sandbox01' && resource.type == 'sqladmin.googleapis.com/Instance'"
  }
}
resource "google_secret_manager_secret_iam_member" "database_binding" {
  for_each  = local.service
  secret_id = var.database_secret_ids[each.key]
  role      = "roles/secretmanager.secretAccessor"
  member    = "serviceAccount:${google_service_account.identity[each.key].email}"
}
output "service_urls" { value = { for key, service in google_cloud_run_v2_service.workload : key => service.uri } }
