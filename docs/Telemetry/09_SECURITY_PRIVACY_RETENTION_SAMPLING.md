# 09 — Security, Privacy, RBAC, Retention and Sampling

## Core rule

Observability data is production operational data and must be governed like production data.

## Data classification

### Safe by default
- timing,
- stage/status,
- agent/tool/model names,
- token/count metrics,
- stable error codes,
- approved source identities.

### Restricted
- session/run/thread IDs,
- user identifiers,
- case/fault IDs,
- resource/chat IDs.

### Prohibited by default
- credentials/tokens,
- auth headers,
- full prompts,
- full assistant responses,
- Teams message bodies,
- attachment contents,
- knowledge document bodies,
- hidden chain-of-thought.

## Central redaction

All custom telemetry metadata passes through a single allow-list/redactor.

Responsibilities:
- permit known fields,
- remove secret-looking fields,
- truncate strings,
- sanitize error messages,
- optionally hash/opaque selected identifiers,
- reject unexpected nested payloads.

Do not rely on each caller remembering to redact.

## RBAC

Suggested roles:

### User
Own chat progress only.

### Operator
Safe active-run/timeline metadata.

### Developer/SRE
Technical trace/error details.

### FinOps
Cost/usage/allocation, not conversation content.

### Admin
Configuration/retention.

### Auditor
Read-only governed summaries according to policy.

Enforce authorization server-side.

## Retention tiers

Define independent configurable retention for:
- cost ledger — long,
- aggregate metrics — long,
- error/timeout traces — medium/long,
- governed operational traces — medium/long initially,
- healthy generic traces — shorter/sampled later,
- active-run state — TTL,
- domain diagnostics — bounded.

Final day counts depend on enterprise policy and compliance requirements.

## Sampling

Never sample:
- cost ledger,
- aggregate metrics,
- security/safety events.

Always retain initially:
- errors,
- timeouts,
- stalled runs,
- command authority/approval paths,
- governed operational turns if policy permits.

Healthy general traces can be sampled later after baselines exist.

## Deletion interaction

If sessions/conversations can be deleted, define whether related trace metadata is deleted, anonymized or retained under audit exception. Do not create a hidden shadow copy of deleted content.

## Encryption

Use platform encryption in transit/at rest. Apply production metadata access controls to telemetry databases and billing datasets.

## Secret leakage testing

Use fake secrets in automated tests and verify they never appear in:
- traces,
- logs,
- metrics,
- cost ledger,
- API responses.

If sanitizer detects a prohibited field, increment a safe counter without exporting the value.


## Fixed production retention

Initial targets:

| Data | Retention |
|---|---:|
| Normal successful traces | 30 days |
| Error/timeout/stalled traces | 90 days |
| Governed operational traces | 90 days |
| Structured application logs | 30 days unless policy requires longer |
| Aggregate metrics | 13 months |
| FinOps usage ledger / billing analytics | 24 months |
| Active-run status | short TTL after terminal state |

Enterprise/compliance policy may require longer retention.

## Fixed sampling policy

Initial stabilization: 100% trace capture.

After stabilization:
- retain 100% errors/timeouts/stalled/safety/governed operational turns;
- retain 100% cost ledger and aggregate metrics;
- healthy general chat may be sampled to 10–20%.

Sampling changes are versioned and observable.
