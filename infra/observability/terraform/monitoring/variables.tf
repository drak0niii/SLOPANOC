variable "project_id" {
  type        = string
  description = "Exact approved target; no implicit current project."
}
variable "environment" {
  type = string
  validation {
    condition     = contains(["development", "staging", "production"], var.environment)
    error_message = "Explicit deployment environment required."
  }
}
variable "notification_channels" {
  type        = list(string)
  default     = []
  description = "Existing deployment-owned channels, never created here."
}

variable "cloud_run_service" {
  type        = string
  description = "Exact environment-specific service for platform dashboard filters."
  validation {
    condition     = can(regex("^[a-z][a-z0-9-]{0,62}$", var.cloud_run_service))
    error_message = "Explicit Cloud Run service name required."
  }
}
variable "cloud_sql_instance" {
  type        = string
  description = "Exact environment-specific instance, without project prefix."
  validation {
    condition     = can(regex("^[a-z][a-z0-9-]{0,97}$", var.cloud_sql_instance))
    error_message = "Explicit Cloud SQL instance name required."
  }
}
