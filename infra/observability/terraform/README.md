# M1 sidecar integration contract

This provider-free module has no resources, data sources, backend, secrets or state
imports. It outputs reviewed inputs for the existing Cloud Run service owner. It
cannot deploy a competing service or change IAM. No apply/plan is part of M1.

Evaluate the owning service's container dependencies so the collector health probe
succeeds before application startup. Mount config.yaml read-only via an approved
immutable image/config artifact or approved secret version. No secret creation is
implied. Supply service version, region, Git SHA and release ID from the deployment;
K_REVISION is normalized by Settings. Use instance-based billing/always-allocated CPU
(cpu_idle=false on ALL relevant containers) for reliable background batches. Evaluate
minimum instances, termination grace and per-instance costs with the actual service.
The output is a contract, not an apply-ready replacement for that unknown service.

Future changes require exact project/service/environment, current template diff,
image digest, config hash, least-privilege existing identity, API status, CPU/cost,
rollback and interruption/security risk review under AGENTS.md. Google Trace agent,
Monitoring metric writer and Logging log writer permissions are candidates for review,
not assignments. No service-account keys or billing-export/SQL/BigQuery resources.

Validate locally with Terraform 1.14.x: fmt -check and validate. No provider download,
backend initialization or remote state is necessary. Do not add cloud providers or
remote backends without revisiting validation isolation. Pin the production image
by reviewed digest before promotion; changing version.json requires config/privacy
compatibility tests. Live sidecar, IAM and destination behavior remains unverified.
