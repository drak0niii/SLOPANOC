
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

## Exceptional archive definitions — local authoring boundary

M15 may author only the dedicated environment-isolated GCS archive bucket contract,
90-day primary lifecycle target, explicit soft-delete/platform encryption assumptions,
least-privilege writer/reader IAM contract, archive bridge configuration and Collector
eligible-only stable OTLP routing. Names/projects/service identities and deployment
bindings must be supplied during later reviewed activation, never inferred from credentials.
No bucket/IAM/lifecycle/deployment/apply/import/state mutation is authorized by this
contract. Native Terraform validation remains NOT EXECUTED while CLI unavailable;
static inspection is not native PASS. Doc 16 defines archive retention eligibility,
asynchronous deletion, transport independence and M16/M17 handoff. No unsupported
Cloud Trace 90-day setting or artificial Monitoring 13-month TTL may be authored.
