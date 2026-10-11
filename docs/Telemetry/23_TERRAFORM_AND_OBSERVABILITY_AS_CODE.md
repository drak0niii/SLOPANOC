
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

## Separate read service — future reviewed infrastructure scope

Doc [25](25_OBSERVABILITY_CONSOLE_AND_READ_PLANE.md) defines an independently
deployable Observability Read API: separate Cloud Run entrypoint/compute/scaling/
health/service identity; least-privilege observability DB and archive/telemetry
reads; explicit frontend API base/origins and verified authentication bindings.
Shared database connections/scans must be bounded across reader replicas.
Production settings show effective read-only configuration and never bypass IaC.
This pass authors architecture documentation only, no Terraform/config files.
Future IAM/users/secrets/service/DNS deployment, shared migration, apply/import/
state mutation require exact-operation approval; environment/project bindings
are unresolved. Independent service and read privileges are not live-validated.

## Hybrid future resource scope

[26 — Hybrid Event-Driven Observability + Auditability](26_HYBRID_EVENT_DRIVEN_OBSERVABILITY.md) and [resource manifest](GCP_HYBRID_OBSERVABILITY_RESOURCE_MANIFEST.md) add
Phase-1 audit Pub/Sub topic/subscription/DLQ/authenticated push, publisher/projector/
reader services, least-privilege identities/DB roles, audit configuration, dedicated
archive and billing/pricing export dependencies. Phase 1/2/optional classification
is planning only. Operational Telemetry Pub/Sub is OPTIONAL / SCALE-TRIGGERED,
disabled, curated only after measured need; it is not Phase-1 minimum. Terraform stays system of record where supported; manual-only
bootstrap requires source-controlled runbook/drift review. No apply/import/state
operation, API enablement, IAM/grant, pgAudit restart, billing setup, resource creation
or shared migration is authorized by this pass. Confirm exact bindings before approval.

## Phase-1 production-readiness update — 2026-10-11

Review-only hybrid module under infra/observability/terraform/hybrid represents minimum audit resources and three disabled-default services. Backend-disabled local init and fmt/validate only; no plan/apply/import/state/cloud mutation. See HYBRID_PHASE1_PRODUCTION_READINESS.md for required reviewed inputs.
