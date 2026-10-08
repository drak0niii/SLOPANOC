# Observability contracts and M1–M3 runtime

This package is inert. It creates no SDK provider, logger handler, background task,
network client, database engine, tables, routes or UI. Contract imports do not
activate it. M1–M3 lifecycle activation is documented below; M4–M19 remain gated.

## Owners and dependency direction

- stages.py: lifecycle event codes, statuses, transition policy, one-way legacy/public projections.
- errors.py: technical codes from telemetry doc 04; never public retry/approval authority.
- attributes.py: resource/span/release correlation and bounded metric-label policy.
- redaction.py: classification and safe default projection for every future sink.
- schemas.py: event/span/log/run/usage contracts, versions, Settings/RBAC/effective-config definitions.
- config.py: pure typed projection; backend/config/settings.py alone supplies the environment mapping.

Producers → contracts/runtime facade → redaction → exporters/repositories.
Contracts depend only on standard Python and existing Pydantic. They never import
application orchestration, agents, tools or evidence. Telemetry never selects
knowledge, grants authority, authorizes commands, bypasses approval or becomes intent.

## Source integration / convergence

| Existing owner | Future integration |
|---|---|
| api/chat_service.py | M2 root trace around canonical execute_turn_events/background execution, including _run_turn_events finalization. No new runner. |
| api/app.py | M1 initialization/shutdown in existing lifespan; retain PostgreSQL validation and warmup ordering. |
| api/turn_context.py | Trusted run ContextVar, never the content bridges. Keep persistent ADK drain Task so attach/detach share context. |
| api/run_trace.py | Sanitized public SSE projection, not active registry. Preserve count allowlist/deduplication. |
| api/activity_queue.py and activity_translator.py | Reuse observed nested tool activity and safe static presentation, not orchestration. |
| api/perf_timing.py | Reuse clocks/boundaries; bridge useful measurements later without duplicate spans/metrics. |
| tools/knowledge/diagnostic_trace.py | OBSERVABILITY ONLY — NEVER EVIDENCE. Project counts/codes before export; bounded query/command/request/title/error text is prohibited by default. Do not export snapshots wholesale. |
| agents/* callbacks | M3/M4 compose with existing callback chains and enforcement; do not replace/reorder guards. |
| context/assembly.py | Counts/token footprint/policy only, no context bodies. |
| gateway/power_automate_client.py | M5 observes actual gateway wait, not invented direct Graph spans. Existing timeout/write safety remains. |
| gateway/safe_error.py and api/errors.py | Preserve lowercase user-safe errors/retryability; technical classification needs actual owning boundary. |
| cases/operations/approval/API pending/history | Preserve actual governance, continuation, provenance, approvals, session persistence and rewind owners. backend/conversation is absent; do not create it to fit a conceptual diagram. |

PUBLIC_PROGRESS maps canonical lifecycle events to existing SSE Stage/label values;
it is not installed into SSE. Terminal COMPLETED → ok; FAILED/TIMEOUT/CANCELLED →
error is a compatibility projection, not a change to current cancellation behavior.
A UI warning is not a failed run. Domain success/error can only be mapped at a known
boundary. Perf session_loaded/model_call_start/model_call_end map exactly; other
marks (case_context_checked, runner_invocation_start, read_continuation_executed,
first_model_event, first_message_delta, generation_complete,
source_reference_enrichment, session_reloaded_for_cleanup) have no lossless stage
mapping. In particular first delta is not provider first-token time. Do not fabricate
TTFT/planning/provenance stages from these broad marks.

## Runtime ownership by milestone

- M1 tracing/metrics/logging/exporters: application → OTLP → Collector → Cloud Trace,
  Cloud Monitoring and Cloud Logging. Inspect ADK SDK/provider configuration first.
- M2 active_runs/turn_trace: execution-local progress/cache only under approved M2
  scope; durable Cloud SQL cross-instance truth belongs to M7. ChatService task handles retain local execution/cancellation
  ownership. ActiveRunRepository is an interface, not a registry implementation.
- M3 model_instrumentation/model_usage: pre-call identity and per-attempt metadata
  handoff. Durable idempotent ledger/outbox is M10 by explicit approved scope. Existing run-keyed pending model slot
  cannot support nested/concurrent calls. Inventory warmup/embedding/remediation calls.
- M4/M5 tool/dependency wrappers: actual children, preserve callback semantics.
- M6 watchdog/errors: deadlines/heartbeat/stall/retry policy. STALLED is nonterminal;
  terminal status cannot reopen. A disconnect need not cancel backend mutation.
- M7 repositories/api_models: summaries, timelines, authorization and retention.
- M8 UI; M9 SRE/SLO; M10 durable accounting/runtime usage; M11–M14 billing/pricing/reconciliation/allocation/
  economics/budgets; M15 security hardening; M16 validation; M17 operationalization;
  M18 IaC/release/CI/W3C; M19 outcomes/profiling. M0 implements none of these runtimes.

## Safety and schemas

Version 1 telemetry/cost/pricing shapes are owned by schemas.py. Breaking changes
require version/migration/compatibility updates. UTC-aware timestamps, nonnegative
counts/finite durations, typed stages/errors and forbid-extra models reject unsafe
unknown fields. Metadata is a flat allowlist of integer counts; booleans/nested
objects/free text are rejected. sanitize_metadata drops unsafe fields; schema
validation rejects them. Restricted IDs belong in explicit correlated record fields,
never arbitrary metadata or metrics. Future richer safe code fields must be registered
and typed before use. Classifications alone do not authorize access. Safe typed core fields are classified
safe but are not accepted as arbitrary metadata. Future sinks must revalidate data
before serialization; frozen models do not make contained dictionaries immutable.

Metric keys AND values must be registered. value_registry comes from bounded
server configuration, never a model, user input or per-run dictionary. High-cardinality
IDs, release/config hashes and raw error strings cannot be labels. ResourceMetadata
rejects per-run attributes. Release attribute constants reserve all doc 22 dimensions;
unknown versions remain absent, never fabricated. Resource values are deployment-owned.

Structured LogRecord.message uses the canonical event name; exception details belong
at one sanitized owning boundary. No logger configuration exists here.

## Configuration

Settings.observability_config lazily projects its existing injected mapping. There
is no second os.environ reader or secret resolver. New flags default false; their
values do not enable runtime behavior in M0. Initial sampling is 100%, always-error
and governed flags true. Later sampling policy changes need versioning and M15 gates.
OTel-enabled config requires observability; OTLP mode requires an explicit endpoint.
The endpoint is SecretStr,
rejects credentials/query/fragment and is never included in effective UI configuration.
Use explicit SLOPANOC_OTEL_ENVIRONMENT for production deployment (development default).

Timeouts, seconds: model 120; Graph falls back to existing request_timeout_seconds
(default 10); knowledge falls back to knowledge_dense_timeout_seconds (existing
validated default); DB/storage 30; heartbeat 5; stall 30. Explicit new settings win.
These are future contract defaults, not new runtime deadlines; warmup remains its
existing 30-second behavior. M6 must review remaining turn budgets and child caps.
Currency EUR; primary price source gcp_detailed_billing_and_pricing; optional project/
dataset identifiers reserve billing integration settings without querying a provider.
All master-contract names are listed in config.SETTING_NAMES with SLOPANOC_ prefix.

Application Settings owns parsing, environment/backend policy supplies effective
runtime values, Terraform owns infrastructure/production deployment config, durable
DB owns run/accounting records, runtime/UI state owns selections/navigation only.
EffectiveConfiguration.read_only is always true in this production contract, including
Terraform and environment ownership. No UI writes or cloud actions are implied.

## Persistence / FinOps

M0 has no ORM models or migrations. Future application observability Base joins
existing Alembic metadata with application-table filtering; never manage/drop ADK
session/event/app/user tables. Reuse configured session/case Cloud SQL URL and injected
async SQLAlchemy patterns, never private ADK engines. Assess pool ownership when
implementing persistence. Execution-local status M2; ledger/outbox M10; durable status/summaries/indexes M7;
financial tables follow their milestones. Verify revision dependency head then.
Do not use create_all as production provisioning. Shared migrations require exact
revision/target/schema/index/lock/data risk/rollback approval.

UsageEvent is a schema, not M10 ledger/cost functionality. Accounting is unsampled,
idempotent, append-oriented and durable; usage_event_id is created deterministically
at the actual billable attempt boundary later and enforced unique in persistence.
Retries with charges are separate events. Immutable correction/reversal references
original event, reason, actor and timestamp; never overwrite financial history.
Estimated and billed cost are distinct nullable Decimal values. Unknown usage stays
unknown or explicitly estimated; native currency retained, EUR reporting. Detailed
Billing + Pricing exports primary, FOCUS supplementary. Trace export/sampling cannot
control accounting coverage; durable outbox recovery, not memory retry, is required.

## Settings / UI / API contract

Navigation owner stays src/components/shell/settings/SettingsModal.tsx, opened by
Sidebar and selected through AppState/types. Future category observability_finops,
label Observability & FinOps, uses existing dialog, secondary panel navigation,
spacing/tokens/Hilda-Inter typography/light-dark colors/cards/focus/scroll semantics.
Existing Usage/Connectors/Skills remain; MOCK_USAGE is quota demo, not accounting.
No frontend files/UI changed in M0. schemas.py encodes nine IDs/names and roles.

Overview: safe health/freshness. Tracing: run lookup/waterfall. Reliability: dependency/
stall/retry state. SLOs: targets/error budgets. FinOps: estimated/billed EUR/native
cost, allocation/economics/budget/forecast. Data & Retention: effective policy.
Integrations: health/freshness without credentials. Access: effective capabilities.
Diagnostics: authorized run/status/stage/agent/tool/dependency/elapsed/last progress/
timeline/errors/permitted cost. No fake measurements for unimplemented sections.

M7 future read APIs: /api/observability/runs/{run_id}, its /timeline,
/api/observability/sessions/{session_id}/runs, /active, /health. Separate financial
APIs. Server authentication and field/query authorization are mandatory; current dev
user header is not production identity. ROLE_PERMISSIONS is a capability ceiling,
not an authentication implementation, role inheritance or route authorization.
FinOps has financial data only, never automatic raw Teams/conversation/case access.
No role in these contracts grants raw content. User progress must be ownership-scoped;
auditor summaries policy-scoped; financial scope requires explicit server checks.

Effective config DTO reports safe value, owner, read_only, config_version; no secrets.
Run/summary/timeline schemas support future data projections, never direct unrestricted
serialization to clients. Normal chat retains existing sanitized trace/citations and
safe static labels, optional Details/run ID; no reasoning. M8 builds UI, M0 only
establishes this contract. External mutations require the AGENTS exact-operation gate.

## M1 runtime foundation (locally activated; no turn instrumentation)

The contract modules and package import remain inert. The existing FastAPI lifespan
now acquires runtime.py after the mandatory database gate, before unchanged warmup,
and closes its lease in finally. tracer/meter/logger providers are explicit, process
owned and installed deliberately once. Identical active leases share the provider;
conflicting/foreign/terminal providers require a process restart. No private OTel
global reset, ADK bootstrap or direct Google exporter. Factory Runtime accepts private
providers/exporters for tests; global/lifespan ownership tests use subprocesses.

Disabled flags retain application behavior with no providers/readers/exporters/log
handlers. enabled exporter_mode=none supplies SDK APIs without network; local uses
bounded sanitized in-memory sinks; otlp uses explicit HTTP/protobuf endpoints and
finite exporter timeouts. Production enabled mode requires OTLP; HTTP is permitted
only on explicitly declared Cloud Run sidecar loopback, otherwise TLS. No proxy,
auth-header, client-cert, resource-detector or cloud metadata environment is adopted
implicitly by the application adapter. Settings alone owns environment mapping and
normalizes K_REVISION; metadata absent locally is omitted. No baggage imported.

tracing.span and logging.emit are M1 metadata-only primitives, not chat/model/agent
instrumentation. Scope slopanoc.observability and canonical event names alone export.
Foreign ADK spans/logs are rejected before queues, owned spans lose all raw exception
events, links and status descriptions, and custom attributes go through redaction.py.
Logs revalidate the strict LogRecord body before queueing and require matching trusted
service/environment. Run ID is an explicit trusted argument, not derived from raw
messages. The existing run ContextVar can be supplied later without a new registry.

Fixed health gauges have only registered environment/operation labels. SDK views
remove foreign instruments; the metric export gate checks fixed keys/values and
strips raw exemplar attributes. MetricResource fields remain resource metadata, not
promoted as user/run/release metric labels. Export failures/queue drops/cleanup failures
are observable in Health and bounded local JSON fallback even when metrics are down.
No fallback re-enters logging/export queues. Local health is execution-local telemetry
state, never the authoritative cross-instance active-run store.

Trace/log queues are finite, nonblocking, batched daemon workers. SDK OTLP retries
are capped by exporter timeout; no second application retry loop. Total flush/close
shares a monotonic deadline; unsupported/hanging SDK shutdown APIs run on daemon
cleanup with no atexit/executor join. Loss after a deadline is explicit; in-flight
remote delivery remains unknown rather than falsely marked confirmed. Local sinks
are bounded capture rings for development only, not durable history/accounting.

Production safe JSON formatter handles existing application/root/Uvicorn/ADK handlers
and restores only its own changes on lease release. It never evaluates raw messages,
args, exceptions or arbitrary extras. Legacy messages become telemetry.legacy_log;
producer-specific safe logging convergence is later work. Existing content producers
are not edited. No duplicate raw exception logs added. Host-installed/new handlers
must adopt this formatter before production traffic; deployment review must prove
there are no extra unformatted destinations. Foundation operational logs use OTLP;
legacy structured stdout uses the existing Cloud Run logging path pending later
producer convergence. Do not blanket-exclude stdout or duplicate OTLP records.

M1 pins API/SDK/HTTP exporter/proto/common 1.41.1 and semconv 0.62b1 in requirements.txt,
matching ADK 1.33.0 constraints. The semconv schema URL is 1.40.0 from the pinned
Schemas.V1_40_0, not inferred from SDK version. No auto-instrumentation dependency
or prompt/completion recording enabled. Future semconv upgrades require changelog,
contract compatibility, dashboard/query review, privacy/lifecycle tests and dependency
resolution review. M0 gen_ai.provider.name is preserved; ADK gen_ai.system is not
silently added to exports. Telemetry/cost/pricing event version remains 1.

M1 source-controlled Collector configurations and version pin live in
infra/observability/collector. Provider-free Terraform outputs describe sidecar
integration without adopting an unknown production service or creating resources.
No database migrations, cloud mutations, billing changes, M2+ functions or UI.
Effective Settings entries are typed, read-only, hashed safe values; expose endpoint
configured boolean only. No health/config HTTP routes or frontend authorization.

Export records also normalize instrumentation scope/version/schema attributes and
strip tracestate. Metric descriptions/units are registered static values, never raw
SDK strings. Collector defense-in-depth clears scope attributes/version and
tracestate and uses fixed health metric descriptions/units before batching.

## M2 root-turn runtime

ChatService.execute_turn_events starts one slopanoc.turn without attaching context
across relay yields. Execution task registration precedes run.started. The existing
_drive attaches the root/observational ContextVar while holding the real session
lock and driving _run_turn_events to exhaustion, including finalization/persistence.
Outer finally ends the root after generator cleanup and lock release; task callback
backstop handles pre-start cancellation and unexpected terminalization escape.
finish is idempotent; callback does not overwrite a specific/finished result.

Only observed request/session/attachments/coarse orchestration/finalization/
persistence/server-delivery phases are added. Pure decorators observe existing
server route evaluation, fault-thread resolution and broker assembly. No standalone
TurnPlan/TurnAuthority object is invented; no specialist/tool/model/dependency spans.
Conditional stages appear only on actual calls. Existing perf timings, public
RunTraceRecorder/dedup/SSE output, evidence run binding and knowledge diagnostics
remain owned by their existing modules. Telemetry never participates in governance.

turn_trace owns technical terminal state, ActiveRuns is a weak-reference bounded
execution cache, and run_trace remains public projection. The cache is not a durable
registry or protected API. Progress snapshots expose IDs, stage/current span,
elapsed/last-progress/timeline/truncation and independent relay status for future
M7/M8 contracts. No Settings API/UI or cross-instance support capability is added.

Late ADK invocation ID is bound once; missing pre-ADK identity stays unknown until
terminal execution-only fallback (pre-adk:<run_id>, origin execution). Never use
prior session-history IDs for telemetry. Native trace identity remains separate
from trusted support run identity. Context tokens reset in their attaching task;
shared state refuses updates after terminality. Actual runtime events update last
progress without recording content or per-token event history. No watchdog/heartbeat.

Python and both Collector configs explicitly admit M2 registered operations and
safe canonical lifecycle events with typed IDs/status/origin/config hash. Raw events,
links, exceptions, content and free-text metadata remain denied. Structured JSON
logs derive run/late-turn correlation from observational context through early
session work AND finalization, without extending the evidence mailbox lifetime.
Resource release metadata inherits M1; config_version is observability-config hash.

M2 tests use injected private providers/fake Runner/SQLite, with guarded loopback
Collector export. Live GCP, durable registry, browser W3C/receive/render, model/agent/
tool/dependency details, ledger and production performance remain later milestones.

## M3 model instrumentation

model_adapter.ObservedModel delegates the public ADK BaseLlm API to the unchanged
cached provider. Each agent/clone has immutable bounded attribution; explicit
model_activity scopes refine existing classification, synthesis and repair calls.
No callbacks are added, replaced or reordered. Earlier synthetic callback responses
produce no provider requests. Context is restored before yielding to the consumer.
Warmup and image ingestion detach from user turns; embeddings inherit an active
turn when appropriate. No agent/tool/dependency invocation instrumentation is added.

model_provider is the sole PRIVATE SDK compatibility boundary. Exact ADK 1.33.0 and
GenAI 1.75.0 versions and signatures are checked before per-instance interception of
_async_request/_async_request_once, httpx send and aiohttp request. The physical seam
also counts aiohttp's internal transport retry. Shared client pools, options, auth,
SDK retry/backoff and returned responses remain unchanged. Unsupported shapes report
safe health degradation and delegate execution, but cannot pass coverage acceptance.
No global SDK patch or auto-instrumentor is installed. Model capture environment
switches are forced false before runtime/model use, even with exporters disabled.

Each logical call owns slopanoc.model.operation; each actual transport submission
owns CLIENT gen_ai.request. Parentage uses the current exported M2 phase/root rather
than filtered ADK parents. Fresh UUIDs identify logical calls and SDK submissions;
attempt and observation IDs are deterministic within that call/submission, independent
of trace sampling. IDs are telemetry correlation, never evidence or authority.
Every attempt closes on success/error/cancellation/abandonment. Post-transport
structured validation records a separate linked slopanoc.model.validation span;
it does not rewrite successful usage or create another provider accounting event.

TTFT means request-start to first raw nonthought output event, explicitly labeled
first_provider_output. Usage-only/empty/thought packets do not set it; function-call
presence can qualify without retaining arguments. No nonstream/embedding/completion
latency is substituted for unavailable TTFT. Final cumulative usage snapshots replace
earlier ones. Output counts combine candidates and thoughts only when both are
reported; cached input remains a subset. Missing, conflicting and truncated usage
is unknown/partial, never fabricated zero. Embeddings retain only available statistics
and billable characters, never text/vectors. Error codes are bounded taxonomy values.

model_metrics owns fixed instruments and six dimensions: environment/provider/model/
agent/operation/status. The finite static model registry maps unlisted models to
other; response versions and IDs never expand it. Duration and TTFT use seconds;
token quantities use {token}. Export gates validate counters/histograms and discard
exemplars. Both Collector configs independently project the same exact metadata.

ModelUsageObservation is an immutable UTC-aware metadata contract. One synchronous
injected UsageObservationSink receives each finalized actual attempt irrespective
of sampling/disabled tracing/export outage. No default durable sink or queue is
implemented. Sink failure is isolated and health-reported, not retried or represented
as accounting success. M10 must supply durable idempotency, process-loss recovery,
outbox and correction semantics. Durable accounting ledger:
OWNED BY M10 — NOT IMPLEMENTED IN M3 BY DESIGN. No pricing, cost estimates or billing.

No Settings/API/UI changes: safe model metadata supports future M7/M8 protected
projections; RBAC and financial access remain server responsibilities. Effective
capture/tracing configuration remains read-only backend/environment/IaC policy.
Local fake-transport and Collector tests do not claim live-cloud/runtime deployment.

## M4 agent and logical tool instrumentation

agent_instrumentation.Execution is the canonical observational descriptor, nested
in M3's attribution context and tied to the trusted M2 turn. ObservedAgent wraps
one actual run_async execution. Context attaches only while driving next/close,
restores before yielding and rejects closed descriptors in copied late tasks.
Agent copies/clones retain the subclass. Canonical ownership comes from M3 model
attribution; server-forced routing may mark primary, Team Manager is coordination,
ingestion is system, and unavailable specialist classification stays unknown.

adk_adapter is the sole M4 private compatibility boundary. It verifies ADK 1.33.0,
four reviewed source fingerprints and TelemetryContext.function_response_event,
then binds original ADK dispatch bytecode to isolated per-flow globals. Its local
telemetry facade wraps before/body/error/after tool processing and delegates original
tool execution exactly once. It does not modify installed modules, classes, callbacks
or package source. Ordinary ADK Agent remains unchanged. Unsupported shape degrades
telemetry and executes the original flow; coverage acceptance then fails.

tool_instrumentation owns 22 canonical tool names, including ADK's real injected
set_model_response. Direct adapters cover deterministic Teams prefetch, authoritative
member retrieval and server-governed knowledge search without wrapping shared
callables twice. Before-tool blocking records not_executed; returned policy rejection
remains a completed application outcome. Genuine exceptions, timeouts and cancellation
retain original propagation; collapsed gateway errors cannot establish a timeout.
Result projection reads only registered scalar status/code and known list lengths.
Approval service and authority boundaries emit safe lifecycle events with no human
wait span, sensitive text or decision feedback into business policy.

M3 model_parent first resolves a live M4 descriptor; otherwise existing M2 phase/root
fallback remains. A nested delegation reads Turn → Agent → Tool → Specialist Agent
→ Model Operation → Request. Native ADK helper spans are still filtered. M5 can use
the attached native tool context or active_execution; no dependency children exist.
RunTraceRecorder, PerfTimer and knowledge diagnostic_trace remain preserved;
diagnostic_trace is OBSERVABILITY ONLY — NEVER EVIDENCE.

execution_metrics records unsampled counters and second histograms using fixed
environment/agent/status and optional tool/tool_category dimensions. IDs and content
never become labels. Python and both Collector gates independently validate the
exact fields, numeric shapes and units and strip exemplars. Telemetry failure never
retries a business invocation. Future authorized APIs/UI can project sequence,
duration, outcome and current agent/tool; M4 adds no API, persistence or UI and
effective production configuration remains read-only.


## M5 dependency instrumentation (local implementation)

The concrete client inventory is `dependency_instrumentation.CLIENT_OWNERS` and
`test_observability_dependency_coverage.py` rejects unassigned SDK imports/sinks.
M1 owns OTLP export; M3 owns every model/embedding request. M5 observes the actual
Power Automate POST, four SQLAlchemy engine owners, two GCS object stores and
Secret Manager cache misses. There is no direct Graph, BigQuery, Redis or remote
vector client in the current runtime. Local retrieval stages are INTERNAL groups.

Native parentage is Turn → Agent → Tool → Dependency, with M2 phase/root fallback.
Knowledge stages group actual DB and existing M3 embedding children, without a
second provider wrapper or usage observation. ContextVar parentage and bounded
execution-local live children support concurrent waits, restoration and elapsed
metadata. These handles are neither durable status nor public diagnostic APIs.
A cancelled asyncio.to_thread await cannot abort an SDK call already running;
its span ends when the worker finishes. Terminal turns clear live handles.

Gateway HTTP status describes Power Automate, with TOOL_ERROR/TOOL_TIMEOUT and
explicit gateway_http/gateway_transport origin. It never fabricates GRAPH_*.
Routes derive exclusively from the closed operation registry; raw URLs, hostnames,
queries, headers, request/response bodies and exception text never enter projection.
429/typed timeout and bounded Retry-After are facts; downstream Graph status,
auth and hidden retries remain unknown. Pagination counts actual getMessages POSTs.

Four DB owners use instance-owned public pool classes/events and session-factory
subclasses. Every cursor execution has one db.client span; textual SQL is classified
other, never parsed/exported. connection_acquire_duration includes pool queue wait,
connection creation and pre_ping; it is not a pure queue metric. COMMIT/ROLLBACK
session spans confirm completion; bare engine events explicitly report only
commit_requested/rollback_requested. Supported pool gauges aggregate by DB owner;
unsupported StaticPool gauges are absent. Existing read retry count is observed
without changing retry policy. No global client patch or SQL/DSN/parameter export.

GCS upload/download/exists/delete and Secret Manager access_secret_version wrappers
invoke originals once. URI/bucket/blob construction and secret cache hits emit no
remote span. Bytes are counts; bucket/object/secret names and contents are excluded.
Hidden SDK retries remain unknown. Retrieval observes metadata/applicability/sparse/
dense/fusion, no-result and dense fallback without changing evidence or ranking.
Telemetry setup/finish/log/metric/export failures are guarded independently from
business calls and cannot replay writes or commit transactions.

Dependency counters and seconds histograms use environment/dependency/operation/
status only; pool gauges use environment/dependency. Storage byte counters use By.
No IDs, routes, hosts or arbitrary fields become labels. Python and both Collector
configs enforce the finite vocabulary independently. Application export strips
exemplars. Infrastructure collection/dashboards and reliability policy remain later
milestones; production deployment has not occurred.

## M6 reliability controls

Reliability runs independently of telemetry enablement. Settings owns typed,
**provisional** hard caps; these are not approved production latency SLOs. A turn
has one monotonic total deadline (180s) with a 5s cleanup reserve inside it. Child
budgets use `min(parent.absolute, monotonic_now + category_cap)` and iterators keep
one budget for their whole lifetime. Timeout causes use existing canonical codes.

The auxiliary watchdog observes material phase/agent/tool/dependency transitions
and non-thought provider output. Timer ticks/export/transport heartbeats do not
count. STALLED is nonterminal and resumes on real progress. Independent business
and cleanup timers retain enforcement if observation fails. M2 alone finalizes
one root/terminal result after lock-release responsibility.

Synchronous Teams and attachment execution uses bounded daemon workers (8 running,
16 pending). Timeout stops the caller; it cannot kill a Python thread. Capacity
stays occupied until actual completion. Tool workers receive private state;
message/hosted-content/activity updates are deferred until a timely owner commits.
Late results are discarded and recorded with detached safe metadata. Ambiguous
write outcomes are OUTCOME_UNKNOWN; there is no automatic gateway/action replay.

Business transport has count (256) and approximate serialized byte (4MiB) limits,
explicit oversize/backpressure failure, and separate completion control. Ordinary
generator failures follow already-produced events. ASGI send is bounded; SSE
comment heartbeats provide liveness without business progress. Disconnect detaches
delivery while the existing independently owned backend driver can continue.

Default caps in seconds: model 120; agent/orchestration 150; tool/planning/storage
30; whole knowledge 15 (dense still 10); database acquire 5/query 15; session load,
session lock, gateway, secret, persistence and ASGI send 10; queue admission 1.
All are configurable through existing SLOPANOC Settings. No M6 durable records,
API, UI, deployment, SLO evaluation or financial accounting is provided.

Caller deadlines and SDK transport limits do not prove remote server cancellation.
Database/write confirmation may remain unknown. CPU-bound code cannot be safely
preempted by asyncio, and already-running synchronous workers retain bounded
capacity until they return. No unsafe thread termination is used.


## M7 durable diagnostic projection and read APIs

M2 TurnTrace remains canonical lifecycle; M6 remains watchdog/deadline owner.
A single Publisher emits strict immutable RunState/DiagnosticEvent projections
into the process-owned Coordinator. Database records are DIAGNOSTIC ONLY — NEVER
AUTHORITY / NEVER EVIDENCE. Agents/tools never write/query this repository.

`SLOPANOC_PROJECTION_ENABLED` defaults false and is independent of OTEL flags/trace
sampling. All `PROJECTION_*` policy lives in existing ObservabilityConfig/Settings;
config API exposes effective values read-only. Enabled normal app startup still
requires PostgreSQL and uses existing session/case Cloud SQL URL resolver. Dedicated
pool has two connections, no overflow; engine construction does not connect or
create schema. Apply additive revision `9f71c2a64e08` after `7c4e9a2d1f05` only under
operation-specific approval for shared environments. No automatic startup DDL.

Tables: observability_run_status; observability_turn (terminal operational summary);
observability_run_event (maximum 128 safe significant events per run). Identifiers,
UTC instants, measured elapsed offsets, canonical enums, safe registered work names
and trusted release/config versions only. Dependency JSON is strict DependencyState
objects, never an extensible metadata bag. No prompts/responses/content/commands/
tool payloads/SQL/URLs/credentials/reasoning or accounting fields. Session correlation
is omitted until existing facade lookup confirms it; detach_session permanently
suppresses later reattachment. Rewind does not rewrite diagnostics.

Default coordinator capacity 2048 admitted runs, including already-written active
runs with terminal capacity reserved. At most one coalesced pending state per run,
128 cumulative timeline events and one detached pending wakeup; no task-per-update.
Normal writes spaced at least 1s per run, periodic checkpoint 15s, independent from
M6 wakeups and progress. Terminal envelopes outrank normal state; up to 3 attempts
within 5s, per attempt 1s. Shutdown uses one 5s process budget including disposal;
no DB await added to turn cleanup. Saturation/admission loss/unpersisted terminals
are explicit bounded local health counters with safe rate-limited JSON fallback.
Health is process-local plus authorized durable stale count; no new Collector
attributes or metrics. These defaults are provisional tuning, not production SLOs.

Run versions increase under canonical turn lock. Repository uses owner/version/
nonterminal conditional writes and atomic terminal summary/event persistence.
First terminal facts cannot change; post-terminal observations may update only
cleanup/delivery/version/truncation, preserving existing terminal timeline. Native
late worker events cannot reopen a terminal run. No usage/cost outbox here.
**Unacknowledged asynchronous projection updates may be lost if the process dies
before persistence.** Priority/retries are not accounting-grade crash durability.

Read-time STALE is separate from RunStatus: checkpoint older than 60s plus 5s clock
grace, or total UTC deadline plus grace. True outcome remains unknown; no fake
FAILED/TIMEOUT, business resumption or affinity/cache fallback. UTC deadline
translation uses one paired reference; STALE elapsed freezes at last observation.
New process incarnation cannot update an older producer's row.

Timeline preserves start/latest critical episodes/terminal/cleanup/delivery,
compacts older ordinary transitions and reports truncation. Retention is independent
from Cloud Trace: terminal current-state cache 24h; verified ordinary completed
turn summary/timeline 30d; exceptional/stalled/operational/unknown 90d. Classification
uses only observed registered server stage/agent/tool/dependency facts. Bounded
cleanup method needs a separately approved production scheduling policy; none is
provisioned. Summary/timeline survive short current-state TTL; stale rows expire
after 90d relative to deadline/checkpoint without synthetic terminalization.

GET /api/observability/runs; /active; /runs/{run_id}; /runs/{run_id}/timeline;
/sessions/{session_id}/runs; /health; /config. Explicit DTOs, 50 default/100 maximum
page, fixed keyset ordering/filter-scoped bounded cursors, validated enums/UTC
windows, four read slots/2s query budget and bounded 60/min principal throttle.
Operator: scoped operational fields; Developer/SRE: deeper technical correlations;
Admin: operational plus effective configuration. Existing role capability ceilings
remain authoritative; User/FinOps/Auditor get no implicit technical permission.

All routes **deny by default**. verified_principal_provider returns None. Current
X-SLOPANOC-DEV-USER, headers, environment flags and frontend claims cannot activate
it. Tests alone use explicit FastAPI dependency overrides. A future actual verified
provider/role mapping needs separate production integration and validation; local
role fixtures do not prove production identity. Safe static errors, environment/
field scope, no trace URLs and no ORM auto-serialization. Existing /health liveness
remains independent. No M8 frontend, M9 SLO engine, M10 ledger or cloud deployment.


## M9 local SRE layer

Canonical registry/evaluator: `slo_contract.py` / `slo_evaluator.py`. The M7 protected
API gains `/api/observability/slos`, `/slos/{slo_id}` and an authenticated diagnostic
`POST /sse-receipts`. Production verified identity and environment scope are mandatory;
no identity-header fallback is added. Existing Settings SLO cards consume backend DTOs.
Policy/configuration remains view-only; objectives and alerts are provisional.

`SLOPANOC_SLO_ENABLED` defaults false. With existing M7 projection/database enabled,
the application can attach the bounded best-effort SRE writer. Production activation
requires approved additive migration `b37e90a14c62`, M7 prerequisites, verified identity
and deployments. Startup never creates tables or applies migrations. Operational
receipts/buckets are compact SRE data, not the M10 financial ledger. Lost writes latch
coverage failure; a clean producer and a new full window restore usable coverage.
Heartbeat gaps require a new continuous 28-day window. The rolling endpoint watermark
lags by configured maximum turn timeout plus 120 seconds, rounded down to a UTC minute.

Generate local alert/dashboard JSON with `.venv/bin/python -m backend.observability.slo_iac`.
All policies are disabled, channels unset, one PromQL condition per logical alert.
Cloud metric names, ingestion, platform inputs and noise are not live-validated.
`CloudMonitoringProvider` is an unavailable future read-only seam, not a production client.
Safe synthetic adapters are explicit and disabled by default; no remote scheduler
or tenant discovery is installed. Direct Graph and cost-ledger sources remain unavailable.

## M10 runtime usage accounting

`finops/` owns durable runtime quantities only; M3 remains the sole provider usage
extractor, and M9 owns completeness formulas. Enable `SLOPANOC_FINOPS_ENABLED` only
with the additive accounting migration present. The sole lifespan installs accounting
before warmup, independently of OTel, M7 projection and SLO flags. No startup DDL.
Production verified identity remains required/default-denied for protected reads.

Admission and STARTED commit in separate short transactions before physical transport
entry. ADMITTED is not metered. STARTED records dispatch entry, not proof that the
remote provider received/billed a request. A crash immediately after the STARTED commit
is conservatively an unresolved obligation, never silently complete. Final M3 metadata
is committed to the inbox before materialization; recovery replays only that safe
payload. No DB transaction spans provider I/O. Unrecoverable pre-inbox quantity loss
remains visible debt; it is never reconstructed by provider/business replay.

One immutable BASE per environment/attempt, deterministic versioned UUIDv5 identity,
DB partial unique index, transactional duplicate/hash-conflict handling. Quantity
corrections append referenced ADJUSTMENT rows. Null is unknown, explicit zero is zero;
provider totals and cached/candidate/thought components retain M3 semantics. Aggregates
show observed sums with known/unknown counts and separate quantity coverage. No money,
pricing, invoice, FX, allocation or billing integration exists.

Operational bounds (environment-owned, read-only): admission+STARTED total2s; final
capture total2s plus failure-state update at most250ms;3write attempts;8write slots;
250ms admission wait;100record recovery batch;2s recovery pass;5recovery attempts;
30s checkpoint;3s shutdown. PostgreSQL's transaction row lock is the short materializer
lease (`FOR UPDATE SKIP LOCKED`); a crashed transaction releases it automatically.
Recovery never holds locks across provider work. Exhausted work remains visible;
there is no HTTP retry/replay mutation endpoint. Runtime ledger retention target24
calendar months; no destructive retention execution in M10, pending debt/corrections
preserved for future reconciliation. No session-deletion cascade.

Financial reads: `/api/observability/finops/runtime-usage`, `/ledger-health`,
`/ledger-completeness`; verified FINANCIAL_DATA capability plus environment scope.
Existing FinOps ceiling grants financial data only; SRE/Admin do not automatically
gain it. General M9 operational reads may show content-free accounting completeness.
Runtime usage queries cap31days/100groups/page/1000groups traversal with bounded query
concurrency/deadline. UI displays usage and health in existing Settings; later cards
stay unavailable and effective policy is read-only. Accounting metrics use fixed
names and environment/operation/status/window labels only, no identifiers/exemplars.
