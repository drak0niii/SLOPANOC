# Local Observability Console and separate read API

The console is `/resources/observability` in the existing SLOPANOC Resources-site shell.
This is the approved separate feature, not M17. All dashboards and effective policy, retention, integrations and access live here.
App Settings has no Observability entry. All information uses the separate read API.

## Launch contract

Start the existing ANOC service through its normal local launch process. Start the
reader independently from the repository root:

```sh
.venv/bin/python -m uvicorn backend.api.observability_app:create_app --factory --host 127.0.0.1 --port 8001
```

For local Vite development configure:

```sh
npm run dev
```

Leave `VITE_OBSERVABILITY_API_BASE_URL` unset in local development. Vite routes
`/api/observability/*` to the independent reader on port 8001, except SSE receipts
which remain on ANOC on port 8000. Browser fetch uses native same-origin
credentials; proxy forwarding preserves Cookie/Authorization, without asserting
roles or granting capabilities. This is transport only, not authentication.

For an explicitly selected separate-origin reader, set
`VITE_OBSERVABILITY_API_BASE_URL=http://127.0.0.1:8001` and set
`SLOPANOC_OBSERVABILITY_ALLOWED_ORIGINS` on the reader to the exact browser
origin, e.g. `http://localhost:5173`. Multiple origins are comma-separated, without
spaces. No wildcard or credentialed-cookie CORS is enabled. Existing business API
and POST SSE receipt transport continue to use `VITE_SLOPANOC_API_BASE_URL`.
Production builds require an explicit HTTPS observability base; configure it at
build time. Vite SPA fallback supports direct route reload locally; production
hosting fallback and authentication transport need separate deployment validation.

`SLOPANOC_OBSERVABILITY_READ_DATABASE_URL` must reference an already-provisioned,
approved PostgreSQL asyncpg read principal. No database, schema or identity is
created by startup. Do not bind development commands to shared databases by
convenience. Development may reuse an explicitly supplied `SLOPANOC_DATABASE_URL`;
production/staging require the dedicated URL. SQLite is restricted to explicit
Python `isolated_test=True` disposable-file fixtures, not production launch.

## Shared LOCAL/DEV identity

### Verified local launch configuration (2026-10-11 runtime repair)

The running reader must exist on port 8001. A running ANOC service on 8000
alone cannot serve the console's GET requests. Both processes need explicit
auth bindings; exporting them in another terminal does not update a running
process. Restart the local processes after changing their environment.

Run these in separate terminals, retaining your existing exported
`SLOPANOC_DATABASE_URL` and `SLOPANOC_KNOWLEDGE_DATABASE_URL` backend bindings.
No database credentials are copied into this guide. The reader reuses the
explicit development database binding with read-only transactions; this does
not provision missing telemetry schemas or publish missing source data.

ANOC:

```sh
cd /Users/costi/Desktop/SLOPANOC/SLOPANOC-main
SLOPANOC_OTEL_ENVIRONMENT=development \
SLOPANOC_AUTH_MODE=development \
SLOPANOC_DEV_AUTH_POLICY_FILE=/Users/costi/Desktop/SLOPANOC/SLOPANOC-main/config/auth/development.json \
SLOPANOC_MODEL_WARMUP_ENABLED=false \
.venv/bin/python -m uvicorn backend.api.app:app --host 127.0.0.1 --port 8000
```

Reader:

```sh
cd /Users/costi/Desktop/SLOPANOC/SLOPANOC-main
SLOPANOC_OTEL_ENVIRONMENT=development \
SLOPANOC_AUTH_MODE=development \
SLOPANOC_DEV_AUTH_POLICY_FILE=/Users/costi/Desktop/SLOPANOC/SLOPANOC-main/config/auth/development.json \
.venv/bin/python -m uvicorn backend.api.observability_app:create_app --factory --host 127.0.0.1 --port 8001
```

Frontend:

```sh
cd /Users/costi/Desktop/SLOPANOC/SLOPANOC-main
env -u VITE_OBSERVABILITY_API_BASE_URL -u VITE_SLOPANOC_API_BASE_URL npm run dev -- --host localhost --port 5173 --strictPort
```

`development.json` resolves `api-user` as Developer/SRE with operational metadata
and technical diagnostics in development. For a separate financial test session,
use `/Users/costi/Desktop/SLOPANOC/SLOPANOC-main/config/auth/development.finops.json`
as the policy path in **both** backend commands; it resolves the same `api-user`
with the FinOps role and financial data only. Existing role ceilings prohibit
granting all three capabilities to a single principal. Neither profile is valid
in production. Keep all services bound to loopback.

Both backends now consume the same server-side VerifiedIdentityProvider. Default
mode is disabled. Explicit `SLOPANOC_AUTH_MODE=development` with the same trusted
`SLOPANOC_DEV_AUTH_POLICY_FILE` and local/development environment resolves the
existing default/dev-user source to validated server-owned grants. Browser roles,
capabilities and environment headers never grant authority. The example policy
provides Operator metadata only; financial/technical access requires explicit
role-compatible policy. No local login screen or new cookie/token scheme is added.

See [shared authentication and future IAP requirements](AUTHENTICATION.md) for the
three backend bindings, policy schema/ceilings and production denial. Without
these explicit bindings `/access` remains 401. Production cannot enable development
auth; IAP provider is inert/deny-only until real signed-JWT verification and reviewed
deployment exist. FastAPI test providers are not production authentication.

## Effective read bindings

| Binding | Default / meaning |
|---|---|
| `SLOPANOC_OBSERVABILITY_READ_POOL_SIZE` | 2, maximum 8, zero overflow |
| `SLOPANOC_OBSERVABILITY_READ_POOL_TIMEOUT` | 1 second, maximum 2 |
| `SLOPANOC_OBSERVABILITY_READ_READ_DEADLINE` | 2 seconds, maximum 2 |
| `SLOPANOC_OBSERVABILITY_READ_READ_CONCURRENCY` | 4, maximum 8 |
| `SLOPANOC_OBSERVABILITY_READ_ARCHIVE_ROOT` | Optional existing filesystem archive root; no directory creation; one reconstruction slot |
| `SLOPANOC_OBSERVABILITY_READ_PRODUCER_POLICY_FILE` | Optional reviewed immutable JSON ObservabilityConfig snapshot, environment-matched, no symlink, at most 64 KiB; absent means unavailable policy, not reader defaults |
| `SLOPANOC_OBSERVABILITY_READ_BILLING_SCOPE`, `...PRICING_SCOPE`, `...FOCUS_SCOPE` | Optional server-owned SHA256 published-source scopes, never raw billing account inputs from browser |
| `SLOPANOC_OBSERVABILITY_READ_SOURCE_MODE` | BIGQUERY; TEST_FIXTURE only for local fixtures; descriptors never initiate extraction |

The environment is the existing `SLOPANOC_OTEL_ENVIRONMENT` binding. Server
capabilities, role ceilings, selected environment and cursor scope remain enforced.
Environment selection cannot grant access. PostgreSQL connections request read-only
transactions, statement and lock deadlines. These settings supplement least-privilege
DB grants; actual grants and PostgreSQL contention have not been live-validated.

`/health` is process liveness. `/readiness` checks database connectivity with
`SELECT 1`, not schema completeness, producer health or full source availability.
Protected health separately reports durable projection connectivity and stale
records. Reader health never implies healthy ANOC or Collector.

## Lifecycle and endpoint sources

The reader creates its bounded pool, repositories, read adapters, production
safe JSON formatter lease and optional existing archive reconstruction wrapper; shutdown disposes its own pool. It does
not acquire producer telemetry, warm models, instantiate execution services,
start projection/SLO/accounting/financial workers, run source extraction or retention,
ingest SSE receipts, or generate financial projections.

The explicit 30-GET inventory and individual source classification are in
`backend/api/observability_app.py` (`READ_PATHS`, `SOURCE_CLASSIFICATION`). It covers
runs/active/detail/timeline/session runs/retention; protected health/config/policies/
access; SLO list/detail; runtime usage/ledger health/completeness; billing health/
pricing/summary; reconciliation/allocation; unit economics; budgets/forecasts/
anomalies; retained exceptional archive reconstruction. Public liveness/readiness
are the only additional GETs. Mutation routes are not installed.

Durable rows and published financial generations remain authoritative. ANOC
pending/admitted/terminal-pending/accepted/coalesced/attempted/persisted/failed/
retry/rejection/terminal-unpersisted/shutdown-incomplete/invalid counters, exporter
state, Collector configuration/health and ledger admission failures are unavailable
without a durable producer publication. They are never replaced with zeros.

Missing latency/throughput/error-rate series, unsupported agent statistics,
financial attribution and alert history remain unavailable. Alert panels show
existing source states; no alert delivery or remediation is introduced. Trace
waterfall uses retained span timing/parentage only, not inferred reasoning.

## Reproducible validation

```sh
.venv/bin/python -m pytest -q backend/tests/test_observability_read_plane.py backend/tests/test_observability_dependency_coverage.py
.venv/bin/python -m pytest -q -s backend/tests/test_observability_read_process.py
npm test -- --reporter=dot
npm run build
git diff --check
```

The process fixture binds loopback only, creates disposable SQLite data, injects
verified test identity and forbids installed model/ADK/tool/command/financial job
entry points. It runs real ANOC and reader factories in different PIDs; slow reads,
30-request bursts, reader termination and restart preserve ANOC session creation.
This proves process/failure separation for the tested path, not production CPU,
turn/SSE capacity, shared PostgreSQL contention or provider availability.

Full backend regression uses the existing local-only M16 supervisor. Do not edit
historical M16 receipts or weaken the CPU gate. Results, repairs and individually
evaluated feature criteria are in `.agent/OBSERVABILITY_EXECUTION.md`.

## Production handoff

Cloud Run isolation NOT DEPLOYED. Read-only DB principal NOT CREATED. Production
CORS/auth topology and shared DB contention NOT LIVE-VALIDATED. No Terraform changes
are authored for this feature; native validation was not executed (CLI absent).
All deployment/IAM/grants/origins/hosting operations require exact user approval
under AGENTS.md. Cloud deployment status = NOT EXECUTED — USER APPROVAL REQUIRED.
M16 remains incomplete and M17 unstarted.

## Dashboard integration

Production requires an explicit HTTPS `VITE_OBSERVABILITY_API_BASE_URL`; missing
configuration fails closed. Development defaults to the dedicated reader proxy,
not the ANOC business fallback. Test configuration supplies a loopback fixture binding
only. See [dashboard source audit](OBSERVABILITY_DASHBOARD_SOURCES.md) for all
30 allowlisted GETs, exact fields, capabilities, range support and unavailable
metrics. Usage/billing/history use 1/7/30 completed UTC days. Active runs are a
current snapshot, SLOs a rolling 28-day window and controls published monthly
outputs; these scopes are labelled independently. Refresh advances the window
and refreshes active queries plus verified access without reloading the page.

## Hybrid scaffolding does not change local launch

[Doc 26](26_HYBRID_EVENT_DRIVEN_OBSERVABILITY.md) adds target audit/outbox/Collector/
projector architecture. The new pure contracts and dormant nop branch are not
imported by either service or launched here. No Pub/Sub publisher/projector worker,
cloud credentials, outbox/projection schema or migration is added to this launch.
The independent reader remains GET-only and source gaps remain unavailable.
