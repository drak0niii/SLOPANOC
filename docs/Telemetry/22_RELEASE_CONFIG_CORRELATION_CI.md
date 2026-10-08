
# 22 — Release, Configuration Correlation and CI Enforcement

## Production decision

Every trace must be attributable to the exact software/configuration that produced it.

## Required release attributes

Per process/resource:
- service version,
- Git SHA,
- deployment/release ID,
- Cloud Run revision,
- environment,
- region.

Per turn where applicable:
- model name/version,
- agent contract version,
- prompt/system-instruction version,
- tool schema version,
- TurnPlan schema version,
- telemetry schema version,
- cost schema version,
- pricing version,
- feature/config version.

## Version strategy

Prompt/agent/tool contracts require explicit version identifiers when changes can affect:
- quality,
- routing,
- tool calling,
- safety,
- cost,
- latency.

A hash of normalized configuration is acceptable where semantic versioning is impractical.

## Change-correlation dashboards

Support:
- latency by deployment,
- failures by Git SHA,
- cost by model/prompt version,
- quality by release,
- planning failure by tool-schema version.

## W3C Trace Context

Browser -> backend propagation uses W3C Trace Context:
- `traceparent`,
- `tracestate` where needed.

Do not put user/session/customer secrets in baggage.

The backend remains authoritative for `run_id`.

## CI observability gates

CI fails for:

### Unknown telemetry vocabulary
- new unregistered stage/status/error code.

### Cardinality violations
- run/session/user/chat/case/fault IDs used as metric labels.

### Redaction violations
- prohibited field names,
- fake-secret fixtures appearing in exporter payloads.

### Span lifecycle
- known execution path leaves unclosed spans.

### Model usage accounting
- instrumented billable model call lacks usage-ledger path.

### Schema/version discipline
- telemetry schema changed without version/migration;
- price schema changed without compatibility update.

## Deployment annotations

Every deployment emits a dashboard/trace marker containing:
- release ID,
- commit,
- timestamp,
- environment.

## Rollback analysis

Runbooks support:
- compare pre/post release latency,
- compare pre/post model cost,
- compare error/SLO burn,
- compare quality/outcome metrics.
