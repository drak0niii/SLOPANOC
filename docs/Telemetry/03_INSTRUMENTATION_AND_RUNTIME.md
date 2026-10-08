# 03 — Instrumentation and Runtime

## Decision

OpenTelemetry is the canonical runtime instrumentation layer for traces, metrics and log correlation. SLOPANOC may wrap OTel for safe naming/redaction, but must not create a proprietary parallel tracing system.

## Initialization

Initialize once in FastAPI lifespan:
- tracer provider,
- meter provider,
- propagators,
- batch processors/exporters,
- resource attributes,
- log correlation,
- local/no-op mode.

Shutdown must flush with a bounded deadline.

## Application wrapper API

Provide helpers similar to:

```python
start_turn_trace(...)
span(name, **safe_attrs)
async_span(name, **safe_attrs)
event(name, **safe_attrs)
record_exception(...)
current_trace_ids()
metric_counter(...)
metric_histogram(...)
```

Wrappers must enforce naming, attach correlation IDs, pass metadata through redaction and update the active-run registry.

## Root trace

Instrument `backend/api/chat_service.py` first. The root span should be stable, e.g. `slopanoc.turn`.

Mandatory root phases:
1. request accepted,
2. session load,
3. attachments,
4. TurnPlan planning/server plan,
5. thread/pending interaction,
6. Context Broker,
7. agent execution,
8. tools/dependencies,
9. authority/provenance,
10. command egress,
11. final persistence,
12. SSE completion,
13. terminal cleanup.

Root span closes in `finally` even on failure.

## Active run registry

Conceptual model:

```python
class ActiveRun:
    run_id: str
    trace_id: str
    turn_id: str
    session_id: str
    status: str
    current_stage: str
    current_span_id: str | None
    current_agent: str | None
    current_tool: str | None
    current_dependency: str | None
    started_at: datetime
    stage_started_at: datetime
    last_progress_at: datetime
```

Operations:
- register,
- update stage,
- heartbeat,
- mark terminal,
- cleanup/TTL.

No raw prompt storage.

## GenAI/model spans

Every provider model invocation is one child span.

Record:
- provider/model,
- agent,
- role (`planning`, `specialist`, `synthesis`, etc.),
- input/output/cached tokens,
- TTFT,
- total latency,
- retries,
- finish reason,
- tool-calling mode,
- success/error.

Every model call also writes one usage-ledger event.

If token counts are estimated rather than provider-reported, mark `usage_estimated=true`.

## Agents

Canonical agents:
- `team_manager`,
- `technical_authority_engineer`,
- `incident_manager`,
- `problem_manager`,
- `automated_operations_engineer`.

Each invocation records:
- TurnPlan role (`primary`/`supporting`),
- start/end,
- outcome,
- child model/tool spans,
- contribution category,
- error code.

Never record chain-of-thought.

## Tools

Use centralized ADK tool callbacks/wrappers wherever possible.

Record:
- tool name,
- owning agent,
- attempt,
- duration,
- outcome,
- safe result category/count,
- timeout/retry.

Retries must not be invisible because they affect both latency and cost.

## Dependencies

### Microsoft Graph / Teams
Child spans for:
- list/resolve chats,
- pagination,
- read messages,
- attachment metadata where relevant.

Metrics:
- latency,
- HTTP/provider error,
- auth/rate limit,
- pages,
- counts,
- timeout.

Never store message bodies or bearer tokens.

### Knowledge
Keep `backend/tools/knowledge/diagnostic_trace.py` and bridge into OTel.

Trace:
- query preparation,
- sparse retrieval,
- dense/vector retrieval,
- fusion/ranking,
- applicability,
- selection.

Capture counts/scores/identities permitted by existing policy, not document bodies.

### Database
Instrument:
- connection acquisition/pool wait,
- query duration,
- transaction errors/timeouts.

Do not capture sensitive SQL parameters.

### Storage
Instrument object operations, bytes, latency and errors. Do not record signed URLs or credentials.

## Context Broker

One `context.selection` span with:
- candidates,
- selected/excluded counts,
- domains,
- token estimate,
- exclusion reason counts,
- policy version.

## Governance

Instrument, but never influence:
- TurnPlan,
- source requirements,
- knowledge selection,
- ProcedureAction,
- Command Authority,
- approval,
- TurnAuthority,
- provenance,
- command egress.

## Browser correlation

SSE progress messages include safe `run_id`, stage and elapsed time. Browser telemetry should record:
- prompt submitted,
- SSE connected,
- first event,
- first token,
- message completed received,
- rendered,
- SSE disconnected.

This distinguishes backend completion from frontend spinner failures.

## Sampling

Initial launch may retain 100% traces while volume permits.

Always retain errors/timeouts/stalled and governed operational turns initially.

Never sample cost usage records or aggregate metrics.


## Browser/backend trace propagation

Use W3C Trace Context:
- `traceparent`,
- optional `tracestate`.

Do not put user/session/customer data in OpenTelemetry baggage.

The backend generates/validates trusted `run_id` independently.

## Semantic-convention versioning

Pin OpenTelemetry SDK and semantic-convention versions.
No silent upgrade is permitted.
