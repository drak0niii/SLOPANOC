# M1 Collector foundation

Production baseline: Google-Built OpenTelemetry Collector 0.160.0 sidecar:
https://docs.cloud.google.com/stackdriver/docs/instrumentation/opentelemetry-collector-cloud-run
Component set at the pinned version:
https://github.com/GoogleCloudPlatform/opentelemetry-operations-collector/blob/v0.160.0/google-built-opentelemetry-collector/README.md

version.json owns the exact version/tag, independently of the Python SDK. Verify
image digest and production environment compatibility before promotion; no latest
image, image build/push, deployment or secret/IAM action is authorized by these files.
Matching upstream contrib 0.160.0 is the native development validator because this
workspace has no Docker daemon. Its component validation is local evidence, not
proof of the Google image running on Cloud Run.

config.yaml exports via googlecloud to Cloud Trace, Cloud Monitoring and Cloud
Logging. This preserves the fixed destinations instead of introducing a new backend.
The Google image at this pin includes googlecloud, filter, transform, memory_limiter,
resource, resourcedetection, batch and health_check. Its exporter owns retries; the
local OTLP exporter explicitly caps backoff at 5 seconds. Both bound sending queues.
Application queues separately cap SDK export time. Initial trace sampling is 100%;
no tail sampler or FinOps data path. Foreign scopes, unknown/raw events and links
are filtered; M2 typed lifecycle events receive the same narrow redaction. Transformations remove unsafe attributes; safe event meaning survives.
M1 samples and registered M2 root/phase operations are admitted by explicit
allowlists. M3 model operations/metadata are admitted as described below;
M4 agent/tool and M5 dependency metadata are admitted as described below. Do not enable model-content capture to test ingestion.

OTLP is loopback-only. Health probe listens on the container interface for Cloud Run
startup probing; it serves no diagnostics/content. Self-metrics are loopback pull
telemetry (queue/refused/exporter/memory/process health), not recursively reinjected
into the same pipeline. Remote scraping/alerting and dashboards belong to later work.
Production GCP resource detection/ADC occurs only in the deployed collector, never
application startup or local tests. Both configurations are environment-parametrized:
OTLP_PORT, HEALTH_PORT, SELF_METRICS_PORT, SLOPANOC_ENVIRONMENT; production additionally
GOOGLE_CLOUD_PROJECT; local LOCAL_SINK_ENDPOINT. No credentials in these values.

Validate with a pinned binary's validate subcommand and dummy environment values;
this loads config without starting GCP exporters. Run config.local.yaml only in
isolated loopback tests; it contains no GCP exporter/detector and exports to an
in-memory OTLP HTTP sink. Test harness supplies random ports and credentials-free
environment. Do not run production config as a local smoke test.

Use always-allocated CPU/appropriate instance-based billing for all relevant Cloud
Run containers, readiness dependency and shutdown-budget evaluation. These decisions
need the actual owning service template before any deployment. Structured stdout
fallback and primary OTLP logging require narrowly scoped duplicate-ingestion review;
no broad exclusion is authored or applied without a verified operational filter.

M2 adds typed run/session/turn and optional thread/fault attributes, terminal status,
turn-id origin and safe config hash to span/event projections. It does not deploy
this updated config; production rollout remains subject to exact-operation approval.
Both configs use identical filter/redaction statements, including spanevent context.
Native validator and local SDK-to-Collector-to-sink tests cover safe root event/ID
survival and sentinel denial. No collector sampling/resource/IAM/topology change.

M3 adds exact gen_ai.request, slopanoc.model.operation and slopanoc.model.validation
names and typed model metadata. Model lifecycle events are allowed only on request/
validation spans. Finish reasons are a bounded enum list, IDs are restricted trace
fields, counts are integers, streaming is boolean and durations are finite/bounded.
Unknown gen_ai attributes and message/argument/result bodies remain denied. No
gen_ai wildcard. SDK-to-Collector tests prove positive usage/TTFT/parent metadata
survival and negative content denial; direct malformed OTLP bypass tests independently
exercise Collector redaction without trusting the Python gate.

Per-instrument model counter/histogram projection uses only six fixed dimensions:
environment/provider/model/agent/operation/status. Health gauge rules are unchanged.
Units are 1, s or {token} by exact instrument name; exemplar data is discarded.
No accounting data path or trace sampling change. Updated source is validated locally,
not deployed; cloud rollout still requires exact-operation approval.

M4 adds only slopanoc.agent and slopanoc.tool plus their closed lifecycle events,
approval.requested/completed, procedure_action.resolved and command_authority.completed.
Canonical agent/purpose/role, the 22-name tool registry, category/result/disposition,
nonnegative result count and timeout boolean are validated independently on spans
and events. Tool arguments, response bodies and command payloads are never admitted.
Agent counters/duration use environment/agent/status; tool counters/duration additionally
use tool/tool_category. Units are exactly 1 or s; exemplars are removed. Independent
malformed OTLP tests reject unregistered result values and raw arguments while
preserving safe metadata. Both source configurations validate against native contrib
0.160.0; local OTLP tests use loopback only. This update is not deployed.


M5 admits exactly http.client, db.client, db.connection, db.transaction,
storage.client, secretmanager.client and knowledge.retrieval. Operations, dependency
roles, status/origin/failure kind, transaction outcome, acquisition boundary, finite
HTTP routes and scalar counts are independently constrained on spans/events.
No new lifecycle event or raw SDK payload is admitted. HTTP routes cannot be
reconstructed from request URLs. The poisoned direct-OTLP test bypasses the Python
gate and verifies URL/SQL/token denial alongside positive dependency metadata.

M5 counters/duration use only environment/dependency/operation/status; pool gauges
use environment/dependency. Names and units are finite (1, s, By). No route or ID
label and no SDK/exporter recursion. These local source changes add no deployment,
resource discovery, IAM, sampling, exporter destination or infrastructure control.

M5 Python export strips exemplars; the independent Collector gate drops M5
datapoints containing exemplars (including their filtered attributes/identities).
Direct OTLP poison tests verify a safe sibling point survives and the exemplar
point is excluded. Ordinary private metric labels are stripped independently.
The pinned OTTL Len implementation accepts ExemplarSlice; an empty generic list
is not a valid value for the exemplar setter. Native validation alone is insufficient;
local pipeline tests verify runtime evaluation.
