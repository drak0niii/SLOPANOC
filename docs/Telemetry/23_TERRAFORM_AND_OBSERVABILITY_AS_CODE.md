
# 23 — Terraform and Observability as Code

## Production decision

Observability infrastructure is managed as code.

Terraform is the standard for cloud resources and observability configuration where supported.

Manual console configuration is not the system of record.

## Terraform scope

Manage where supported:
- OTel Collector deployment/config dependencies,
- Cloud Monitoring dashboards,
- alert policies,
- log sinks/exclusions,
- BigQuery datasets/tables/views,
- billing export destination dependencies,
- service accounts/IAM,
- retention policies,
- budget alerts where supported,
- Cloud Run observability environment/config wiring.

If a resource cannot be managed by Terraform:
- store declarative config/script in repo,
- document manual bootstrap,
- add drift-review process.

## Repository layout

Recommended:

```text
infra/
  observability/
    terraform/
    collector/
    dashboards/
    alerts/
    bigquery/
```

Exact layout may follow existing infrastructure conventions.

## Environments

Separate:
- dev,
- staging,
- production.

No production dashboard/alert points to dev resources.

## Promotion

Changes follow:
- code review,
- plan,
- apply,
- validation.

Dashboard/alert changes are reviewed like application code.

## Secrets

Terraform must not store provider secrets in plaintext config.

Use approved secret-management patterns.

## Drift

Scheduled drift detection identifies:
- manual dashboard edits,
- alert changes,
- IAM drift,
- retention drift.
