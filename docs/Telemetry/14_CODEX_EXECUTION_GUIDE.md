# 14 — Codex Execution Guide

## Purpose

This file tells a coding agent how to execute the roadmap without architectural drift.

## Before every milestone

1. Read `README.md`.
2. Read `00_MASTER_BUILD_CONTRACT.md`.
3. Read the milestone in `12_IMPLEMENTATION_ROADMAP.md`.
4. Read all directly relevant design files.
5. Inspect the **current working tree** and `git diff`.
6. Inspect existing implementations before creating abstractions.
7. Produce a brief implementation map:
   - files to add,
   - files to modify,
   - existing code to reuse,
   - invariants at risk.

Do not code first and rationalize architecture later.

## Local working tree wins

The developer may have uncommitted changes. Never reset, checkout, overwrite or revert unrelated work.

Remote GitHub may be stale compared with local code.

## Coding rules

### Reuse before replace
Prefer adapters/wrappers/extensions over duplicate subsystems.

Do not create:
- a second turn pipeline,
- a second run registry,
- a second cost calculation truth,
- a second telemetry naming scheme.

### Keep orchestration thin
`chat_service.py` coordinates; `backend/observability` implements reusable observability behavior.

### Server-owned semantics
Stage/status/error/cost categories are server code and stable enums/codes, not model-generated free text.

### No hidden reasoning
Never enable full prompt/model reasoning logging as a shortcut.

### Cardinality discipline
Never add run/session/case IDs as metric labels.

### Financial discipline
Never silently substitute estimated cost for billed cost or spread unknown cost to make allocation look complete.

## Milestone completion report

After each milestone report:

### Implemented
Exact functionality.

### Files changed
Path and purpose.

### Invariants preserved
Especially command/governance safety.

### Configuration added

### Migrations added

### Telemetry/FinOps output
Example spans/metrics/ledger rows.

### Known limitations
Only intentional deferrals.

### Exit criteria
Pass/fail for every milestone gate.

## Stop conditions

Stop and report a blocker rather than invent unsafe behavior if:
- implementation requires weakening command authority;
- security/compliance policy is unknown but raw data exposure is required;
- financial allocation key does not exist;
- correct implementation would require logging prohibited content.

## Review checklist

Before milestone commit:
- no duplicate telemetry systems;
- imports clean;
- async spans close;
- exporter failure bounded;
- secrets redacted;
- metric labels bounded;
- usage ledger unsampled;
- pricing version persisted;
- migrations reversible/safe;
- tests updated/added;
- docs updated.

## Commit discipline

Prefer logical milestone/submilestone commits.

Suggested prefixes:
- `obs:`
- `sre:`
- `finops:`
- `ui:`
- `docs:`

Do not mix unrelated conversational architecture changes into observability work.


## Approved production decisions

Codex must not ask to redesign or choose alternatives for:
- OTel Collector -> Cloud Trace/Monitoring/Logging.
- Cloud SQL durable active-run status.
- JSON structured logs.
- Terraform observability-as-code.
- W3C Trace Context.
- Detailed Billing + Pricing primary; FOCUS supplemental.
- EUR reporting.
- Accounting-grade unsampled cost ledger.
- fixed initial retention/sampling.
- quality/outcome telemetry.
- CI contract gates.
- continuous profiling.

If local repository constraints make one technically impossible, report the conflict and smallest compatible adaptation; do not silently choose another architecture.
