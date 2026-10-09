# LOCAL DEFINITIONS ONLY. No project, identity or deployment inferred from ADC.
variable "project_id" {
  type = string
}
variable "environment" {
  type = string
  validation {
    condition     = contains(["development", "staging", "production"], var.environment)
    error_message = "An explicit canonical environment is required."
  }
}
variable "bucket_name" {
  type = string
}
variable "location" {
  type = string
}
variable "writer_service_account" {
  type = string
}
variable "reader_service_account" {
  type = string
}
variable "archive_bridge_endpoint" {
  type      = string
  sensitive = true
}

resource "google_storage_bucket" "exceptional_trace_archive" {
  project                     = var.project_id
  name                        = var.bucket_name
  location                    = var.location
  storage_class               = "STANDARD"
  uniform_bucket_level_access = true
  public_access_prevention    = "enforced"
  force_destroy               = false
  lifecycle {
    precondition {
      condition     = var.writer_service_account != var.reader_service_account
      error_message = "Archive writer and reader identities must be distinct."
    }
    precondition {
      condition     = strcontains(var.bucket_name, var.environment)
      error_message = "The dedicated bucket name must include its canonical environment."
    }
  }
  labels = {
    environment = var.environment
    purpose     = "exceptional-trace-archive"
  }
  lifecycle_rule {
    action {
      type = "Delete"
    }
    condition {
      days_since_custom_time = 90
      matches_suffix         = [".otlp", ".manifest.json"]
    }
  }
  soft_delete_policy {
    retention_duration_seconds = 604800
  }
}

resource "google_storage_bucket_iam_member" "writer" {
  bucket = google_storage_bucket.exceptional_trace_archive.name
  role   = "roles/storage.objectCreator"
  member = "serviceAccount:${var.writer_service_account}"
}
resource "google_storage_bucket_iam_member" "reader" {
  bucket = google_storage_bucket.exceptional_trace_archive.name
  role   = "roles/storage.objectViewer"
  member = "serviceAccount:${var.reader_service_account}"
}
output "archive_binding" {
  sensitive = true
  value = {
    environment       = var.environment
    bucket            = google_storage_bucket.exceptional_trace_archive.name
    bridge_endpoint   = var.archive_bridge_endpoint
    primary_days      = 90
    soft_delete_days  = 7
    binding_status    = "NOT_DEPLOYED"
    transport         = "stable_otlp_http_protobuf"
    archive_policy    = "archive-v1"
    redaction_policy  = "privacy-v1"
    sampling_policy   = "sampling-v1"
  }
}
