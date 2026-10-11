# Phase-1 hybrid definitions — REVIEW ONLY

No apply, plan, import, remote state or cloud change was executed or authorized.
Standalone local-backend module; do not adopt existing resources implicitly.

Terraform 1.14.0 and signed Google provider 7.46.1 (lock file) were used for
backend-disabled local initialization and provider-schema validation. Local
provider cache is outside the repository. No credentials are needed for validate.

Represented minimum: two topics, push + attached inspection subscriptions,
four dedicated service accounts, publisher/projector/reader Cloud Run services,
scoped publisher/DLQ forwarding/OIDC/Run/instance-restricted SQL/secret IAM.
Publisher: min=max=1, CPU always allocated; other services request-driven min=0.
Publisher/projector activation defaults false; ANOC capture defaults false elsewhere.

Before separate exact-operation approval, supply reviewed immutable image digest,
existing DB-URL secret IDs and exact versions per dedicated SQL role, verified
projector service-origin audience, authorized reader invoker and trusted reader
identity/producer-policy/origin configuration. A mounted immutable producer-policy
file must exist in the reviewed image/binding; arbitrary config defaults are not an
approved production policy. Secret resources/logins are not created by this module.
No allUsers grant, new database, pgAudit, billing, scheduler, Collector or optional
operational telemetry Pub/Sub. Deployment actor actAs/API enablement prerequisites
are separate, reviewed operations, never inferred from available credentials.

The push endpoint references the deployed projector URI; OIDC audience is an explicit
reviewed origin input because this application must be configured before receiving
traffic. Check origin equals the actual service URL before activating the subscription
consumer/publisher. IAM-protected services without an approved machine/human identity
must remain inaccessible. Reader human production identity remains a blocker.

Manual resource creation would require separately approved IaC ownership/adoption;
prefer a reviewed Terraform rollout. This README does not authorize any operation.
See docs/Telemetry/HYBRID_PHASE1_PRODUCTION_READINESS.md for all remaining gates.
