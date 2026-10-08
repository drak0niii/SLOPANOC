variable "environment" {
  type        = string
  description = "Effective deployment environment; never inferred from credentials."
  validation {
    condition     = contains(["dev", "staging", "production"], var.environment)
    error_message = "Choose dev, staging or production."
  }
}
variable "project_id" {
  type        = string
  description = "Target for future approved deployment; this module performs no cloud operations."
}
variable "collector_config_mount" {
  type        = string
  description = "Read-only config mount supplied by the existing Cloud Run service owner."
  default     = "/etc/otel/config.yaml"
}
