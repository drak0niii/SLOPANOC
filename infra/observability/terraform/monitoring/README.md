# M9 Monitoring definitions

Generated JSON is derived from backend/observability/slo_contract.py by
`.venv/bin/python -m backend.observability.slo_iac`. Definitions are provisional;
all policies are disabled. One combined PromQL condition per logical policy avoids
multi-condition incident multiplication; it does not guarantee one notification.
Root policies alone receive deployment-owned paging channels. Context policies
remain separate triage/ticket signals. Contacts and channels are not created here.

No remote backend, state or resource has been initialized, planned or applied.
Terraform apply, IAM/channel changes and deployment need exact-operation approval.
Production metric ingestion, exported metric naming, environment filters, receiver
grouping, noise and thresholds require live validation after approved activation.
Cloud Monitoring provider is a future read-only source seam; current backend uses
compact operational rollups with explicit source kind, watermark and coverage.


Every application selector contains `__ENVIRONMENT__`; Terraform binds it to the
explicit environment. Platform charts also require exact project/service/Cloud SQL
instance inputs, with no implicit current project. Histogram panels use native
Monitoring descriptive p95 aggregation; canonical pass/fail still uses exact receipts.
Reference catalogs: https://docs.cloud.google.com/monitoring/api/metrics_gcp_p_z
and https://docs.cloud.google.com/monitoring/api/metrics_gcp_c. No catalog lookup is
production series validation. Unconnected Collector/Graph and baseline-pending
signals remain explicit; BigQuery/monetary dashboards are outside M9.

Local validation used Terraform 1.14.x, Google provider 7.0.0 and a scratch directory
with backend disabled. Committed lock file pins checksums; no .terraform cache/state
is included. Provider schema validation requires only a local plugin socket and no
credentials. Native syntax checks used PromQL parser 0.5.0 in isolated scratch space.
