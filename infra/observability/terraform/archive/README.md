# Exceptional archive — unexecuted local infrastructure contract

M15 only authors these definitions. No bucket, IAM binding, service identity,
deployment, scheduler, Cloud Logging change or shared migration has been executed.
All inputs are supplied explicitly during a later exact-operation approval gate.
No backend or provider plan/init has run. Native Terraform fmt/validate remains
NOT EXECUTED — CLI unavailable; static HCL inspection is separate.

Independent redacted Collector branches export to Cloud Trace and exceptional-only
stable OTLP/HTTP. No experimental GCS Collector exporter is required. Local bridge
uses a disposable filesystem sink. Production bridge deployment remains M17, with
TLS/authentication, durable safe bounded staging, trusted closure receipt and
conditional immutable object commits qualified before activation. No GCS write
implementation is implied by the local stand-in.

Bucket per environment, uniform access and public-access prevention. Writer creates
objects only; separate server-side diagnostic reader may read objects. Application
RBAC and environment scope are required in addition to storage IAM. No anonymous
objects, public signed URLs or browser bucket credentials. Platform encryption is
assumed; no CMEK implementation is claimed. Standard storage cost depends on
eligible sanitized trace volume, request rate and retention; live sizing is pending.

Commit full immutable payload and manifest at the same primary-retention anchor;
eligibility is first durable complete commit plus 90 days. Lifecycle eligibility 90 days after Custom-Time is
asynchronous. Explicit soft delete 7 days can extend physical retention. Staging
chunks require their own bounded TTL and must never delete dependencies of a retained
manifest. No exact-day erasure or invented legal-hold guarantee. Retry recovery
must retain the original anchor, not rewrite objects or refresh TTL.

M16 handoff (record only): bursts, tail-sampler memory/late chunks, independent queue
saturation, bridge/Collector restart, partial loss, malformed OTLP, retry/idempotency,
GCS-latency simulation and reconstruction under load. M17 handoff (record only):
bridge service deployment/binding, buckets/lifecycle, IAM/identities, monitoring,
alerts, retrieval activation and approved live validation.

The lifecycle definition uses `days_since_custom_time = 90`, not upload age.
The future production binding must set the same non-refreshing `Custom-Time` to
first complete durable commit on the payload and manifest. An object with no
Custom-Time remains retained; missing metadata must be surfaced as degradation,
not silently replaced with an earlier upload-age cutoff. Cloud Storage supports
this condition and leaves objects without that metadata ineligible
([provider lifecycle documentation](https://docs.cloud.google.com/storage/docs/lifecycle)).
The exact GCS commit/metadata protocol and necessary narrowly scoped permissions
must be qualified and explicitly approved before M17 binding; the create-only
writer definition is not represented as having executed that protocol.
