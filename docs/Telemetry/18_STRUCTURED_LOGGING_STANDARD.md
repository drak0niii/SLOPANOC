
# 18 — Structured Logging Standard

## Production decision

All production application logs are structured JSON and exported to Google Cloud Logging.

Logs must correlate with:
- `trace_id`,
- `span_id`,
- `run_id` where available.

Plain unstructured production logs are not the primary logging format.

## Canonical JSON fields

Required:

```text
timestamp
severity
service
environment
message
event_name
error_code
trace_id
span_id
run_id
```

Conditional:
- agent,
- tool,
- dependency,
- stage,
- model,
- duration_ms,
- retry_count.

Prohibited:
- access tokens,
- auth headers,
- secrets,
- full prompt/response text,
- full Teams content,
- chain-of-thought.

## Severity mapping

- DEBUG — developer detail, normally reduced in production.
- INFO — lifecycle/expected operational transitions.
- WARNING — degraded/retried/recoverable condition.
- ERROR — failed operation requiring investigation.
- CRITICAL — safety/security/data-integrity incidents.

A source gap or command rejection is not automatically ERROR; classification depends on whether it is expected policy behavior.

## Exception ownership

One boundary owns the detailed exception log.

Higher layers:
- add context via trace events,
- do not emit duplicate full stack traces.

## Cloud Logging requirements

Configure:
- structured field parsing,
- trace correlation,
- log-based metrics only when not duplicating OTel metrics,
- retention policy,
- exclusion filters for noisy low-value logs where safe.

## Logging vs tracing

Logs answer textual operational events/errors.  
Traces answer causal flow and latency.  
Metrics answer aggregate health.

Do not duplicate identical payloads across all three.

## Log retention

Default production retention:
- 30 days for normal application logs unless enterprise policy requires otherwise,
- security/audit logs follow security policy,
- raw conversation content is not retained in logs.

## Logging tests

CI must detect:
- JSON schema violations,
- prohibited field names,
- fake-secret fixtures appearing in exporter payloads,
- raw exception duplication in known critical paths.
