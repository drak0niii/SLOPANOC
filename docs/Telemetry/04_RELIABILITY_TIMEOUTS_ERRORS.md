# 04 — Reliability: Timeouts, Watchdogs and Errors

## Principle

A request may be slow or fail; it must never be operationally unknowable.

## Deadlines

All potentially blocking boundaries use configurable deadlines:
- model provider,
- Microsoft Graph,
- knowledge retrieval,
- database,
- object storage,
- generic external HTTP,
- telemetry/cost persistence.

Use a turn-level remaining budget plus child caps so sequential child calls cannot each consume a full global timeout.

## Watchdog

Track:
- current stage,
- stage start,
- last progress,
- elapsed.

If no progress exceeds configured threshold, emit `run.stalled` and increment stalled metrics.

A stall is observable before timeout and may later complete.

## Heartbeats

During long SSE turns emit a safe heartbeat periodically:

```json
{
  "type": "processing.heartbeat",
  "run_id": "...",
  "stage": "teams.read_messages",
  "elapsed_ms": 20014
}
```

This differentiates backend-alive from connection-broken.

## Error taxonomy

Stable codes, not raw exception strings.

### Planning
- `PLANNING_TIMEOUT`
- `PLANNING_INVALID`
- `PLANNING_FAILED`

### Model
- `MODEL_TIMEOUT`
- `MODEL_RATE_LIMIT`
- `MODEL_AUTH_ERROR`
- `MODEL_PROVIDER_ERROR`
- `MODEL_INVALID_RESPONSE`

### Graph
- `GRAPH_TIMEOUT`
- `GRAPH_AUTH_ERROR`
- `GRAPH_RATE_LIMIT`
- `GRAPH_NOT_FOUND`
- `GRAPH_PROVIDER_ERROR`

### Knowledge
- `KNOWLEDGE_TIMEOUT`
- `KNOWLEDGE_NO_SOURCE`
- `KNOWLEDGE_PROVIDER_ERROR`

### Database
- `DATABASE_TIMEOUT`
- `DATABASE_CONNECTION_ERROR`
- `DATABASE_QUERY_ERROR`
- `DATABASE_PERSISTENCE_ERROR`

### Storage
- `STORAGE_TIMEOUT`
- `STORAGE_ERROR`

### Agent/tool
- `SPECIALIST_FAILED`
- `TOOL_TIMEOUT`
- `TOOL_ERROR`

### Governance
- `SOURCE_GAP`
- `COMMAND_AUTHORITY_REJECTED`
- `APPROVAL_REJECTED`

### Streaming
- `SSE_DISCONNECTED`
- `SSE_COMPLETION_MISMATCH`

### Observability/FinOps
- `TRACE_EXPORT_FAILED`
- `METRIC_EXPORT_FAILED`
- `COST_LEDGER_PERSIST_FAILED`
- `TELEMETRY_DROPPED`

### Runtime
- `TURN_STALLED`
- `TURN_TIMEOUT`
- `TURN_CANCELLED`

## Retry policy

Retries only for retry-safe operations.

Per dependency define:
- retryable codes,
- max attempts,
- exponential backoff/jitter,
- idempotency guarantee.

Never automatically retry non-idempotent operational actions, approvals or command execution unless existing semantics explicitly make the retry safe.

## Circuit breakers

Consider for broad provider outages such as Graph/model provider. Circuit state must itself be observable.

Do not insert a circuit breaker into governed command execution without explicit safety review.

## User vs operator failure data

User receives:
- safe concise failure,
- retry/escalation guidance where appropriate.

Operator trace receives:
- error code,
- owning boundary,
- dependency,
- duration,
- retry count,
- correlation IDs.

No secrets or raw stack traces in user responses.


## SLO interaction

Timeout and terminal semantics must match `21_SLO_FORMULAS_AND_INITIAL_TARGETS.md` so reliability controls and SLO denominators cannot diverge.
