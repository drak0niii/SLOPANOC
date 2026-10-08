output "collector_container_contract" {
  value = local.collector
}
output "application_environment_contract" {
  value = local.application_environment
}
output "collector_configuration_sha256" {
  value = filesha256("${path.module}/../collector/config.yaml")
}
