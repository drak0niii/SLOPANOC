output "normalized_view_definitions" {
  value       = local.definitions
  description = "Reviewable SQL only; creates no views, datasets, IAM, exports or jobs."
}
output "activation" {
  value = "NOT_EXECUTED_USER_APPROVAL_REQUIRED"
}
