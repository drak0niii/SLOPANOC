# M16 local failure, recovery and load validation — 2026-10-09

Status: **IMPLEMENTED IN PART; ACCEPTANCE INCOMPLETE. Ready for Next Milestone: NO.**
The persistent authority for status is `.agent/OBSERVABILITY_EXECUTION.md`. This
report records observed local evidence, not production qualification or a waiver.
M17/M18/M19 have not been started. No remote/cloud/shared mutation occurred.

## Reproducibility and isolation

Use `.venv/bin/python scripts/observability/run_m16_validation.py --profile PROFILE`.
Profiles: `ci`, `native`, `sampling`, `tail-long`, `extended`, `regression`, `postgres`.
The runner removes credential/provider/proxy/DB environment, creates temporary HOME
and TMPDIR, selects SQLite-only DSNs, blocks non-loopback Python network and DNS,
and bounds each owned child process group. The PostgreSQL availability probe exits
2 with NOT LIVE-VALIDATED; it never accepts a supplied DSN or installs/pulls anything.
A native Collector already present at `/private/tmp/m1-collector/otelcol-contrib`
is required; there is no download/install fallback. Version: contrib 0.160.0.

Normal CI includes a 30-second archive soak, actual default SDK/SLO queue bounds,
canonical turns at concurrency 4/8/12, durable accounting admission/result failure,
real accounting hard exits, archive crash seams, retention and M11–M14 fault checks.
The extended profile selects a ten-minute archive soak plus a sixty-second application
soak. `--heap-diagnostic -k application_soak` is diagnostic, not RSS qualification.
The full-window case is selected using `--profile tail-long -k
full_window_pending_pressure backend/tests/test_m16_collector_resilience.py`.
Only local/test doubles execute; application tests use in-memory ADK sessions plus
real disposable SQLite accounting. They do not prove Cloud SQL session durability.

## Reproduced defects and scoped repairs

1. Local tail timing: with early children and FAILED/TIMEOUT/EVER_STALLED roots four
   seconds later, the previous two-second window archived 0/3. No caching strategy
   can revise an already dropped trace. Both local source defaults now match the
   existing production source default of 240 seconds. No production file changed.
   Short repaired fixture retained all three complete traces. The real 240-second
   gate also passed, with root evidence arriving at 180 seconds.
2. Archive bridge admission: 20 incomplete HTTP headers created 20 handler threads
   despite the inner four-request semaphore. Admission now precedes thread creation,
   excess connections receive bounded 503, and the socket timeout is set before
   header parsing. The same slow-header test passes with at most four handler threads.
3. Cross-section authorization loss: original baseline source failed both 401/403
   tests; a sibling retained financial data after diagnostic revocation. Mounted
   sections now cancel outstanding work, advance response generations and clear
   protected values together. Initial role-specific 403 remains an independent
   access outcome; 401 and loss of previously permitted scope invalidate siblings.
   A preceding 503 or key change cannot hide prior authorization. No data cache,
   permissions grant, server RBAC change or new Settings interface was introduced.

Harness failures are not product defects: invalid early fixture links/closure
encoding were repaired; globbing transient staging directories was replaced by
reading stable completed-object directories; duplicate archive retry correctly
bypassed a fault injected into unused writes, so the soak targets verification
reads. A newly created mock per React render was replaced by a stable mock and the
original-source failure was reproduced again. Missing guard enablement now exits
78 because ordinary sitecustomize exceptions are ignored by Python. No production
runtime imports that guard.

## Late exceptional sampling and capacity

The full 240-second pressure test retained **128/128 complete exceptional traces**
and **256/256 Cloud Trace-like spans**, with terminal evidence delayed 180 seconds;
completion was observed at 242.23 seconds. Early and terminal spans were both present.
RSS observations were 202,113,024 bytes at startup and 95,584,256 bytes after residency;
these are two samples, not a measured peak or a leak slope. The Collector cap was
256 traces per independent branch. Healthy long traces remained subject to the
10% fixture policy and never entered the archive. Production healthy retention
remains 100%; existing 100/10% native fixtures and SDK ALWAYS_ON regressions pass.

The local planning window is 212 seconds: 180 total turn + 5 batch interval +
(ceil(2048/256)+1)*2 exporter residence + 9 transport/batch/retry/timer allowance.
This conditional allowance is not a guarantee when queues drop or transport fails.
At 0.5 new traces/second, a 64-trace burst and 25% headroom, a 240-second residence
requires 230 entries; 256 fits this planning shape. The expected-new-traces hint
32/second cannot be interpreted as a supported throughput. The local preflight
rejects a two-second timer, a 300-second turn deadline and a two-traces/second
proposal with the same capacity. It does not configure or qualify a deployment.

Both cache-disabled and 128-entry cache fixtures forwarded late spans of kept
traces and did not reclassify late exceptions after prior DROP. All spans must
reach the same sampling processor instance. Multi-instance production affinity is
NOT LIVE-VALIDATED. Three hard-kill/reopen repetitions show volatile child loss
stays PARTIAL; retrying telemetry bytes restores one immutable COMPLETE version.
These are Collector hard kills with graceful bridge reopens, not tests of every
possible combined hard-crash order. No business dispatch capability exists in
those processes. Full default-window burst evidence is distinct from shortened
three-second capacity characterization.

## Business, accounting, reliability and persistence

Canonical GENERAL turns execute one fake runner per accepted turn at concurrency
4/8/12 for success, exception and SSE disconnect. Roots close once, background tasks
and session locks release. Tool-worker pressure admits 24 jobs (8 workers/16 pending),
rejects a further 24, then releases held capacity. Existing reliability tests cover
all child deadline categories, remaining-parent budgets, cleanup reserve, watchdog,
EVER_STALLED, monitor failure, cancellation origins, late result/authority guards,
Graph/gateway timeouts and provider-owned retries. These are local doubles.

Admission and STARTED failures dispatch the actual instrumented fake model zero
times at concurrency 4/8/12. Capture failure preserves the model result, makes one
physical invocation per turn and leaves visible debt; recovery does not repeat
provider execution. Hard exits after admission, STARTED, provider result and inbox
commit reopen the same SQLite file. Admission expires NOT_STARTED; uncertain
started/result attempts become OUTCOME_UNKNOWN; committed inbox materializes once.
The physical-dispatch file is unchanged by recovery. Existing correction/reversal,
conflict/idempotency and duplicate materialization tests remain applicable.

M11–M14 predecessor publication fault tests plus repeated native-currency signed-net,
component, absolute-total and count poison preserve prior current generations.
Retention concurrent reads and batches preserve atomic checkpoints and bounded
progress; repeated batch resume deletes nothing twice. Existing UNKNOWN dependency,
lineage, lost-fence, dry-run and financial retention tests pass locally. SQLite
cannot prove PostgreSQL row locks, serialization or uncertain network commit behavior.

SDK trace/log queues each accept 2,304 records (256 in flight plus 2,048 queued),
reject 100, show failed bounded flush while held, drain to zero and release the
export worker. The actual SLO writer accepts 1,024, rejects 100 and preserves UNKNOWN
source coverage after drain. SSE independent count/byte/oversized bounds detach and
stop buffering; disconnect leaves the canonical backend driver responsible for
completion. Other queue shapes and combined workload saturation remain incomplete.

Archive concurrent commit/reconstruction at workers 1/2/4/6 verifies immutable
idempotency and safe contents. Real child exits after chunk, object and manifest
creation reopen the same root, retry safely and reconstruct one verified envelope.
No missing span or uncommitted manifest is labeled COMPLETE. Corpus/fuzz tests
cover malformed protobuf, oversized/traversal/identity/environment/time/attribute
inputs and valid recovery. No actual GCS object operations were executed.

## Performance and resource verdicts

Matched local turns keep accounting enabled in both modes. Final CI measurement:
25 measured turns/mode after warmup, 60 physical fake provider calls, 60 ledger rows,
no debt. Median disabled/enabled wall time: 25.38/30.97 ms; process CPU: 18.46/23.96 ms.
Synthetic CPU delta: 29.77%. **Typical production CPU <3% is NOT established.**
There is no accounting-free baseline and no synthetic delay added to dilute the
percentage. The agreed CPU gate remains unpassed.

A full backend regression failed the unchanged synchronous dependency budget:
p95 13.415 ms versus <10 ms. The isolated diagnostic measured p95 0.462 ms HTTP,
1.350 ms database, 0.647 ms storage and 1.663 ms knowledge, 100 pairs each. This
separate passing diagnostic does not convert the failed full run to PASS. The
original mixed run also reported legacy aiosqlite worker/closed-loop warnings.
Scheduling/GC/fixture interference has not been proven to fully explain the failure.
The added safe benchmark diagnostic prints subsystem and CPU/wall summaries.

Ten-minute archive soak: 600.20 seconds, 2,388 operations, 597 batch samples,
three fault/drain cycles; batch p50 22.64 ms / p95 34.87 ms / max 54.39 ms, CPU16.21s.
Warmed RSS184,139,776 bytes, peak184,221,696; settled168,345,600 /168,820,736 /
169,328,640. Threads5 and FDs6 stayed constant. One complete durable object/manifest;
no temporary commit artifacts. p99 is not reported for fewer than1,000 samples.

Application capture-sink soak initially failed: warming four rather than eight
retained blocking workers caused bounded thread growth. Fully warming workers
removed it; FDs14, tasks1, checked-out connections0 and threads17 then stayed stable.
RSS still exceeded the original max(16MiB,10%) allowance: 197,656,576 to221,069,312
bytes over60s. Heap diagnostic showed intentionally retained development records:
2,048 spans,504 logs,64 metric snapshots. These findings are not hidden or declared
an application leak repair. A separate non-retaining counting-exporter workload
measures application-owned drift with the same allowance and accounting; its final
result is recorded in the execution log and receipts. No threshold was raised.

## Validation result and remaining gates

Declared CI:178 passed in55.37s. Native group:25 passed in154.93s; this exceeds the
provisional60s profile target and is recorded rather than hidden. Full240s pressure:
1 passed in245.19s. Archive extended:1 passed in602.33s. Frontend:983 passed across
67 files; TypeScript/Vite build PASS. Full backend: **1 failed,5904 passed,14 skipped**
in453.98s. Subsequent targeted queue/guard/CI checks are separate from that full run.
Twelve historical skips require unavailable real corpus or indistinguishable pending
fixture; two new opt-in cases were executed separately. New application soak was
added after that collection and must not be counted as executed by that full run.

Remaining blocking local work includes representative CPU qualification, resolving
full-run synchronous overhead, complete per-queue saturation/residence evidence,
all combined crash orders, broader governed business no-replay integration and
post-scenario cross-generation integrity, mixed API/retention/FinOps capacity and
comprehensive multi-process resource profiles. Individual C01–C44 results are in
the execution record; partial coverage is FAIL for milestone acceptance.

PostgreSQL, GCS/lifecycle/IAM, live Google Collector/exporters, Cloud SQL/session
persistence, production multi-instance affinity and shared migrations are **NOT
LIVE-VALIDATED**. Terraform fmt/validate are **NOT EXECUTED — CLI unavailable**.
Cloud deployment: **NOT EXECUTED — USER APPROVAL REQUIRED**. Exact future operations
must be proposed separately with project, environment, resources, cost, security,
operational risks and rollback; none is authorized by this report.

The non-retaining application transport profile passed separately in62.60s:
256 turns=256 physical fake model invocations=256 ledger rows, no debt; 60.72s
measured, CPU10.37s, turn p50/p95/max106.18/180.20/246.26ms (256 samples; p99N/A).
RSS196,952,064 warmed, peak203,161,600, settled198,098,944/197,394,432/198,606,848;
threads17, FDs14, tasks1, pool checked-out0 throughout settled observations. This
profile validates the actual SDK processors/queues with counter-only fake exporters,
not a cloud transport. Development capture-cache RSS failures above remain recorded.

Mixed financial profile passed in2.97s:24 concurrent M13/control reads alongside
16 M12/M13 generation attempts; immutable M12/M13/M14 bodies matched before/after,
and both SQLite pools had zero checked-out connections. It is a financial-workload
profile, not proof of every cross-generation crash combination.

Pinned behavior references: [tail sampler0.160.0](https://github.com/open-telemetry/opentelemetry-collector-contrib/blob/v0.160.0/processor/tailsamplingprocessor/README.md)
and [configuration0.160.0](https://github.com/open-telemetry/opentelemetry-collector-contrib/blob/v0.160.0/processor/tailsamplingprocessor/config.go).
Default caches are zero-sized; late arrivals inherit a resident prior decision;
removal/restart and cache eviction affect that residency. Processor-instance affinity
is a prerequisite. These upstream constraints informed the local timing gate;
local native outcomes above are measured evidence, not inference from documentation.

## Actual files changed

- `.agent/OBSERVABILITY_EXECUTION.md`
- `backend/observability/archive_bridge.py`
- `backend/tests/_m16_harness.py`
- `backend/tests/test_m16_accounting_resilience.py`
- `backend/tests/test_m16_archive_resilience.py`
- `backend/tests/test_m16_collector_resilience.py`
- `backend/tests/test_m16_finops_resilience.py`
- `backend/tests/test_m16_load_profiles.py`
- `backend/tests/test_m16_performance.py`
- `backend/tests/test_m16_queue_resilience.py`
- `backend/tests/test_m16_retention_resilience.py`
- `backend/tests/test_m16_security_resilience.py`
- `backend/tests/test_m16_turn_resilience.py`
- `backend/tests/test_observability_collector.py`
- `backend/tests/test_observability_dependency_performance.py`
- `docs/Telemetry/validation/M16_LOCAL_RECEIPTS.json`
- `docs/Telemetry/validation/M16_LOCAL_VALIDATION.md`
- `infra/observability/collector/README.md`
- `infra/observability/collector/config.local.yaml`
- `scripts/observability/guard/sitecustomize.py`
- `scripts/observability/m16_sampling_preflight.py`
- `scripts/observability/run_m16_validation.py`
- `src/components/shell/settings/observability/FinancialSourcePanel.test.tsx`
- `src/components/shell/settings/observability/useObservabilityQuery.test.tsx`
- `src/components/shell/settings/observability/useObservabilityQuery.ts`

Final declared CI: **179 passed in54.65s**. Exact profile receipts are in
`M16_LOCAL_RECEIPTS.json`; failed full backend remains FAIL.

## M16 continuation — 2026-10-10 (final regression pending)

The original 12 failed gates remain preserved above and in the execution record.
This continuation adds actual canonical backend child exits at eight boundaries,
49 integrated approval/command cases (seven boundaries × seven ancillary faults),
five retention process exits, six three-process crash orders, native default
exporter saturation/recovery, actual projection/activity/accounting admission/spool/
financial-materializer bounds, mixed protected ASGI reads, and one shared M11–M14
correction lineage with two financial publication process exits. The command fixture
uses real approval, Command Authority and Teams write gates with fake HTTP: equipment
execution is not implemented by the architecture; remediation dispatch remains zero.

Measured projection work was repaired without relaxing any bound: immutable finite
vocabulary caches; no eager deny-object allocation for known registry fields; one
SDK mapping snapshot; absent-family filtering; present-field model projection; one
sanitized-event projection; and unchanged final model attributes avoid redundant SDK
validation only when the installed span limit proves no eviction. The latter is
used only immediately before model span completion; unknown SDK internals and full
attribute populations retain original behavior. Five native SDK limit cases pass.
10,000 deterministic differential projection/rejection outcomes match the pre-edit
reference. Final privacy/cardinality/full regression must still pass.

The dependency component diagnostic (n=1,000 per component) improved complete-wrapper
median thread CPU from 277.834µs to 109.334µs; these are separate diagnostic runs,
not additive decomposition and not an explanation of the entire historical 13.415ms
wall-time tail. A full backend run after the initial repairs passed 5,915 tests with
15 skips, and knowledge paired p95 was 1.014083ms (<10ms unchanged). Final source has
additional changes/tests and therefore requires its own full rerun.

CPU remains FAIL. Earlier synthetic tiny-turn timing was not qualification. The
representative workload now uses real ADK Team Manager→TAE governed retrieval,
ProcedureAction and command presentation, installed GenAI fake transport and the
same durable unsampled accounting in both modes. Fixture construction is outside
CPU timing. Two mode blocks reverse their order, exclude ten warm turns each, drain
warm batches, execute thirty measured turns each, and settle measured export queues
before stopping process CPU. No sleeps/padding/provider latency or permission bypass
inflate the baseline. The first settled diagnostic measured 140.196ms baseline and
156.784ms enabled (+11.832%, limit <3%), 60 measured turns per mode and 1,120 physical
calls =1,120 ledger rows. Final idle qualification is pending. No inconclusive or
failed CPU result can become PASS through an isolated dependency microbenchmark.

Actual 240-second tail window: 148 pending traces per branch; terminal roots at
180 seconds; all 128 exceptional traces complete (32 each FAILED/TIMEOUT/
EVER_STALLED→COMPLETED/GOVERNED), 256 exceptional cloud-like spans, no healthy archive,
no early eviction, queue depth zero, completion at 242.237 seconds. Collector current
RSS sampled 175 times: warm 213,663,744B, peak 214,925,312B, resident 143,294,464B.
The 160MiB memory-limiter setting is a Collector heap policy, not an RSS hard cap.
The planning bound 212s and population 230 remain conditional admission estimates;
they do not guarantee surviving queue drop, process loss or absent trace affinity.

Native default exporters: each branch reaches its actual 128-request cap with a
fixture batch of one. Failed enqueue spans are explicit; sibling retains its complete
input. After healing the same process, queues drain to zero and a fresh exceptional
trace completes. This is intentional overload, not a supported always-keep promise.
The actual common limiter refuses both branches under a 1MiB test-only pressure seam
and recovers under the unchanged source 160MiB policy. Local 128/256 tail populations
remain distinct from a 16-entry/24-trace intentional eviction fixture.

Resource receipts contain owned PID RSS/thread/FD/socket counts, local task counts,
idle checked-out DB counts and bounded queue/backlog state. Native processes have no
portable external asyncio task view: `tasks=null` means unavailable/not applicable,
not zero. Backend self-measurement and application soak measure actual local tasks.
A test-only engine ownership fixture disposes SQLite engines before loop teardown;
it does not suppress warnings or enter the application. Final full regression must
confirm absence of closed-loop aiosqlite warnings. Development capture intentionally
retains bounded snapshots; production OTLP exporters do not use that capture class.
The final application sink uses actual OTLP trace/log encoding with no retained
records and preserves the privacy sentinel assertion on encoded bytes.

Extended archive rerun: 600.269s, 2,388 operations, 597 samples, three fault/recovery
cycles and protected reconstruction. Warm RSS186,728,448B; settled170,786,816B;
peak187,514,880B; FD10/socket2/thread5 stable; one immutable manifest/object and no
transient commit file. The accompanying 60.733s application run recorded256/256 calls
and rows, no debt, pool checked-out0, FD15/socket2/thread17/task1 stable; final OTLP-
encoding application rerun remains pending after the last projection optimization.

Frontend:985 tests/67 files pass, including 401/403 cancellation and late-response
rejection across all Settings query families; TypeScript and production build pass.
No new UI or mutable infrastructure setting was introduced. Local PostgreSQL binaries
are absent from PATH, Postgres.app and standard Homebrew locations; Docker CLI exists
but both usual daemon sockets are absent. No daemon start/image pull/install/shared
DSN fallback. Native Terraform CLI absent. All cloud/shared validation remains pending;
M17/M18/M19 are not implemented. No cloud or external resource mutation occurred.

## Final M16 continuation acceptance — 2026-10-10

This final result supersedes the pending checkpoints above. M16 remains INCOMPLETE:
42 local criteria PASS, C41 FAIL and C42 FAIL. Ready for Next Milestone: NO.
M17 is NOT IMPLEMENTED and must not begin, including after any later green M16 run
without a separate instruction. No cloud/shared/external mutation occurred.

Final guarded full backend: **5,998 passed, 15 skipped, 1 failed**, 838 warnings,
598.67s. The sole failure is `test_representative_governed_turn_cpu`; the two cached
case-database fixture regressions are repaired. Closed-loop aiosqlite thread warnings:
zero. Skips: eleven unavailable real-corpus tests, two opt-in extended tests executed
separately, one separate full-window test executed separately, and one existing
pending-value semantic skip. Final CI:260 passed/0 failed/81.69s. Frontend:985 tests
in67 files pass; TypeScript and production build PASS. Local migration/privacy/
cardinality regressions pass in the selected126 and final full suite. Native full:
35 pass/1 opt-in skip; full-window executed separately. Compile and diff check PASS.

### Final overhead evidence

Original dependency paired p95=13.415ms. The unchanged <10ms gate now PASS in the
full suite, n100 per dependency after10 warm iterations, rotating mode order:

| Dependency | Baseline wall p50 ms | Enabled wall p50 ms | Paired overhead p50 ms | Paired overhead p95 ms | Median ratio increase % |
|---|---:|---:|---:|---:|---:|
| HTTP | .001666 | .146729 | .145103 | .362083 | 8704.67 |
| Database | .260875 | .745250 | .482250 | .540917 | 185.67 |
| Storage | .001709 | .142583 | .140896 | .357292 | 8243.19 |
| Knowledge | .048125 | .596208 | .547854 | .595083 | 1138.88 |

Ratio uses (enabled median / baseline median -1)*100; paired median is calculated
separately, not by subtracting medians. Tiny wait-excluded HTTP/storage baselines
make relative ratios large; acceptance is absolute synchronous overhead. P99 N/A
at n100. macOS26.6.2 arm64, Python3.11.2, local SQLite/fake dependencies. Complete
wrapper diagnostic n1000 thread CPU p50 improved277.834→109.334µs (p95119.625µs,
p99136.542µs in the recorded repaired diagnostic). Immutable registries, eager
allocation, repeated SDK mapping/projections and final attribute validation are
measured hotspots. The complete original wall-tail cause was not fully isolated;
no unsupported scheduler/GC attribution. No telemetry/privacy/accounting removed.

Representative settled CPU is a **REAL_DEFECT, FAIL**: full-suite baseline mean
137.092517ms, enabled155.207133ms, increment18.114617ms, **13.213425%**, unchanged
required <3%. Isolated final also FAIL:137.044133→149.240783ms,+8.899797%.
60 measured turns/mode,10 warm turns/block,2 reversed blocks/mode, identical ADK
Team Manager→TAE retrieval/ProcedureAction/command presentation and durable M10 in
both modes. Setup excluded, warm batches drained, normal asynchronous batching,
all measured export work settled inside process CPU timing, no synthetic padding.
1,120 physical calls=1,120 ledger rows, debt0. Full turn p50 baseline136.1315ms,
enabled144.0555ms; p95145.835/163.740ms; p99 N/A. Earlier tiny-turn CPU was diagnostic
only and cannot qualify this gate. A passing microbenchmark cannot override this.

### Final scenario and resource evidence

- Actual240s/180s native run PASS:148 pending/branch,128 exceptional COMPLETE,
  32 each FAILED/TIMEOUT/EVER_STALLED→COMPLETED/GOVERNED; healthy archive0, early
  evictions0,256 exceptional cloud-like spans,queues0,completion242.237s.
  RSS175 samples:warm213663744B/peak214925312B/settled143294464B.
- Planning envelope:180s supported root+23s SDK/export allowance+9s conservative
  transport allowance=212s≤240s. At .5 traces/s and burst64,25% headroom population
  ceil((.5*240+64)*1.25)=230≤256. This is a conditional planning bound; native
  long run measured148 pending and 2 spans/trace. The configured expected32 new
  traces/s is a sizing hint, not validated admission capacity or a promise to keep.
- 128/256 trace storms are distinct supported short-window local tests;16-entry/
  24-trace overflow intentionally exposes eviction/incomplete capture. Exporter
  storms200 traces/400 spans overflow actual128 request queues with fixturebatch1:
  cloud failure drops271 spans with sibling200/200 archives; archive failure drops
  270 with sibling400 distinct cloud spans and archive0/200 COMPLETE. Same-process
  heal drains queues0 and fresh exceptional archive succeeds. Common1MiB test seam
  refuses4 spans/503 across both branches; source160MiB restart recovers. The160MiB
  limiter is a heap policy, not an RSS cap. Overload is degradation, never complete
  retention. Exporter recovery3 PASS; RSS peak deltas below unchanged64MiB allowance.
- Default spool pending256 rejects257th then601s expiry/recovery. Default logical
  bytequota256MiB verified with a corrupt sparse staging file; rejection then expiry
  and COMPLETE recovery pass. This does not qualify256MiB valid payload or RSS.
- All49 governed cases PASS:actual turn/provider/agent/tool/gateway/command count1,
  remediation0. Approval consumption cannot replay. Seven boundaries × seven faults
  include real retention SQL rollback and protected-diagnostics service failure;
  all HTTP and simulated command effects remain fake/local.
- Eight real backend exits PASS. Accepted-before-first-await lacks a durable
  operational row before any business dispatch: explicit truthful gap. STARTED
  uncertainty stays UNKNOWN/stale, completed invocation has one ledger row, terminal
  persistence remains COMPLETED. Recovery never changes dispatch receipts; owner/CAS
  fences prevent late overwrite. No fabricated terminal or automatic business retry.
- Six three-process restart orders PASS, including genuinely simultaneous backend+
  bridge and Collector+bridge. Reconstructed archive complete only after missing
  bytes restored; volatile early child loss stays PARTIAL. Prior manifest immutable.
  All three owned PIDs measured before kill; two restarted PIDs measured after.
- Five actual retention exits PASS:before transaction/scan/delete/precheckpoint/
  postcommit. Uncommitted8 rows/gen0; committed1 row/gen1 for batch7; restart deletes
  only remaining rows and then0. No duplicate side effect/checkpoint inconsistency.
- Shared nonempty100EUR reviewed budget, M11–M14 corrections and two publication
  exits PASS:24 concurrent audits, four histories each,20/30/40 corrections, exact
  conservation, coherent unique head, prior histories byte/JSON immutable, reopen
  returns same valid head,checked-out DB0. Financial dependency/UNKNOWN tests pass.
- Archive soak600.269s PASS:2388 operations,597 samples,three fault/recovery cycles,
  protected reconstruction,one immutable manifest/object,no transient commit files.
  Warm/peak/settled RSS186728448/187514880/170786816B,FD10/socket2/thread5 stable.
- Final nonretaining real-OTLP-encoding application soak60.822s PASS:256 calls=
  256ledger,debt0,pool0,tracequeue0,FD15/socket2/thread17/task1 stable. Warm/peak/
  settled RSS194854912/201850880/198868992B. Three cycles,privacy sentinel intact;
  unchanged10%/16MiB allowance. Earlier extended command overall1 FAIL(CPU)/2 PASS
  is preserved, not relabeled as profile PASS. Intentional bounded development
  capture is TEST_HARNESS_ONLY; production config rejects local capture exporter.
- Native Collector settled FD9/socket3/thread13–15; bridge FD5/socket2/thread2;
  running backend FD12–14/thread8–10. Backend self task10,pool0,export queues0.
  External process asyncio task counts are N/A, not assumed0. Stagingchunks0,
  completedversions2,temporaryfiles15 in the crash fixture. No monotonic unbounded
  resource growth or unrecovered backlog in the measured local profiles.
- Protected reads48 mixed requests with4 permits; fifth blocked read deadline2s
  returns503,permits recover4,healthy200,revokedUser403 before query. FinOps reads24;
  accounting admission8 and ninth bounded .25s/provider0; activity16/projection2048/
  financial materializers2 recover after overflow. Existing SSE/SDK/SLO bounds pass.
- Paired100%/10% native sampling evidence and final full M9–M14 invariance tests
  PASS: healthy sampling cannot alter unsampled arithmetic or accounting. Final
  privacy, SDK limit0/1/2/3/128, cardinality, server RBAC and environment isolation
  PASS. Frontend401/403 clears all query families and rejects late results.

### Threshold / assertion audit

| Item | Old | New | Reason |
|---|---|---|---|
| Dependency threshold | <10ms | <10ms | Unchanged approved contract |
| CPU threshold | <3% | <3% | Unchanged approved contract; still FAIL |
| Local decision wait, both branches | 2s | 240s | Align already-production240s; supported root/arrival derivation and native long gate |
| Collector num_traces/export queues/memory | 256 /128 /160MiB | Same | No production bound inflation |
| Spool pending/logical bytes |256 /256MiB | Same | Default pressure seams validate actual bounds |
| Bridge admission / socket timeout |4 /2s | Same | Admission moved ahead of parsing/thread creation; .1s overload response is bounded rejection |
| Resource limits |64MiB exporter delta;10%/16MiB app | Same | No relaxation |
| Financial panel alert count |1 |3 | Revocation now correctly clears all3 protected sections; content-clearing assertions retained |
| Diagnostic p99 |reported at n100 |N/A below1000 | Honest statistical reporting, no acceptance threshold change |

Fast fixtures explicitly override decision waits/batching/memory only for named
fault seams, never source production policy. Full pressure uses actual240s. All
changed assertions preserve or strengthen business, privacy and completeness rules.
CPU failure has not been relabeled or waived. Test-only SQLite cache cleanup fixes
the fixture defect and does not change production case persistence.

### Complete C01–C44 exit table

| Gate | Objective | Final local result |
|---|---|---|
| C01 | Governed no-replay exact dispatch | PASS |
| C02 | Accepted roots / hard-crash uncertainty | PASS |
| C03 | Ownership, locks and cleanup | PASS |
| C04 | Deadlines and parent budgets | PASS |
| C05 | Bounded legitimate retry | PASS |
| C06 | Stall classifications | PASS |
| C07 | Cancellation semantics | PASS |
| C08 | Worker admission | PASS |
| C09 | Finite queues and recovery | PASS |
| C10 | SSE backpressure | PASS |
| C11 | Admission prevents provider dispatch | PASS |
| C12 | Capture failure preserves result/debt | PASS |
| C13 | Accounting crash recovery | PASS |
| C14 | Idempotency and corrections | PASS |
| C15 | M11 publication fencing | PASS |
| C16 | M12 conservation | PASS |
| C17 | M13 allocation conservation | PASS |
| C18 | M14 immutable histories | PASS |
| C19 | Collector outage and restart | PASS |
| C20 | Independent exporters / common pressure | PASS |
| C21 | Healthy vs exceptional eligibility | PASS |
| C22 | Exceptional overload degradation | PASS |
| C23 | Late sampling and eviction | PASS |
| C24 | Archive idempotent commits | PASS |
| C25 | No false COMPLETE | PASS |
| C26 | Protected archive isolation | PASS |
| C27 | Retention process-kill atomicity | PASS |
| C28 | Financial retention dependencies | PASS |
| C29 | DB saturation and recovery | PASS |
| C30 | Conditional PostgreSQL availability | PASS — conditional discovery; PostgreSQL NOT LIVE-VALIDATED |
| C31 | M9 sampling invariance | PASS |
| C32 | M10–M14 sampling invariance | PASS |
| C33 | Privacy and metric cardinality | PASS |
| C34 | Authorization and environment isolation | PASS |
| C35 | Polling and revocation races | PASS |
| C36 | Settled resources and soak | PASS |
| C37 | Combined restart orders | PASS |
| C38 | Cross-generation integrity | PASS |
| C39 | Bounded CI and extended validation | PASS |
| C40 | Measured local envelopes | PASS |
| C41 | CPU and synchronous overhead | FAIL |
| C42 | Complete regression and build | FAIL |
| C43 | Truthful pending infrastructure | PASS |
| C44 | No mutation / no later milestone | PASS |

### Scope, limitations and final disposition

All PASS entries describe local evidence and actual gate scope, not production
qualification. PostgreSQL conditional discovery PASS:postgres/initdb/pg_ctl/psql
absent, Docker CLI present but configured/local sockets absent; no install/start/
pull/shared fallback. Native Terraform CLI unavailable. Accepted-before-first-await
root durable gap and volatile early trace loss remain explicit above. CPU remains
a blocking defect; full backend remains failing. Artifact receipts contain sanitized
aggregate measurements only, never raw prompts, telemetry payloads, DB files or keys.

Production Cloud Run load: NOT EXECUTED. Production Cloud SQL: NOT EXECUTED.
Production PostgreSQL concurrency: NOT LIVE-VALIDATED. Production Collector capacity:
NOT LIVE-VALIDATED. Production Cloud Trace: NOT LIVE-VALIDATED. Production GCS archive:
NOT LIVE-VALIDATED. Native Terraform validation: NOT EXECUTED. M17: NOT IMPLEMENTED.
Cloud deployment: NOT EXECUTED — USER APPROVAL REQUIRED. No exact cloud operation
was proposed/authorized/executed; prior source-controlled pending deployment/IAM/
archive/Collector/shared migration families remain pending and require the exact
10-point operation approval gate. No M17/M18/M19 work occurred.

UI impact: existing Settings information/API contracts reused; existing component
patterns retained; server RBAC applies; authorization loss clears mounted values;
configuration remains effective read-only. No infrastructure mutation control or
later-milestone UI introduced. Files changed are listed in the checkpoint above.

Ready for Next Milestone: **NO**. Stop at M16.

## M16 CPU closure attribution before production edits — 2026-10-10

Diagnostic A–I gates reuse actual workflow/SDK/processors; no external export. H
adds real population/classification and I has no additional separable layer in this
configuration. Producer projection and separate model-log gates were corrected in
the diagnostic harness and B–E repeated. Initial runs preserved as history.

| Layer | Mean CPU ms/turn | Increment previous ms | Paired increment vs own baseline ms | Paired overhead % |
|---|---:|---:|---:|---:|
| A | 137.480300 | N/A | 0.000000 | 0.000000 |
| B | 133.881417 | -3.598883333333305 | -2.528083 | -1.853304 |
| C | 137.040017 | 3.1585999999999217 | 0.312283 | 0.228398 |
| D | 136.262850 | -0.777166666666659 | 2.085000 | 1.553908 |
| E | 142.261900 | 5.999049999999983 | 9.312183 | 7.004290 |
| F | 170.005700 | 27.74380000000005 | 31.195950 | 22.473890 |
| G | 169.233700 | -0.7719999999999914 | 12.100600 | 7.700860 |
| H | 149.886950 | -19.346750000000043 | 9.497650 | 6.765224 |
| I | 151.235267 | 1.3483166666666762 | 17.717000 | 13.269345 |

The means are noisy and nonadditive: negative increments are retained, not selected
away. A is the actual disabled business+accounting baseline, not an enabled no-op
substitution. B infrastructure; C root; D children; E metrics; F typed projection;
G structured logs; H archive metadata; I full. The profile, not a noisy layer
increment alone, supports optimizing per-span family scans and enum set rebuilding.

Business equivalence before edits:80 turns/outcomes,560 provider/model/admissions/
ledger,160 agents,320 FunctionTool calls per mode; additionally80 custom specialist
tool calls were identified and are now counted explicitly (total400 tools/mode).
Physical1120=ledger1120. Initialization and first turns are outside measured blocks;
warm export drained and measured export settled inside total process CPU.

Main cProfile delta (60 measured turns/mode), diagnostic inflated times:
- observability/redaction.py:80:safe_span_attributes: calls8340, delta self1.981840ms/turn, delta cumulative6.849568ms/turn, avg self18.627µs/call.
- observability/tracing.py:171:on_end: calls2460, delta self1.245353ms/turn, delta cumulative10.775396ms/turn, avg self30.374µs/call.
- observability/redaction.py:145:safe_model_attributes: calls4560, delta self0.813999ms/turn, delta cumulative1.419017ms/turn, avg self13.241µs/call.
- observability/privacy_policy.py:114:<dictcomp>: calls2460, delta self0.311298ms/turn, delta cumulative0.455294ms/turn, avg self7.593µs/call.
- observability/tracing.py:149:on_start: calls2460, delta self0.281632ms/turn, delta cumulative0.458492ms/turn, avg self6.869µs/call.
- observability/tracing.py:75:safe_events: calls2460, delta self0.258112ms/turn, delta cumulative2.877164ms/turn, avg self6.295µs/call.
- observability/tracing.py:121:set_final_attributes: calls840, delta self0.227080ms/turn, delta cumulative1.167825ms/turn, avg self20.030µs/call.
- observability/tracing.py:99:safe_context: calls4920, delta self0.226439ms/turn, delta cumulative0.425875ms/turn, avg self2.761µs/call.

SDK attribute assignment delta82,020 calls/60 turns; profile self2.124ms/turn and
cumulative5.708ms/turn, overlapping child cleaning functions. Total enabled export
profile:3280 spans and640 logs across80 turns; trace encoding0.605504s,log0.081885s.
These profiler times cannot be added to unprofiled13.213425% or used as subtraction.
Profiled benchmark:417.368→455.658ms,+9.174%, DIAGNOSTIC ONLY. No threshold changed.
Healthy app path serializes its required OTLP trace once; exceptional archive envelope
creation happens in Collector/bridge and is not invoked for healthy app CPU.
No accounting duplication, diagnostic DTO/retention/forecast construction or provider
startup was found in the recurring extra business work. Instruments are created once;
some metric registry dicts still rebuilt, but profile contribution is smaller.

## Final CPU-only continuation — 2026-10-10

M16 remains BLOCKED. C41 FAIL; C42 FAIL; Ready for Next Milestone NO. The unchanged
representative process CPU requirement is <3%; unchanged dependency requirement
<10ms. No threshold/timeout/queue/memory/sampling/retention/accounting/governance/UI
policy changed. No cloud operation and no M17 implementation.

### Scoped repairs and validation

1. safe_span_attributes previously selected families then scanned/projected them
   repeatedly. It now visits present fields once using an immutable finite dispatch
   of the same type/enum/regex/range checks. Unknown fields deny. Arbitrary values
   still validate; no credential/content/identifier value is cached. Closure handling
   retains the original validator. Immutable finite regex and enum definitions are
   constructed once; there is no mutable global request state.
2. SafeSpanProcessor rebuilt producer/name sets from Enum.value at each span end;
   safe_events converted the same finite event names repeatedly. Immutable canonical
   producer/event tables replace this work. Safe event attributes, parentage, status,
   archive class/population metadata, event order and metrics remain unchanged.

Independent pre-edit reference covers every field with SAFE/RESTRICTED/PROHIBITED,
unknown/wrong types, oversized values, NaN/infinity/overflow, bounded enums, and
prompt/response/credential/secret/command/SQL/Teams/hidden-reasoning markers plus
10,000 deterministic mixed mappings. All producer/surface/environment boundaries,
512 concurrent request mappings and all event classes/modes match the reference.
No permissive trusted bypass was introduced. Actual privacy/cardinality/exemplar/
SDK limits and installed GenAI/ADK transport paths also pass after repair.

Initial projection/privacy/SDK selection19 PASS; final bounded M16 CI395 PASS,
0fail,82.11s (includes new reference/event tests and actual M15 privacy evidence);
focused real SDK/model/logging/export/parentage/server-policy/sampling selection
399 PASS,0fail,17.91s. Frontend985 PASS/67files,10.71s; TypeScript and production
build PASS (Vite4.10s). Compile and git diff --check PASS. No frontend change this
CPU-only continuation. Existing long/native/archive/retention/crash evidence is
preserved; no sampling, span boundary timing policy, Collector or terminal metadata
change justified rerunning long stress. No long stress was rerun.

### Authoritative repeats — all results, no selection

| Run | Baseline mean CPU ms/turn | Enabled mean CPU ms/turn | Increment ms/turn | Overhead % | Result |
|---|---:|---:|---:|---:|---|
| 1 | 139.140433 | 149.452750 | 10.312317 | 7.411445 | FAIL |
| 2 | 132.492133 | 140.448300 | 7.956167 | 6.005011 | FAIL |
| 3 | 131.579467 | 147.924983 | 16.345517 | 12.422544 | FAIL |

Original full-suite13.213425%, original isolated8.899797%. Final authoritative
repeat12.4225436390278%; required<3%, Threshold changed NO. No test rounded or
relabeled as PASS. Every run60 measured turns/mode,10 warm turns/block,two reversed
blocks/mode,normal async batching,measured export settled inside process CPU timer.
Same ADK Team Manager→TAE retrieval/ProcedureAction/command presentation,fake installed
GenAI transport and same durable M10. Assertions/receipt checking remain symmetric;
per-turn profiler objects/imports were moved outside timing in both modes, and
count-only spies are identical in both modes. This is not padding or estimated CPU
subtraction. Diagnostic feature gates/profile runs skip only their qualification
assertion explicitly and print DIAGNOSTIC_ONLY; their pytest PASS is not C41 PASS.

Each authoritative mode:80 accepted/completed turns including warm,560 provider
calls/model operations/accounting admissions/ledger rows,160 agent operations,
400 tools (320 FunctionTool +80 custom TAE tool),debt0. Total1120=1120 ledger.
Per measured block30 turns=210 physical calls; accounting is never disabled.
Runtime setup5.512–5.850ms and fake-provider fixture setup19.420–20.258ms are outside
steady state. Lazy first-provider initialization occurs in first warm turn. First
turn and final export settlement CPU are individually retained per block in receipts;
recurring export settlement stays inside the measured mean. Shutdown13.573–15.061ms
is measured separately after qualification. CPU scope is the application process,
including its worker threads; Collector/bridge are separate processes, not measured
here, and no work was moved out of scope to lower this metric.

### Residual CPU profile and limits of attribution

The full before-edit A–I table and profile above remain the source of attribution.
Layer means from independent processes vary; negative increments and full-vs-layer
variation are retained. They are not additive costs or acceptance. Main cProfile
covers60 measured turns/mode using thread CPU; worker profiles cover80 enabled
turns including warm. cProfile changes timings: the after profile403.059→456.529ms,
13.266%, is diagnostic only. Profiles do not exactly reconcile an unprofiled run's
18.114617ms or16.345517ms because of profiling perturbation and business CPU noise.
Exact dispatch counts exclude extra business work as the cause; none was found.

Key after-repair profile deltas per measured turn, overlapping cumulative costs:

| Function | Enabled calls | Delta self ms/turn | Delta cumulative ms/turn | Enabled self µs/call |
|---|---:|---:|---:|---:|
| opentelemetry/attributes/__init__.py:289:__setitem__ | 82020 | 2.113589 | 5.692517 | 1.546 |
| observability/redaction.py:153:safe_span_attributes | 8340 | 1.514241 | 4.363894 | 13.767 |
| opentelemetry/attributes/__init__.py:50:_clean_attribute | 82020 | 1.307258 | 2.811418 | 0.956 |
| observability/tracing.py:182:on_end | 2460 | 1.005930 | 8.079673 | 24.535 |
| opentelemetry/attributes/__init__.py:229:_clean_attribute_value | 82020 | 0.606724 | 0.870134 | 0.444 |
| opentelemetry/attributes/__init__.py:254:__init__ | 8640 | 0.539891 | 4.600361 | 3.749 |
| opentelemetry/sdk/trace/__init__.py:1180:start_span | 2460 | 0.511713 | 4.985794 | 12.481 |
| observability/redaction.py:106:validate | 35160 | 0.345326 | 0.657524 | 0.740 |
| opentelemetry/attributes/__init__.py:286:__getitem__ | 81480 | 0.313073 | 0.313073 | 0.231 |
| observability/tracing.py:160:on_start | 2460 | 0.267182 | 0.447551 | 6.517 |
| observability/tracing.py:132:set_final_attributes | 840 | 0.234869 | 1.171376 | 20.163 |
| observability/tracing.py:88:safe_events | 2460 | 0.226602 | 1.978485 | 5.527 |
| observability/tracing.py:110:safe_context | 4920 | 0.221749 | 0.420749 | 2.704 |
| opentelemetry/sdk/trace/__init__.py:891:set_attributes | 3300 | 0.215062 | 1.991111 | 3.910 |
| opentelemetry/sdk/trace/__init__.py:806:__init__ | 2460 | 0.199215 | 2.812248 | 4.859 |

Projection cumulative delta6.849568→4.363894ms/turn and span processor delta
10.775396→8.079673ms/turn in diagnostic profiles. SDK attribute assignment still
82,020 extra calls/60 measured turns (1,367/turn); required capture/start/final/event
metadata remains intact. Trace worker encodes3280 spans and log worker640 records
across80 turns. Their recurring encoding is charged inside settlement. Resource
hashing/copying, protobuf key/value encoding, safe log DTO revalidation and SDK
metric aggregation remain; metric instruments are registered once. Some finite
metric registry rebuilding remains a smaller contributor. No repeated policy
schema construction or retained raw payload was found in the new fast dispatch.
Healthy application traffic produces required OTLP trace payloads; archive envelope
serialization remains Collector/bridge work, not an application eligibility scan.
The representative governed trace has archive eligibility; source/profile inspection
shows no archive submit/full reconstruction/retention/forecast/API DTO work on this
application path. Context attach/detach and duration clocks retain required semantics.

The remaining last-run increment16.345517ms exceeds its3.947384ms allowed increment
by12.398133ms. Even the lowest measured repeat remains6.005011%, not PASS. Further
closure needs deeper producer/SDK/exporter data-flow work, rather than removing
observations or bypassing validations. This investigation does not prove a universal
lower bound or that no future implementation can meet3%; it proves the current
scoped repairs do not. Candidate designs, not implemented or approved departures:
- Immutable request-local validated attribute fragments/certificates reused across
  required sinks, retaining full validation of arbitrary/mutated input and each
  surface's filtering. Risk: stale trust or mutable nested values can breach privacy;
  SDK limits/evictions, concurrency, full trace/event equivalence require proof.
- A more efficient SDK/OTLP encoding path retaining all fields, boundaries and worker
  CPU in acceptance. Risk: compatibility/serialization/exemplar/privacy regressions;
  native dependency changes or replacing SDK internals need separate technical review.
Neither moves CPU into an unmeasured process nor disables trace/metric/log/accounting.

### Dependency regression and unchanged acceptance

Final bounded CI paired p95, n100 each after10 warm iterations:
HTTP0.110333ms,database0.416292ms,storage0.109292ms,knowledge0.464750ms; all<10ms.
P99 N/A. No synchronous regression introduced; original full-suite dependency pass
remains historical, and these current-source results are CI, not a new full-suite
claim. Paired M9 and M10–M14 sampling invariance pass after changes; accounting
admission, recovery and governed no-replay stay intact.

Full backend this CPU-only turn: NOT RERUN. User requires C41 PASS before that full
rerun; all three current authoritative CPU runs failed. Last full backend remains
5998 PASS,15 SKIP,1 CPU FAIL on the preceding source revision. Current source has
395 CI and399 focused passes, not a false current-source full-regression PASS.
C42 FAIL. No full-suite isolated success overrides a full-suite failure.

### Complete status and scope audit

C01–C40 PASS by current focused/CI evidence and preserved unaffected extended/native
receipts; C30 remains conditional discovery PASS with PostgreSQL NOT LIVE-VALIDATED.
C41 FAIL,C42 FAIL,C43 PASS,C44 PASS. Individual authoritative44 definitions unchanged.
PostgreSQL concurrency NOT LIVE-VALIDATED; native Terraform NOT EXECUTED (CLI absent).
Production Cloud Run load/Cloud SQL NOT EXECUTED; production PostgreSQL/Collector/
Cloud Trace/GCS archive NOT LIVE-VALIDATED. No external mutation/deployment, credential
use, shared migration, infrastructure installation or M17 implementation occurred.
Cloud deployment remains NOT EXECUTED — USER APPROVAL REQUIRED; no exact remote
operation is proposed. UI/contracts/RBAC/read-only configuration remain unchanged.

Actual CPU-only changed files:backend/observability/redaction.py,tracing.py;
backend/tests/test_m16_governed_performance.py; new diagnostic/reference/differential
files _m16_cpu_diagnostic.py,_m16_cpu_projection_reference.py,
test_m16_cpu_projection_differential.py; guarded runner; execution/report/receipts.
Prior unrelated local changes preserved. No acceptance threshold/assertion inflation,
sampling percentages/decision waits,queue sizes,memory bounds or policy weakening.

Ready for Next Milestone: **NO**. STOP at M16; M17 NOT IMPLEMENTED.

Attribution limitation: B–E diagnostic ablations also remove producer projection
housekeeping that the disabled authoritative mode can still execute against no-op
spans. Thus negative paired deltas are not purely scheduling noise, and the ladder
is not an orthogonal causal partition of the18.114617ms historical increment.
All business/accounting counts remain equal. Independent before/after call profiles
identify the repeated work; no diagnostic ablation or estimated subtraction enters
the unchanged authoritative CPU runs. A precise additive attribution remains a
limitation, not a qualification claim.
