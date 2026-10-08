# 02 — Telemetry Data Contract

## Purpose

Define one canonical vocabulary so traces, logs, metrics, UI and FinOps describe the same runtime.

No production code may invent arbitrary stage/status/error names without updating this contract.

## Stage vocabulary

### Request/session
```text
request.received
request.validated
session.load.started
session.load.completed
attachments.started
attachments.completed
```

### Orchestration
```text
planning.started
planning.model.started
planning.model.completed
planning.completed
planning.failed
thread.resolve.started
thread.resolve.completed
pending_interaction.resolve.started
pending_interaction.resolve.completed
source_requirements.completed
```

### Context
```text
context.selection.started
context.selection.completed
context.item.selected
context.item.excluded
```

### Agents
```text
agent.started
agent.completed
agent.failed
agent.team_manager
agent.technical_authority
agent.incident_manager
agent.problem_manager
agent.automated_operations
```

### Models
```text
model.request.started
model.first_token
model.request.completed
model.request.failed
model.request.timeout
```

### Tools/dependencies
```text
tool.started
tool.completed
tool.failed
tool.timeout
knowledge.search.started
knowledge.search.completed
teams.list_chats.started
teams.list_chats.completed
teams.read_messages.started
teams.read_messages.completed
database.query.started
database.query.completed
storage.operation.started
storage.operation.completed
```

### Governance/finalization
```text
knowledge.selection.completed
procedure_action.resolved
command_authority.completed
approval.requested
approval.completed
authority.selected
provenance.completed
command_egress.completed
synthesis.started
synthesis.completed
persistence.started
persistence.completed
sse.started
sse.completed
turn.completed
turn.failed
turn.timeout
turn.cancelled
run.stalled
heartbeat
```

## Status vocabulary

```text
PENDING
RUNNING
COMPLETED
FAILED
TIMEOUT
CANCELLED
STALLED
```

`STALLED` is a watchdog state/event; it does not have to be terminal.

## Trace event schema

Conceptually:

```python
class TraceEvent:
    event_id: str
    trace_id: str
    span_id: str
    parent_span_id: str | None
    run_id: str
    turn_id: str
    session_id: str
    timestamp: datetime
    stage: str
    status: str
    agent: str | None
    tool: str | None
    dependency: str | None
    thread_id: str | None
    duration_ms: float | None
    metadata: dict[str, SafeValue]
    error_code: str | None
```

All metadata passes central redaction/allow-list validation.

## OTel resource attributes

Set once per process:
- `service.name=slopanoc`,
- service version/build commit,
- deployment environment,
- cloud provider/platform/region.

Never put session/run IDs in resource attributes.

## Span attributes

Low/medium cardinality:
- `slopanoc.stage`,
- `slopanoc.agent`,
- `slopanoc.tool`,
- `slopanoc.dependency`,
- `slopanoc.domain`,
- `slopanoc.dialogue_act`,
- `slopanoc.authority_mode`,
- `slopanoc.source_mode`,
- `slopanoc.outcome`,
- `slopanoc.retry_count`,
- `slopanoc.error_code`.

Trace-only high cardinality:
- run/turn/session/thread/case/fault IDs.

Use standard OpenTelemetry HTTP/DB/GenAI conventions where available.

## Model telemetry

Required:
- provider,
- model,
- operation role,
- agent,
- input/output tokens,
- cached tokens where available,
- total latency,
- first-token latency where available,
- retries,
- finish status/reason,
- tool/function-calling mode,
- error class.

Do not store hidden reasoning or full prompts/responses by default.

## Tool telemetry

Required:
- tool,
- owning agent,
- latency,
- attempt,
- outcome,
- timeout/retry,
- safe result count/category.

## Context Broker telemetry

Capture:
- candidates,
- selected/excluded counts,
- selected domains,
- token footprint estimate,
- exclusion reason counts,
- policy/version,
- target thread ID in traces.

Never dump full context payloads.

## Governance telemetry

Capture structured summaries only:
- TurnPlan,
- primary/supporting agents,
- source requirements,
- final authority,
- provenance counts by source class,
- command authority decision,
- approval decision,
- egress kept/removed counts.

## Metric cardinality

Allowed labels:
- environment,
- agent,
- tool,
- dependency,
- model/provider,
- domain,
- status,
- error code,
- operation category.

Never labels:
- run/turn/session/user/chat/case/fault IDs,
- raw error strings,
- prompt text.

## FinOps usage event

Each billable usage event contains:

```text
usage_event_id
timestamp
run_id
turn_id
trace_id
span_id
provider
service
sku/model
operation
agent
domain
quantity
unit
input_tokens
output_tokens
cached_tokens
request_count
retry_count
estimated_cost
currency
price_version
pricing_source
usage_estimated
environment
use_case_key
```

No sampling.

## Versioning

Store:
- telemetry schema version,
- cost schema version,
- pricing schema version.

Breaking changes require migrations or compatibility views.


## Release/configuration correlation attributes

Resource/process:
- `service.version`
- `slopanoc.git_sha`
- `slopanoc.release_id`
- `slopanoc.cloud_run_revision`

Turn/model:
- `slopanoc.model_version`
- `slopanoc.agent_contract_version`
- `slopanoc.prompt_version`
- `slopanoc.tool_schema_version`
- `slopanoc.turn_plan_schema_version`
- `slopanoc.telemetry_schema_version`
- `slopanoc.cost_schema_version`
- `slopanoc.pricing_version`
- `slopanoc.config_version`

High-cardinality values remain trace-only, never metric labels.

## M0 code ownership and compatibility

`backend/observability/` owns the inert version-1 contract. `stages.py` owns the exact
stage/status sets above; `errors.py` implements the codes in doc 04. `attributes.py`
owns release/resource/span keys and metric cardinality. `schemas.py` owns telemetry,
cost and pricing schema version constants (integer 1), event/span/log/run/usage
shapes and Settings/RBAC/effective-config definitions. Breaking changes require
explicit version/compatibility updates; M0 does not activate runtime instrumentation.

Normative telemetry statuses are uppercase. Lowercase status and span-like stage
names in illustrative UI/API examples are projections, not extra vocabulary.
Span operations (such as `session.load`) are separate from lifecycle stages
(`session.load.started`). Existing public SSE codes remain unchanged; explicit
one-way mappings in stages.py document compatibility. `heartbeat` is an internal
stage; future `processing.heartbeat` is an SSE envelope type. No runtime adapter
is installed in M0. Broad legacy perf boundaries are not guessed into fine stages.

SafeValue initially accepts only allowlisted nonnegative integer counts. Unknown,
nested, restricted identifier and free-text metadata is rejected by default.
Restricted identifiers have explicit correlated fields and require sink authorization.
Strings containing query/command/request/error text are not safe merely when bounded.
Metric labels require registered keys and bounded server-owned value sets.

Release/resource conventions include `deployment.environment.name`, `cloud.region`,
`gen_ai.request.model` and `gen_ai.provider.name`, plus the slopanoc keys above.
Per-run IDs never belong to resource metadata. Prompt version also identifies the
system-instruction contract. Values are supplied by trusted deployment/runtime owners
later; absent versions are not fabricated.

The code README defines Settings section IDs/names, future server RBAC, read-only
configuration and migration ownership. FinOps schemas are unsampled accounting
contracts only; estimated/billed values remain separate and corrections append
referenced events. No active registry, ledger, exporters, DB objects or M8 UI exists
as a result of M0.

## M1 compatible foundation extensions

Canonical Stage/RunStatus/ErrorCode sets and telemetry/cost/pricing schema version 1
are unchanged. OperationalEvent in the existing schemas.py owner registers
telemetry.initialized, telemetry.degraded, telemetry.shutdown and telemetry.legacy_log
as infrastructure log/sample operations, not new turn stages. LogRecord message must
match its typed event_name. No raw exception or free-text message accepted.

ResourceMetadata permits slopanoc.telemetry_schema_version="1" as process metadata.
Optional service/release/region/revision attributes are trusted Settings values;
missing local values remain absent. Schema URL 1.40.0 is explicitly selected from
semantic-conventions 0.62b1; API/SDK/OTLP HTTP/proto/common pin 1.41.1. SDK and semantic
schema versions are distinct. No GenAI body/HTTP header/SQL capture is enabled.

The existing ObservabilityConfig gains explicit mode/protocol/queue/batch/export/flush
caps and release metadata. Existing enabled endpoint configs default to OTLP. None/local
modes require no endpoint and are development-only; production enabled mode is OTLP,
with TLS except explicitly declared loopback Cloud Run sidecar. Disabled flags remain
off. No alternate config or environment reader introduced.

EffectiveConfiguration adds typed exporter_mode, collector_endpoint_configured,
service_name, environment and telemetry_schema_version alongside existing fields;
all entries remain read_only=True. Its configuration hash excludes endpoints/secrets.
No protected API or UI is created. Logging/metric export gates revalidate data before
serialization; frozen nested metadata is not treated as safe without validation.

## M2 compatible root-lifecycle extensions

`tracing.Operation` registers span operation names independently of the unchanged
canonical Stage set: `slopanoc.turn`, `request`, `session.load`, `attachments`,
`orchestration`, `planning`, `thread.resolve`, `pending_interaction.resolve`,
`context.select`, `finalization`, `persistence.final_answer`, `sse.complete`.
`tracing.TURN_EVENTS` admits only the applicable M2 lifecycle events; detailed
model/tool/dependency events are not enabled. The existing `agent.team_manager`
stage identifies coarse orchestration progress, not an agent-invocation span.
No new business stage or model/agent/tool/dependency instrumentation is introduced.

The typed span/event projection additionally admits `slopanoc.status` (RunStatus),
`slopanoc.run_id`, `slopanoc.session_id`, `slopanoc.turn_id`, optional thread/fault
identifiers (strict Identifier format), `slopanoc.turn_id_origin` (`adk` or
`execution`) and `slopanoc.config_version` (16 lowercase hex characters).
Restricted identities come from server-owned correlation, never user text/baggage;
field-format validation does not grant access. Unknown attribute/event names, raw
exception events, links, descriptions, tracestate and free-text payloads remain
excluded before queueing. Collector applies the same narrow operation/event/typed
field projection. Arbitrary metadata remains count-only, not a string dictionary.

Before an ADK event exposes this run's invocation ID, internal progress has no
conversational turn ID. Once observed, genuine invocation ID is bound once and
shared across nested tasks. Early phase records join through run/trace identity;
no prior-history fallback is consulted. Terminal runs with no observed invocation
use `pre-adk:<trusted-run-id>` with origin `execution`; this is never a history,
attachment or rewind key. Origin `adk` identifies an actual observed invocation.
Root and terminal events carry final correlation; previously closed early child
spans are not rewritten. Existing strict M0 Correlation records are unchanged;
pre-identity/no-op progress is an explicit internal shape rather than invalid IDs.

LogRecord adds optional paired `turn_id`/`turn_id_origin`. Default structured log
correlation comes from the active observational ContextVar; trace/span come from
current OTel context. Existing message/event/privacy rules are unchanged. Resource
Git SHA/release/revision/environment/schema metadata stays inherited from M1.
Per-turn config_version identifies only the existing effective observability config
hash; it does not pretend to version all business or model configuration.

Execution status, server event emission and client relay are separate. Root status
closes once after background generator cleanup and lock release. Valid clarification,
source gap or policy response can complete; raw provider/runtime exceptions cannot
become successful simply through safe wording. Disconnect is a separate safe log
and local delivery observation, not an implicit backend failure. An exception after
wire completion records SSE_COMPLETION_MISMATCH without duplicate public terminal
emission. Trace/log export failures do not change business execution.

M2 active state is bounded execution-local weak-reference cache plus immutable
Progress snapshots with optional trace/turn IDs, current span/stage, UTC start/stage/
progress/terminal times, monotonic elapsed, bounded timeline, truncation count and
relay state. Completed entries are removed, not retained as support history. M0
ActiveRun projection is available once identity exists. The user-approved M2 scope
places durable Cloud SQL status, authorized APIs and retention in M7; production
cross-instance truth remains mandatory there. No API or UI serializes this cache.
Telemetry/cost/pricing schema versions remain 1: optional log fields and registered
operation/typed attribute extensions are compatible; no persisted schema changed.

## M3 approved model instrumentation extensions

The explicit M3 implementation scope assigns durable ledger/outbox/accounting to
M10. M3 emits ModelUsageObservation metadata; it does not persist UsageEvent or cost
records. Durable accounting ledger: OWNED BY M10 — NOT IMPLEMENTED IN M3 BY DESIGN.

Registered operations are slopanoc.model.operation (INTERNAL), gen_ai.request
(CLIENT per actual transport attempt) and slopanoc.model.validation (INTERNAL linked
validation outcome after transport success). Model lifecycle events are admitted
only on model request/validation spans; M2 root events remain unchanged.
GenAI fields: provider.name (gcp.vertex_ai/gcp.gemini/other), operation.name
(generate_content/embeddings), request.model/response.model (validated bounded model
names), response.finish_reasons (bounded known enums), usage.input_tokens,
usage.output_tokens and usage.cache_read.input_tokens. The exact projection denies
all content attributes/events, free text, links, descriptions, headers and baggage.

SLOPANOC fields include bounded agent/model_operation/workload/status/error enums,
streaming boolean, logical_call_id/attempt_id (UUID), attempt/retry_count (integers),
duration_ms/ttft_ms (finite nonnegative), ttft_boundary=first_provider_output,
ttft_availability and usage_availability, candidate/thought/tool-input/total tokens
and billable_characters (nonnegative counts). Run/session/optional turn/native trace
correlation retains M2 ownership, not nested disposable sessions. System warmup and
image ingestion are separate workload identities without user run correlation.

TTFT observes raw provider output before ADK aggregation; nonstream/embedding/no
output has no measured TTFT. Usage snapshots replace cumulative snapshots, never
sum chunks. Output is candidates+thoughts only when both are known; provider totals
remain authoritative and cached tokens are not added again. Conflicting counts are
partial. Truncated streams retain incomplete counts; failure without usage is unknown.
Embedding statistics and billable characters are optional; vectors are excluded.

ModelUsageObservation carries UTC-aware timestamps, call/submission/attempt/observation
identities, provider/model/response_model, bounded attribution/workload, mode, correlation,
status/error/finish, durations and typed ModelUsage. One injected synchronous sink
receives each finalized physical attempt regardless of sampling/export state.
Deterministic attempt/observation identity is derived from fresh logical/submission
UUIDs; it supports future deduplication but is not process-loss recovery. No default
sink persistence or financial fields. M10 must implement unsampled durable accounting.

Model instruments are slopanoc.model.requests/retries/failures/timeouts/usage_unavailable
counters, gen_ai.client.operation.duration and slopanoc.model.ttft second histograms,
and slopanoc.model.input_tokens/output_tokens/cached_tokens/candidate_tokens histograms.
All use only environment/provider/model/agent/operation/status from fixed registries;
unregistered models map to other. IDs, versions and content never become dimensions.
Histograms/counters are shape-validated and exemplars removed before export. No
cost/ledger/API/UI contract is activated. Telemetry schema remains version 1.

## M4 compatible agent/tool metadata extensions

Registered INTERNAL operations: slopanoc.agent (one actual agent execution) and
slopanoc.tool (one logical invocation, including governance callbacks). Their
lifecycle events are agent.started/completed/failed and tool.started/completed/
failed/timeout. Cancellation remains CANCELLED with TURN_CANCELLED; its completion
event does not imply business success. Approval and governed decision events are
approval.requested/completed, procedure_action.resolved and command_authority.completed.

M4 fields are slopanoc.agent, execution_role (primary/supporting/coordination/system/
unknown), execution_purpose (existing bounded ModelPurpose), tool (closed canonical
registry), tool_category, result_category, execution_disposition (executed/
not_executed/unknown), result_count (nonnegative integer) and timeout_observed
(boolean). Existing trusted M2 correlation/status/error/duration and M3 workload/
attempt fields are reused. Unknown role and ambiguous timeout remain unknown;
policy rejection is distinct from system failure. Telemetry never grants authority.

Tool registry: incident_manager, technical_authority_engineer, problem_manager,
automated_operations_engineer, record_case_analysis, record_conversation_target,
record_source_requirements, teams_list_chats, teams_get_messages,
get_resolved_chat_messages, teams_get_hosted_content, teams_get_all_hosted_content,
teams_get_members, get_current_time_context, teams_propose_create_chat,
teams_propose_send_message, teams_create_chat, teams_send_message, knowledge_search,
knowledge_select_evidence, procedure_action_catalog and ADK-injected set_model_response.
Unregistered names map to other and cannot expand metric cardinality.

Counters: slopanoc.agent.executions/failures and slopanoc.tool.invocations/failures/
timeouts (unit 1). Histograms: slopanoc.agent.duration and slopanoc.tool.duration
(unit s). Agent dimensions are exactly environment/agent/status; tool dimensions
add tool/tool_category. Instruments are independent of trace sampling; exemplars
are stripped. No content, arbitrary tool attributes, dependency spans, durable
records or API/UI contract is activated. Optional allowlisted extensions keep the
telemetry schema at version 1 and require no database migration.


## M5 implemented dependency projection

The finite source-of-truth vocabulary is backend/observability/dependency_contract.py.
Registered CLIENT spans are http.client/db.client/storage.client/secretmanager.client;
INTERNAL groups are db.connection/db.transaction/knowledge.retrieval. Identity fields
remain trace-only. Closed dependency/operation/kind/workload, gateway origin/failure,
HTTP method/status-class/server-defined route, DB system/structural operation,
transaction outcome and acquisition boundary are allowed. Numeric bytes/counts/
attempts and bounded Retry-After are typed. No raw URL/host/query/header/body,
SQL/params/DSN, GCS bucket/key, secret name/value or knowledge content is admitted.
No new lifecycle event is required. Existing model request ownership is unchanged.

Power Automate errors use TOOL_ERROR/TOOL_TIMEOUT without presumed Graph origin.
connection_acquire_duration is an inclusive pool.connect boundary, not pure queue
wait. Session transactions confirm outcome; pre-DBAPI engine events mark requested
outcome only. Hidden SDK retries are unknown. In-memory live child snapshots carry
safe dependency/operation/parent/tool/agent/elapsed metadata only; they do not extend
public schemas, persistence, SSE or the durable cross-instance active-run contract.


## M9 SRE aggregate extension — 2026-10-08

Operational schema revision `b37e90a14c62` follows M7 `9f71c2a64e08` and adds only
`observability_sre_receipt`, `observability_sre_bucket`, `observability_sre_source`.
Canonical receipt schema v1 stores run correlation, bounded environment, SHA-256
owner correlation, lifecycle/deadline timestamps, origin/class enums, scalar timing,
component completeness and transport/receipt flags. No prompts, responses, Teams
bodies, credentials, session state or financial data. Finalized operational
receipts expire after 2 days; exact minute aggregates after 35 days. Aggregate
labels are environment/SLO/minute, never run/user IDs. Updates are transactional,
version-guarded and idempotent for each accepted turn; SSE acknowledgements are sticky.

SRE views use environment, registered operation, registered status and bounded window
only. New aggregate metrics are listed in `backend/observability/slo_metrics.py`;
accepted traffic and root duration are unsampled, and result gauges come from exact
compact rollups. Active-turn gauge is execution-local; durable support lookup remains
M7/Cloud SQL. A missing production source yields an explicit unavailable state.

Histogram contract v2 adds exact 5/8/20/30/45-second cut points to M3/M4/M5 duration
views and new root timing. Existing seconds units and names remain. Manifest records
histogram_contract_version=2. Do not merge historical distributions across the
rollout boundary as if they shared buckets. Formal SLOs use exact event counts,
independent of histogram interpolation; descriptive p95 charts are context only.
