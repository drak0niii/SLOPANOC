DEFECT_REGISTER.md

SLOPANOC — Canonical Defect Register

===================================================================
PURPOSE AND AUTHORITY
===================================================================

This is the single, permanent, chronologically-numbered ledger of every
CONFIRMED defect found and fixed in the SLOPANOC codebase, across every
milestone. It exists so that:

- a defect is never re-discovered and re-investigated from scratch
  because its history lived only inside a prose paragraph in `CLAUDE.md`;
- "this was fixed already" claims are verifiable against a real ID, a
  real root cause, and a real fix commit;
- non-defects (things investigated and found NOT to be a defect) are
  recorded too, so they are never re-opened on the same mistaken
  suspicion.

IDs are assigned **once, in first-discovery order, and are never
reused, renumbered, or reassigned** — even if a defect is later found to
be a duplicate of another (in that case the later ID is marked
`SUPERSEDED` and points at the earlier one; it is not deleted). This
register currently tracks `DEF-xxxx` (functional/runtime defects) only;
`SEC-xxxx` (security), `OPS-xxxx` (operational/infra), `UX-xxxx`
(presentation-only), and `DOC-xxxx` (documentation-accuracy) are
reserved prefixes for future use by this same register, sharing this
file's own conventions, once a defect of that class is confirmed.

This register was reconstructed from `CLAUDE.md`'s own milestone-closure
narrative, the real Git history (`git log --reverse`, `git show --stat`
on every commit), and direct source inspection — never from commit
titles alone. Every entry below is a **confirmed** defect: something
that was implemented, behaved incorrectly under a real or realistically-
reproduced condition, was root-caused, and was fixed with an identified
change. See "Considered and explicitly NOT registered" at the bottom of
this file for cases that were investigated and found NOT to meet that
bar — recording the negative finding is itself deliberate, per this
register's own governing instruction, so nobody re-opens them later on
the same mistaken suspicion.

**Next available ID: DEF-0048.**

(Corrected POST-6A: this line read `DEF-0037` while DEF-0037 through
DEF-0047 were all already recorded below. A stale next-ID is exactly
how two defects end up sharing one number, which this register's own
never-reuse rule exists to prevent.)
**First ID in this register: DEF-0001. Last ID currently used: DEF-0036.**
**First use of the reserved `OPS-xxxx` prefix: OPS-0001 (6A.8 migration-test target-isolation finding, below) — an operational/testing-methodology finding, not a functional application defect; no application code was found defective.**

Parent-milestone references below use this register's canonical `Pxx`/
`Pxx-Myy`/`OOB-xxxx` IDs as revised in `docs/MASTER_ROADMAP.md` §2
(major phases are not confused with individual corrective commits;
DEF-0001–DEF-0003's parent is `OOB-02`, not a `Pxx`, since D1/D2/D3/UX-1
is a cross-cutting reliability pass, not a strategic phase).

===================================================================
REQUIRED SCHEMA (every entry below follows this shape)
===================================================================

- **ID / Title**
- **Status** — one of: OPEN, FIXED, WONT-FIX, SUPERSEDED
- **Severity** — one of: CRITICAL (safety/trust-boundary/data-integrity),
  HIGH (user-visible functional break), MEDIUM (degraded/partial
  behavior with a safe fallback), LOW (cosmetic/non-blocking)
- **Detected during** — the milestone/testing activity that found it
- **Parent milestone** — canonical ID (see `docs/MASTER_ROADMAP.md`)
- **Legacy name** — the name/heading this defect is filed under in
  `CLAUDE.md`, verbatim, for cross-reference
- **Affected capability**
- **Symptom** — observed, real behavior
- **Expected** — correct behavior
- **Root cause** — the actual mechanism, not a guess
- **Corrective action** — what changed, file-level
- **Regression protection** — the test(s) that now guard this
- **Live validation** — was this defect's fix confirmed against the
  real stack (real Gemini/Vertex, real Cloud SQL, real Power
  Automate/Teams), and how
- **Fix commit SHA(s)**
- **Related tests**
- **Related docs**
- **Notes**

===================================================================
DEF-0001 — `known_message_ids` state key crashes on a rewind-cleared key
===================================================================

- **Status:** FIXED
- **Severity:** HIGH (crashed a real, otherwise-successful Teams read
  turn before Power Automate was ever called)
- **Detected during:** post-A5 reliability audit (an out-of-band
  corrective pass, not part of a named roadmap milestone)
- **Parent milestone:** OOB-02 — D1/D2/D3/UX-1 Corrective Passes
- **Legacy name:** "CORRECTIVE PASS — KNOWN_MESSAGE_IDS_STATE_KEY
  REWIND-NULL NORMALIZATION" (`CLAUDE.md`), referred to as "D1"
- **Affected capability:** Teams message retrieval, specifically after
  an edit/rewind discarded a branch that had written Teams
  evidence-validation state
- **Symptom:** `TypeError: 'NoneType' object is not iterable` inside
  `backend/tools/teams/get_messages.py`'s `_record_known_message_ids`.
- **Expected:** a rewound state key should behave identically to an
  absent one — an empty set of known message ids, never a crash.
- **Root cause:** verified against installed `google-adk==1.33.0`
  source (`Runner._compute_state_delta_for_rewind`): ADK's own rewind
  mechanism represents "revert this key to before it existed" as a
  literal `None` value written into the rewind event's `state_delta`,
  persisted as-is by both `DatabaseSessionService` and
  `InMemorySessionService` — not a missing key. Four call sites did
  `set(state.get(KEY, []))`, whose `[]` default only fires for a
  genuinely missing key, not a key present with value `None`.
- **Corrective action:** new `backend.tools.teams.get_messages
  .read_known_message_ids` — `set(state.get(KEY, []) or [])` — adopted
  by all four call sites (`get_messages.py`'s own
  `_record_known_message_ids`, `evidence.py`'s `strip_unverified_
  evidence`/`enforce_incident_manager_response_integrity`,
  `direct_read_fast_path.py`'s fast-path evidence-seeding step).
- **Regression protection:**
  `backend/tests/test_known_message_ids_rewind_normalization.py` (17
  tests) — absent key, rewind-cleared key, discarded-branch ids never
  reappearing, gateway still called normally, both `evidence.py`
  callbacks, the fast path's seeding step, the real
  `_seeded_state`→`_StateCapture`→`teams_get_messages` code path for
  both the direct fast path and a resumed SelectionCard continuation.
- **Live validation:** not separately re-run live for this specific fix
  (no live credentials in the implementing session); covered by the
  automated regression above. No live recurrence reported since.
- **Fix commit SHA(s):** `f92eb6e0abf828a53761993d4585138bc6a2643d`
- **Related tests:** `test_known_message_ids_rewind_normalization.py`
- **Related docs:** `docs/TEAMS_TOOL_CONTRACT.md`
- **Notes:** a same-class, write-only key
  (`LAST_TEAMS_EVIDENCE_STATE_KEY` in
  `backend/agents/team_manager/state_sync.py`) was audited in the same
  pass and found NOT reachable by this bug class (never read with a
  `.get(..., [])`-then-iterate pattern in production code) — left
  unchanged, correctly, not a defect.

===================================================================
DEF-0002 — OpenTelemetry cross-task context-detach lifecycle failure
===================================================================

- **Status:** FIXED
- **Severity:** MEDIUM (turns still succeeded; tracing lifecycle was
  broken on virtually every real multi-event turn, logging a real error
  on each one)
- **Detected during:** the same post-A5 reliability audit as DEF-0001
- **Parent milestone:** OOB-02 — D1/D2/D3/UX-1 Corrective Passes
- **Legacy name:** "CORRECTIVE PASS — OPENTELEMETRY CROSS-TASK
  CONTEXT-DETACH LIFECYCLE FIX (D2)" (`CLAUDE.md`)
- **Affected capability:** every streaming or non-streaming turn that
  produced more than one ADK event (i.e. essentially all real turns)
- **Symptom:** `opentelemetry.context ERROR Failed to detach context` /
  `ValueError: Token ... was created in a different Context`, logged
  repeatedly, independent of D1 and independent of Power Automate/Teams.
- **Expected:** ADK's own `tracer.start_as_current_span('invocation')`
  context should attach once and detach once, cleanly, per turn.
- **Root cause:** verified against installed `opentelemetry-api==1.41.1`
  and `google-adk==1.33.0`: `backend/api/chat_service.py`'s
  `_merge_adk_and_activity_events` (added by the same commit as Runtime
  Activity Truthfulness, OOB-01) drove the ADK Runner's own event
  generator via a FRESH `asyncio.ensure_future(agen.__anext__())` call
  on EVERY loop iteration — each resumption ran in a brand-new asyncio
  Task, and `asyncio.ensure_future`/`create_task` copies the current
  `contextvars.Context` at Task-creation time, so `attach()` (first
  resumption) and `detach()` (last resumption) ran in different
  `Context` objects. Classified as SLOPANOC lifecycle misuse, not an
  ADK or OpenTelemetry defect — every other Runner-driving call site in
  this codebase uses a plain, single-task `async for` and is
  self-consistent.
- **Corrective action:** `_drain_agen`, a single persistent task created
  exactly once, drives `agen` via a plain `async for` loop into an
  internal `asyncio.Queue` — every resumption happens inside the SAME
  Task/Context, so `attach()`/`detach()` always pair correctly.
- **Regression protection:**
  `backend/tests/test_d2_merge_adk_activity_events_context_lifecycle.py`
  (9 tests), including a direct reproduction with a real
  `contextvars.ContextVar` shaped like ADK's own span — empirically
  confirmed the OLD pattern reproduces the exact real-stack `ValueError`
  text and the fix does not.
- **Live validation:** not separately re-run live (no live credentials
  in the implementing session); the underlying mechanism is
  logging/tracing-only and does not change response content, so this
  was accepted on automated-test evidence alone, per that pass's own
  explicit scope.
- **Fix commit SHA(s):** `f92eb6e0abf828a53761993d4585138bc6a2643d`
- **Related tests:**
  `test_d2_merge_adk_activity_events_context_lifecycle.py`
- **Related docs:** none dedicated (recorded in `CLAUDE.md` only)
- **Notes:** see DEF-0003 for a second, distinct defect found while
  fixing this one.

===================================================================
DEF-0003 — `_drain_agen` deadlocks on a directly-raised `CancelledError`
===================================================================

- **Status:** FIXED
- **Severity:** CRITICAL while present (hung the entire backend test
  suite, a full deadlock, not merely a slow/degraded path) — but never
  reached production, caught by the fix's own regression pass before
  merge
- **Detected during:** the DEF-0002 fix's own full-regression run (not
  merely theorized — a real hang was observed)
- **Parent milestone:** OOB-02 — D1/D2/D3/UX-1 Corrective Passes
- **Legacy name:** "DEADLOCK FOUND AND FIXED DURING THIS PASS' OWN
  FULL-REGRESSION RUN" (`CLAUDE.md`, under the D2 section)
- **Affected capability:** `_drain_agen` (see DEF-0002) under a
  specific cancellation shape
- **Symptom:** the entire backend test suite hung indefinitely when
  running `test_cleanup_after_asyncio_cancelled_error_raised_mid_run`
  (a fake Runner that raises `CancelledError` directly mid-stream, not
  via external task cancellation).
- **Expected:** any exception raised by `agen`, including
  `CancelledError`, must propagate to the consumer exactly as before
  this refactor — never silently swallowed, never left un-relayed.
- **Root cause:** the first implementation of `_drain_agen` caught only
  `except Exception`, which does not match `asyncio.CancelledError` (a
  `BaseException` since Python 3.8) — the error propagated straight out
  of `_drain_agen` without ever reaching either `adk_queue.put()` call,
  leaving the consumer's `adk_queue.get()` awaiting forever.
- **Corrective action:** `_drain_agen` now catches `BaseException` and
  relays it through the same queue as any other error, restoring the
  original propagation contract byte-for-byte.
- **Regression protection:**
  `test_agen_raising_cancellederror_directly_does_not_deadlock`,
  explicitly bounded by `asyncio.wait_for` so a reintroduction of this
  defect class fails the test loudly instead of hanging the suite again.
- **Live validation:** not applicable (a pure async-control-flow defect,
  proven and disproven entirely at the automated-test level).
- **Fix commit SHA(s):** `f92eb6e0abf828a53761993d4585138bc6a2643d`
  (same commit as DEF-0002 — found and fixed within the same pass,
  before that pass's own closure)
- **Related tests:**
  `test_agen_raising_cancellederror_directly_does_not_deadlock`
- **Related docs:** none dedicated
- **Notes:** none

===================================================================
DEF-0004 — governed-knowledge completion remediation lost current-turn
image evidence
===================================================================

- **Status:** FIXED
- **Severity:** CRITICAL (a trust/fabrication defect: the system
  invented observed sensor/status values instead of using the real
  current-turn image)
- **Detected during:** the user's own B7 final combined live-validation
  pass (real image + Teams + Cloud SQL governed-knowledge run)
- **Parent milestone:** P07 — POST-5.1 B Multimodal Attachments (B7)
- **Legacy name:** "B7 LIVE-REGRESSION CORRECTIVE PASS —
  GOVERNED-KNOWLEDGE COMPLETION REMEDIATION LOST CURRENT-TURN IMAGE
  EVIDENCE" (`CLAUDE.md`)
- **Affected capability:** the bounded, deterministic governed-knowledge
  completion remediation (`enforce_governed_knowledge_at_completion`)
  triggered when a turn declared `requires_governed_knowledge=true` but
  finished with no selected KM evidence, for a turn that also carried a
  current-turn image
- **Symptom:** a real image (observed checksum 7318, status GREEN) +
  Teams + governed-KM run produced "Assuming the image shows a checksum
  of 7319 and a RED status indicator" — fabricated values, not the real
  image content.
- **Expected:** the remediation's own nested `incident_manager` call
  must see the SAME real current-turn image evidence the original
  delegation had, never fabricate observed values when evidence is
  actually available.
- **Root cause:** `enforce_governed_knowledge_at_completion` built its
  own nested `incident_manager` `Content` TEXT-ONLY, unconditionally.
  This remediation is a bare `Runner.run_async` call, never routed
  through `AgentTool`/`MultimodalAgentTool`, so the B6 image-propagation
  mechanism (which only intercepts `AgentTool.run_async`) never reached
  it. The ORIGINAL delegation genuinely had the image (proven by test);
  the SECOND, remediation execution genuinely did not.
- **Corrective action:** `backend/api/multimodal_turn_context.py` gained
  `trusted_image_parts_from_content(content)` (the same filter predicate
  `MultimodalAgentTool` already uses, deliberately duplicated rather
  than cross-imported). `enforce_governed_knowledge_at_completion`
  gained an `image_parts` parameter (default `()`, byte-identical
  behavior for a text-only turn), appended after the structured-request
  text part. `chat_service.py`'s remediation call site passes
  `image_parts=trusted_image_parts_from_content(content)` using the SAME
  already-built, already-validated turn `Content` object the original
  team_manager Runner call used — no new registry, no re-derivation from
  attachment ids. `source_requirements_completion.py` was audited and
  deliberately left unchanged (it only ever calls
  `record_source_requirements`, a boolean-tuple classification, and
  never produces user-facing answer text, so it cannot itself fabricate
  an observed value).
- **Regression protection:**
  `backend/tests/test_p5_1_b7_governed_completion_image_evidence.py`
  (17 tests) — the most important one drives a real `ChatService`/
  `AttachmentService`/Teams-gateway-mock/isolated-KM-repository pipeline
  through the exact live-reproduced trigger and proves, directly against
  `llm_request.contents`, that the remediation model's own first
  reasoning step genuinely receives the same trusted image Part; plus a
  text-only-turn no-change proof, a no-recursive-remediation-call proof,
  `Part.from_bytes` proven never invoked, and `gs://`/bucket-name
  absence from the final answer.
- **Live validation:** YES — Test F of the B7 final real-stack live
  validation pass reproduced the exact combined image+Teams+governed-KM
  scenario and confirmed the correct FAIL (7318 != 7319), correct Teams
  context, correct escalation guidance, and explicitly confirmed NO
  invented RED value and NO "assuming" substitution — this is the live
  proof this fix closed the real defect it targeted.
- **Fix commit SHA(s):** `c154f71` ("feat: complete B7 attachment
  lifecycle and durable provenance")
- **Related tests:** `test_p5_1_b7_governed_completion_image_evidence.py`
- **Related docs:** `docs/TEAMS_TOOL_CONTRACT.md`, `docs/AGENT_CONTRACT.md`
- **Notes:** the fix is structural (passing the same real trusted
  `Part` objects through an existing call), per the explicit instruction
  that governed this correction: "the solution must be structural, not
  keyword-based."

===================================================================
DEF-0005 — exact-duplicate governed-KM provenance chips
===================================================================

- **Status:** FIXED
- **Severity:** MEDIUM (presentation/provenance-accuracy defect —
  correct answer, but misleading duplicated evidence chip; durably
  persisted, so it also survived a hard refresh)
- **Detected during:** the user's own re-run of the DEF-0004 fix
- **Parent milestone:** P07 — POST-5.1 B Multimodal Attachments (B7)
- **Legacy name:** "B7 LIVE-REGRESSION CORRECTIVE PASS —
  EXACT-DUPLICATE GOVERNED-KM PROVENANCE" (`CLAUDE.md`)
- **Affected capability:** Source drawer governed-knowledge provenance
  chips
- **Symptom:** the UI showed FOUR source chips instead of three — Teams,
  Verification, **Verification again**, Escalation — for a single
  correct answer; the duplicate survived a hard refresh (durably
  persisted/reprojected, not a transient frontend artifact).
- **Expected:** each distinct `(knowledge_id, version_label,
  section_id)` identity should render as exactly one chip.
- **Root cause:** audited first, per the governing instruction, before
  any code change. `KnowledgeEvidenceSet`'s own Pydantic model
  validator, `select_evidence`'s guarded append, and
  `build_knowledge_source_references` were all found ALREADY CORRECT —
  no reproducible in-process defect exists in any of them (the exact
  live-Gemini trigger could not be reproduced without live Cloud
  SQL/Gemini access). What WAS found missing: neither
  `turn_source_references.py`'s persistence write NOR its read-time
  history projection had ANY exact-identity normalization of their own
  — so a duplicate reaching either boundary, from any cause, would be
  faithfully persisted/reprojected forever.
- **Corrective action:**
  `backend/api/knowledge_source_reference.py` gained
  `dedupe_knowledge_source_references(references)` — identity-only
  (`knowledge_id`/`version_label`/`section_id`, never title/heading/
  display-name/content, which can legitimately collide for genuinely
  distinct references), first-seen order preserved. Wired at TWO points:
  `chat_service.py`'s own `knowledge_sources` finalization (shared by
  both the live SSE event and the persisted record), and
  `turn_source_references.py`'s `resolve_turn_source_references` as a
  read-time-only safety net for anything already persisted with a
  duplicate.
- **Regression protection:**
  `backend/tests/test_p5_1_b7_km_provenance_exact_duplicate.py` (13
  tests) — exact duplicate, same-document-different-section, same-
  knowledge-id-different-version, deterministic order, persistence/
  read-time-projection boundary tests, and a real end-to-end pipeline
  test proving the live SSE event, the persisted record, and `GET
  /history`'s own projection all show exactly 3 chips (Teams +
  Verification + Escalation), never a duplicate.
- **Live validation:** YES — Test G of the B7 final real-stack live
  validation pass confirmed the real UI rendered exactly three distinct
  source references, no duplicate Verification chip.
- **Fix commit SHA(s):** `c154f71` ("feat: complete B7 attachment
  lifecycle and durable provenance")
- **Related tests:** `test_p5_1_b7_km_provenance_exact_duplicate.py`
- **Related docs:** `docs/KNOWLEDGE_CONTRACT.md`
- **Notes:** frontend needed no change — `AppState.tsx`'s reducers
  already REPLACE (never append/accumulate) a message's own
  `knowledgeSources` array on every write, confirmed by audit and by
  re-running the existing frontend provenance test suite unchanged.

===================================================================
DEF-0006 — A5 live-runtime: model did not reliably do one diagnostic
action at a time
===================================================================

- **Status:** FIXED
- **Severity:** HIGH (a product-safety/UX principle violation — the
  NON-NEGOTIABLE "one check at a time" troubleshooting strategy — found
  reproducibly, twice, including after a prompt-strengthening attempt)
- **Detected during:** A5 first live-runtime validation pass (real
  Vertex Gemini, real Cloud SQL)
- **Parent milestone:** P09-M01 — A5 Knowledge Island Ingestion
- **Legacy name:** "A5 FIRST LIVE-RUNTIME VALIDATION PASS" /
  "A5 FINAL CORRECTIVE IMPLEMENTATION PASS — CORRECTION A" (`CLAUDE.md`)
- **Affected capability:** Incident Manager troubleshooting guidance —
  default posture for "what should I check?"-style questions
- **Symptom:** the model gave 3-4 step numbered procedures even when
  asked "what should I check?", reproducibly, twice — including after a
  legitimate attempt to strengthen the prompt wording.
- **Expected:** one grounded diagnostic action per turn by default,
  unless the user explicitly asks for the full procedure.
- **Root cause:** free-text prompt-only self-restraint proved
  unreliable under real model sampling — a prompt-wording fix could not
  guarantee the property; a deterministic runtime mechanism was needed.
- **Corrective action:** `backend/agents/incident_manager/schemas.py`
  gained a typed `TroubleshootingGuidance` field
  (`TroubleshootingInteractionMode`: `NEXT_STEP`/`FULL_PROCEDURE`;
  `TroubleshootingStep`) on `IncidentManagerResponse`, populated via
  Gemini's own schema-guided structured output.
  `backend/api/troubleshooting_guidance_context.py` (new) is a
  run-scoped store plus `render_troubleshooting_guidance`, a pure
  deterministic renderer that in `NEXT_STEP` mode structurally NEVER
  reads `full_procedure_steps` at all — leaking a later step is a code
  path that does not exist, not merely a discouraged prompt behavior.
  `chat_service.py` gained a hard completion-boundary override that
  unconditionally replaces the turn's final answer with the
  deterministic rendering whenever guidance was populated.
- **Regression protection:** 14 tests for
  `troubleshooting_guidance_context.py`'s store/renderer (NEXT_STEP
  never leaks later steps; FULL_PROCEDURE allows multiple grounded
  steps; command preservation), 8 tests for `evidence.py`'s wiring.
- **Live validation:** YES — A5 final live-runtime retest, Tests A/B/C
  (one-command first turn, same-session follow-up, explicit
  full-procedure override) all PASSED, with rendered content confirmed
  to match `render_troubleshooting_guidance`'s deterministic output
  byte-for-byte after the DEF-0007 fix below (see that entry — the FIRST
  live rerun after Correction A still failed, for a different reason).
- **Fix commit SHA(s):** `6ce4097` ("feat: complete A5 knowledge island
  ingestion")
- **Related tests:** troubleshooting-guidance-focused subset in the A5
  test suite
- **Related docs:** `docs/TROUBLESHOOTING_STRATEGY.md`
- **Notes:** a related, honestly-recorded non-defect: A5 Test D found
  that a bare, non-question first turn (no explicit question) did not
  populate `troubleshooting_guidance` at all — CLAUDE.md explicitly
  records this as "a live wording-sensitivity worth recording honestly,
  but the underlying safety property held: nothing executable leaked."
  This is NOT registered as a separate defect (see "Considered and
  explicitly NOT registered" below) — no fix was made or judged
  necessary, since no unsafe content leaked.

===================================================================
DEF-0007 — troubleshooting guidance discarded before completion-boundary
consumption
===================================================================

- **Status:** FIXED
- **Severity:** CRITICAL while present (silently defeated DEF-0006's
  own fix — the exact mechanism that fix exists to guarantee — falling
  back to the unreliable free-text paraphrase it was built to eliminate)
- **Detected during:** the first live rerun immediately after the
  DEF-0006 fix was deployed
- **Parent milestone:** P09-M01 — A5 Knowledge Island Ingestion
- **Legacy name:** "LIVE DEFECT FOUND AND FIXED DURING THIS
  CORRECTION" (`CLAUDE.md`, under "A5 FINAL CORRECTIVE IMPLEMENTATION
  PASS")
- **Affected capability:** the DEF-0006 completion-boundary override
- **Symptom:** `troubleshooting_guidance` was genuinely populated by the
  model (confirmed by direct instrumentation), but the user-visible
  answer still did not match the deterministic renderer's output at
  all.
- **Expected:** the completion-boundary override must consume the exact
  guidance the model populated for that turn.
- **Root cause:** `chat_service.py`'s own turn-scoped cleanup `finally`
  block called `discard_troubleshooting_guidance(run_id)` BEFORE the
  later completion-boundary override ever got to
  `pop_troubleshooting_guidance(run_id)` — the captured guidance was
  wiped by this codebase's own cleanup discipline before it could be
  consumed, silently falling back to team_manager's own free-text
  paraphrase.
- **Corrective action:** adopted the same snapshot-before-discard shape
  already used for `selected_knowledge_evidence` in the same `finally`
  block — the guidance is now popped (read + cleared) into a local
  variable inside that early `finally`, and the later override consumes
  the local snapshot rather than re-popping an already-cleared store.
- **Regression protection:** covered by the same troubleshooting-
  guidance-context test suite as DEF-0006 (snapshot/consumption
  ordering); re-verified live immediately after the fix.
- **Live validation:** YES — re-verified live immediately after the fix:
  the rendered answer matched `render_troubleshooting_guidance`'s exact
  output byte-for-byte. CLAUDE.md itself calls this "the single most
  safety-relevant defect found in this entire A5 effort, since it is
  the exact mechanism the whole correction exists to guarantee."
- **Fix commit SHA(s):** `6ce4097` ("feat: complete A5 knowledge island
  ingestion")
- **Related tests:** troubleshooting-guidance-focused subset
- **Related docs:** `docs/TROUBLESHOOTING_STRATEGY.md`
- **Notes:** found and fixed within the same implementation pass as
  DEF-0006, before that pass's own closure — both landed in the same
  commit.

===================================================================
DEF-0008 — Pydantic `default_factory` serialization failure inside ADK
tracing
===================================================================

- **Status:** FIXED
- **Severity:** CRITICAL while present (broke every real LLM call
  through `incident_manager` — 24 test files failed with the same
  error; would have broken real production conversational turns)
- **Detected during:** the A5 corrective pass's own full backend test
  suite run (not live Gemini testing)
- **Parent milestone:** P09-M01 — A5 Knowledge Island Ingestion
- **Legacy name:** "SEPARATE LIVE DEFECT FOUND AND FIXED (Correction D
  wiring)" (`CLAUDE.md`)
- **Affected capability:** `IncidentManagerRequest.known_applicability_
  facts` (introduced by the same A5 corrective pass, Correction D)
- **Symptom:**
  `pydantic_core.PydanticSerializationError: Unable to serialize
  unknown type: ..._HAS_DEFAULT_FACTORY_CLASS` inside ADK's own
  production tracing code (`google.adk.telemetry.tracing.trace_call_
  llm`) on every real LLM call through `incident_manager`.
- **Expected:** the new field must serialize cleanly through ADK's own
  tracing path, like every other field on the same schema.
- **Root cause:** the field was first declared as `dict[str, list[str]]
  = Field(default_factory=dict, ...)` — the `default_factory` sentinel
  itself is not a type ADK's tracing serializer(a plain, unrelated
  utility) knows how to handle.
- **Corrective action:** changed to `Optional[dict[str, list[str]]] =
  Field(default=None, ...)` — a plain `None` default instead of
  `default_factory=dict`.
- **Regression protection:** confirmed clean against the isolated
  originally-failing test, then the full backend suite (2904 passed, 1
  skipped at that point).
- **Live validation:** not separately re-run live for this specific
  serialization fix (it is a pure schema/serialization defect,
  reproducible and provable entirely at the test level); covered by the
  A5 final live-runtime retest succeeding at all (this defect would have
  blocked every one of those 8 live gates had it shipped unfixed).
- **Fix commit SHA(s):** `6ce4097` ("feat: complete A5 knowledge island
  ingestion")
- **Related tests:** full backend suite (regression-only, no single
  dedicated defect-reproduction test file — the fix is a one-line schema
  change validated by the suite that was already failing)
- **Related docs:** `docs/KNOWLEDGE_CONTRACT.md`
- **Notes:** found via the automated suite, not live testing — included
  here because it is a genuine, confirmed, fixed defect with a real
  root cause and fix commit, meeting this register's inclusion bar
  regardless of how it was discovered.

===================================================================
DEF-0009 — Teams rich-content requests silently intercepted by the
text-only exact-read fast path
===================================================================

- **Status:** FIXED
- **Severity:** HIGH (a real user-visible failure: "No matching Teams
  content was found" for a request that should have succeeded)
- **Detected during:** live validation of the 5.X first-slice milestone
- **Parent milestone:** P10 — 5.X Teams Rich Content / Media Retrieval
- **Legacy name:** "TEAMS RICH CONTENT ROUTING — CORRECTIVE MILESTONE"
  (`CLAUDE.md`)
- **Affected capability:** Teams inline-image discovery/retrieval,
  reached via `incident_manager`
- **Symptom:** a request needing Teams-posted visual content (e.g.
  "read the latest image... and tell me what is shown in it") was
  silently intercepted by the text-only exact-read fast path
  (`direct_read_fast_path.py`) — live evidence showed
  `teams.getMessages` succeeding but `teams.getHostedContent` NEVER
  being called, ending in "No matching Teams content was found."
- **Expected:** a rich-content-requiring read must run through
  `incident_manager`'s own full tool-calling turn, where
  `teams_get_hosted_content`/`teams_get_all_hosted_content` can actually
  be called after `teams_get_messages`.
- **Root cause:** the fast path had NO structural signal distinguishing
  an ordinary text read from a rich-content read — any unique
  `teams_list_chats` match for a non-write read was eligible. Once
  intercepted, the shortcut hands off to `read_continuation_
  execution.py`'s synthesis-only agents (`tools=[]`) — structurally
  incapable of a second tool call after `teams_get_messages`, so
  retrieval genuinely succeeded but the hosted-content tool was never
  reachable from inside that shortcut, by construction.
- **Corrective action:** reused the existing structured-signal
  precedent (5.1J's `requires_governed_knowledge`, B6's image-evidence
  gate). Added `IncidentManagerRequest.requires_rich_content: bool`
  (default `False`, fail-closed), set by `team_manager`'s own semantic
  judgment via a new "TEAMS RICH CONTENT DELEGATION" prompt paragraph, a
  new `_requires_rich_content` gate function in
  `direct_read_fast_path.py` checked alongside the two existing gates —
  when true, the fast-path marker is never written, so
  `incident_manager`'s normal full tool-calling turn runs instead. No
  keyword/regex routing — the gate reads a typed JSON field only,
  proven by a dedicated source-scan test.
- **Regression protection:**
  `backend/tests/test_teams_rich_content_routing.py` (20 tests) —
  ordinary text read stays fast-path eligible; rich-content read is not;
  bypass is a silent no-op; tool registration correctness; fail-closed
  edge cases; ambiguous/not-found/write resolutions provably unaffected;
  source-level proof of no keyword/regex routing.
- **Live validation:** NOT performed at the time this fix shipped (no
  live Power Automate/Gemini credentials in that session) — ready for
  live validation per that milestone's own checklist; subsequently
  exercised (implicitly) by the 5.X image-vision/multi-image/visual-
  evidence live validation described under P10 in
  `docs/MASTER_ROADMAP.md`, which required rich-content requests to
  reach `teams_get_hosted_content`/`teams_get_all_hosted_content`
  successfully.
- **Fix commit SHA(s):** `0407808805d8388d81602d2f66cdfa5b0164805f`
- **Related tests:** `test_teams_rich_content_routing.py`
- **Related docs:** `docs/TEAMS_TOOL_CONTRACT.md`
- **Notes:** a deferred, explicitly out-of-scope, related gap was
  audited and recorded, not fixed: a rich-content request that ALSO
  hits ambiguous chat resolution (SelectionCard) and is resumed after
  selection would face the SAME structural limitation once resumed,
  since `read_continuation_execution.py` is frozen and out of scope for
  this fix. Not registered as a separate defect — it is a known,
  explicitly-deferred limitation of the selection-continuation resume
  path, not a newly discovered defect in delivered behavior; see
  `docs/MASTER_ROADMAP.md`'s P10 entry.

===================================================================
DEF-0010 — hosted-image ordinal duplication across separate injection
calls
===================================================================

- **Status:** FIXED
- **Severity:** HIGH (visible, incorrect image numbering in a
  multi-image answer/Source-drawer display)
- **Detected during:** live validation of the 5.X deterministic
  all-image retrieval work
- **Parent milestone:** P10 — 5.X Teams Rich Content / Media Retrieval
- **Legacy name:** not separately named in `CLAUDE.md` (discovered and
  fixed within the session that produced commit `0407808`, before that
  commit's own `CLAUDE.md` narrative was written for this specific
  sub-defect)
- **Affected capability:** `inject_pending_hosted_content_image`'s
  ordinal assignment (`backend/api/hosted_content_vision_context.py`)
- **Symptom:** a real run delivering two images both got `ordinal=1`
  instead of distinct values.
- **Expected:** each delivered image's ordinal must reflect its true
  position in the message's own canonical discovery order, globally
  correct across the whole run.
- **Root cause:** `enumerate(ordered_images, start=1)` restarted at 1 on
  EVERY separate `inject_pending_hosted_content_image` invocation; the
  model called the retrieval tool across SEPARATE model turns (not one
  batched multi-function-call response), so each turn's own injection
  call re-numbered its own small batch from 1.
- **Corrective action:** derive `ordinal` from the image's true position
  in `order_map[image.message_id]` (via
  `.index(hosted_content_id) + 1`) instead of per-call enumeration, with
  a stable fallback for the defensive "unknown order" case.
- **Regression protection:**
  `test_ordinal_is_globally_correct_across_multiple_separate_injection_
  calls_in_one_run` (new regression test added specifically for this
  defect).
- **Live validation:** YES — re-validated live after the fix: a real
  3-image Teams message was correctly retrieved and rendered as
  "Visual evidence · 3 images analyzed" with 3 correctly-ordered/
  labeled thumbnails, confirmed via backend log, API response capture,
  and browser screenshot.
- **Fix commit SHA(s):** `0407808805d8388d81602d2f66cdfa5b0164805f`
- **Related tests:** `backend/tests/test_teams_visual_evidence.py`,
  hosted-content-vision-context focused tests
- **Related docs:** `docs/TEAMS_TOOL_CONTRACT.md` §4b
- **Notes:** DEF-0011 was discovered immediately while fixing this
  defect (see that entry) — the first attempted fix for DEF-0010 was
  itself too aggressive and caused DEF-0011.

===================================================================
DEF-0011 — `_message_order` consumed (popped) on first use, breaking a
second injection call in the same run
===================================================================

- **Status:** FIXED
- **Severity:** HIGH (a second batch of images in the same run silently
  lost their canonical order)
- **Detected during:** the DEF-0010 fix's own immediate follow-up
  testing, in the same live-validation session
- **Parent milestone:** P10 — 5.X Teams Rich Content / Media Retrieval
- **Legacy name:** not separately named in `CLAUDE.md` (see DEF-0010)
- **Affected capability:** `inject_pending_hosted_content_image`'s
  order-map lifecycle (`backend/api/hosted_content_vision_context.py`)
- **Symptom:** a real run's SECOND separate `inject_pending_hosted_
  content_image` call found an EMPTY order map, even though the order
  had genuinely been recorded earlier in the same run.
- **Expected:** the canonical order map must remain readable for every
  injection call within a run, not just the first.
- **Root cause:** an earlier, over-aggressive fix for a different
  no-op-call edge case had changed `_message_order` access to
  `.pop()` on first *successful* (non-empty) use — correctly avoiding a
  stale-order bug on a harmless no-op call, but incorrectly also
  consuming/removing the map the first time it was genuinely used,
  leaving nothing for a legitimate second use later in the same run.
- **Corrective action:** changed `order_map =
  _message_order.pop(run_id, {})` to `order_map =
  _message_order.get(run_id, {})` (read-only, never consumed during
  injection) — cleanup deferred entirely to
  `discard_pending_hosted_content_image`'s own `finally`-block call.
- **Regression protection:** covered by the same
  `hosted_content_vision_context` test suite as DEF-0010, extended to
  cover a second injection call in one run.
- **Live validation:** YES — same live validation pass as DEF-0010 (the
  fix for DEF-0011 was required before DEF-0010's own live confirmation
  could pass with multiple images).
- **Fix commit SHA(s):** `0407808805d8388d81602d2f66cdfa5b0164805f`
- **Related tests:** hosted-content-vision-context focused tests
- **Related docs:** `docs/TEAMS_TOOL_CONTRACT.md` §4b
- **Notes:** this defect was itself caused by a fix for a different,
  earlier-discovered no-op-call edge case within the same
  implementation pass — not separately registered, since it was fixed
  before ever reaching a committed, user-visible state.

===================================================================
DEF-0012 — image-only Teams message produced no Source reference for
its own delivered visual evidence
===================================================================

- **Status:** FIXED
- **Severity:** MEDIUM (a genuinely successful, correctly-working
  image-retrieval turn had no Source drawer entry at all — a
  provenance-completeness gap, not an incorrect-answer defect)
- **Detected during:** live validation of the 5.X Source Visual Evidence
  work
- **Parent milestone:** P10 — 5.X Teams Rich Content / Media Retrieval
- **Legacy name:** not separately named in `CLAUDE.md` (see the P10
  entry in `docs/MASTER_ROADMAP.md`)
- **Affected capability:** Source drawer visual evidence for an
  image-only Teams message (no substantive text)
- **Symptom:** `message.completed` had NO `source` field at all for a
  genuinely successful, correctly-working image-retrieval turn.
- **Expected:** a turn with real delivered images must always get a
  Source reference exposing that visual evidence, even if the
  originating Teams message had no substantive text.
- **Root cause:** `TeamsSourceCapture.build_source_reference()` returns
  `None` whenever `incident_manager`'s own textual `evidence` citation
  list is empty — which happens for a message with no substantive TEXT
  (only images) — and the entire `if source_reference is not None:`
  block (which built and attached `visual_evidence`) never executed.
- **Corrective action:** added
  `ensure_source_reference_for_visual_evidence` (new pure function in
  `backend/api/source_reference.py`) that synthesizes a minimal
  `SourceReferenceDTO` (title=None, message_count=None, period_start/
  end=None, evidence=[]) when there's no textual evidence but real
  delivered images exist for the resolved `chat_id`; required moving
  `chat_id` computation outside/before the `if source_reference is not
  None:` gate in `chat_service.py` so it is available regardless of
  whether text evidence existed.
- **Regression protection:** covered by
  `backend/tests/test_teams_visual_evidence.py`'s image-only-message
  cases.
- **Live validation:** YES — the same live validation pass confirmed
  the Source drawer correctly showed "Visual evidence · 3 images
  analyzed" for the real test message.
- **Fix commit SHA(s):** `0407808805d8388d81602d2f66cdfa5b0164805f`
- **Related tests:** `test_teams_visual_evidence.py`
- **Related docs:** `docs/TEAMS_TOOL_CONTRACT.md` §4c
- **Notes:** none

===================================================================
DEF-0013 — external append_event mid-run races ADK's session-revision
staleness check ("could not complete this request")
===================================================================

- **Status:** FIXED
- **Severity:** CRITICAL (broke the first live Gemini/Vertex smoke test
  involving a function-call tool turn — a genuine, reproducible
  production-shaped failure, not an edge case)
- **Detected during:** POST-5.1 B, sub-milestone B4B — the first live
  Gemini/Vertex smoke test after B4B's own backend implementation
- **Parent milestone:** P07 — POST-5.1 B Multimodal Attachments (B4B)
- **Legacy name:** "B4B RUNTIME DEFECT FIX" (`CLAUDE.md`)
- **Affected capability:** any real conversational turn that involves a
  function-call/tool continuation (i.e. most real Incident Manager
  turns), specifically the interaction between
  `record_user_turn_activity`'s external session state write and ADK's
  own in-flight `Runner.run_async` event loop
- **Symptom:** the first live Gemini/Vertex smoke test (a function-call
  tool turn) failed twice with a generic "could not complete this
  request" and no model continuation. Only a durable user event plus a
  bookkeeping event per attempt were ever persisted in the real failed
  Cloud SQL session — no function-call/model event — consistent with
  the Runner's own next append failing before it could be written.
- **Expected:** a real conversational turn involving a tool/function
  call must complete normally; SLOPANOC's own bookkeeping writes must
  never interfere with ADK's own in-flight session persistence for the
  same invocation.
- **Root cause:** root-caused by direct inspection of installed
  `google-adk==1.33.0`'s `Runner.run_async` event loop and
  `DatabaseSessionService.append_event`'s session-revision staleness
  check, confirmed via a disposable local reproduction against a real
  `DatabaseSessionService`. An external `get_session()`/`append_event()`
  call made MID-RUN (while `Runner.run_async` was still yielding further
  events for the same invocation) races ADK's own session-revision
  staleness tracking and breaks the Runner's own next internal append
  (e.g. a tool's function-response) — SLOPANOC's original
  `record_user_turn_activity` call site did exactly this.
- **Corrective action:** deferred the `record_user_turn_activity` call
  to POST-RUN finalization — `chat_service.py`'s `_run_turn_events`'s own
  `finally` block, only after the active `Runner.run_async` invocation
  has fully terminated, never while it is still yielding further events
  for the same invocation. A failed or cancelled assistant turn still
  leaves the session correctly marked/advanced, because the deferred
  write still runs from `finally` after a failure. Reuses the same
  re-fetch-then-write pattern already proven elsewhere in this codebase
  for the same bug class (`_reload_and_persist_cleanup_delta`).
- **Regression protection:**
  `backend/tests/test_chat_service_function_call_continuation.py`
  (function-call continuation, no-final-answer, and multi-turn
  regressions, plus two tests proving the underlying ADK mechanism
  directly).
- **Live validation:** YES — the retry succeeded end-to-end against the
  real stack (session `606a7ba1-da70-4131-b4d2-f038ebc84dc9`): real
  Vertex Gemini 2.5 Flash returned the requested reply with normal
  model-call → assistant-text → generation-complete → run-complete
  behavior and no recurrence of the earlier generic failure; a full
  backend process restart preserved session visibility, the manual
  title, and `chat_activity_at` identically. The earlier defect-evidence
  session was left intact, read-only, not deleted.
- **Fix commit SHA(s):** `71ed8ea` ("feat: add durable saved chat history
  backend") — B4B lands within POST-5.1 B's own multi-commit range
  (`71ed8ea`/`c4a67f3`/`d26395c`/`06da862`); this specific defect's fix
  is recorded in `CLAUDE.md` under B4B, which this register attributes to
  `71ed8ea` as the closest-matching commit in that range by content
  (session-history-service/session-state-keys introduction) — an exact
  single-commit attribution below the phase level was not independently
  re-verified beyond that by this audit pass, consistent with this
  register's standing practice of citing the real phase-level commit
  range honestly rather than inventing false sub-commit precision it
  cannot verify.
- **Related tests:** `test_chat_service_function_call_continuation.py`
- **Related docs:** none dedicated
- **Notes:** this is the specific "ADK saved-chat/session-revision race"
  defect referenced by this register's own governing audit checklist —
  distinct from DEF-0002/DEF-0003 (OpenTelemetry context-detach/
  `CancelledError` deadlock), which are a different mechanism
  (`contextvars`/tracing) despite superficial thematic similarity
  ("something about ADK's Runner event loop and external state").

===================================================================
DEF-0014 — image-only turn dropped from saved-chat history projection
===================================================================

- **Status:** FIXED
- **Severity:** HIGH (an image-only user turn would not count toward
  `has_visible_message`/`chat_activity_at`/legacy-repair, and would not
  reliably appear in the saved-chat list or history)
- **Detected during:** POST-5.1 B, sub-milestone B5's own implementation
  audit (found during B5's own audit, not live Gemini testing)
- **Parent milestone:** P07 — POST-5.1 B Multimodal Attachments (B5)
- **Legacy name:** "BUG FOUND + FIXED during this pass' own audit"
  (`CLAUDE.md`, under the B5 entry)
- **Affected capability:** `backend/api/session_history_service.py`'s
  turn projection (`_user_text` / genuine-turn classification)
- **Symptom:** `_user_text` returning `None` for an image-only user turn
  (no text part at all) was treated as "not a genuine turn" — an
  image-only turn would be silently dropped from
  `has_visible_message`/`chat_activity_at`/legacy-repair projection.
- **Expected:** an image-only user turn is exactly as genuine as a
  text-bearing one and must be counted/projected identically (with an
  empty string for its text, never treated as absent).
- **Root cause:** the turn-projection logic used `_user_text(event) is
  None` as its "not a genuine turn" test, which is correct for a truly
  empty/non-genuine event but incorrectly also matches a real turn whose
  `Content` carries only `file_data`/URI parts and no text part — a
  case B5 introduced (image-only sends) that did not exist before B5.
- **Corrective action:** turn projection now treats an image-only
  genuine user turn as genuine, using `_user_text(event) or ""` for its
  text (never `None`) — `chat_title` still correctly falls back to "New
  chat" when the first turn is image-only with no text.
- **Regression protection:** covered by
  `backend/api/session_history_service.py`'s own test suite (image-only
  turn now appears with `text=""` and counts toward
  `has_visible_message`/`chat_activity_at`); `gs://` URI still never
  exposed via `GET /history` (regression-tested in the same pass).
- **Live validation:** YES — covered by B5's own live multimodal
  validation pass (real disposable session
  `469f7cad-f60b-4a67-b079-ba52cc1bed90`), which included a real image
  send and confirmed correct history/session behavior end to end.
- **Fix commit SHA(s):** `06da862` ("feat: add Gemini multimodal
  attachment runtime")
- **Related tests:** `backend/tests/test_session_history_service.py`
- **Related docs:** `docs/AGENT_CONTRACT.md`
- **Notes:** distinct from DEF-0012 (a LATER, unrelated "image-only"
  gap in P10/5.X: an image-only Teams-sourced turn producing no Source
  drawer reference). DEF-0014 is about SLOPANOC's own saved-chat
  history projection for a directly-uploaded image-only send (P07/B5);
  DEF-0012 is about Source/provenance attachment for a Teams-retrieved
  image-only message (P10). Also distinct from DEF-0015 below (a THIRD,
  separate "image-only message" gap, in Teams reasoning-content
  exclusion rather than history projection or Source attachment) — three
  genuinely different root causes in three different files/mechanisms,
  not merged despite the shared surface-level theme.

===================================================================
DEF-0015 — image-only Teams message excluded from reasoning before
hosted_content_ids could reach Incident Manager
===================================================================

- **Status:** FIXED
- **Severity:** HIGH (would have silently defeated the entire 5.X
  first-slice capability for its own primary real-world target case —
  an inline/pasted image with no accompanying text)
- **Detected during:** 5.X first-slice milestone's own test-writing (not
  live testing — found while writing tests, before the first live
  attempt)
- **Parent milestone:** P10 — 5.X Teams Rich Content / Media Retrieval
  (first slice)
- **Legacy name:** "REAL BUG FOUND AND FIXED DURING THIS PASS' OWN
  TEST-WRITING" (`CLAUDE.md`, under the 5.X first-slice entry)
- **Affected capability:** `backend/tools/teams/system_events.py`'s
  `is_excludable_from_reasoning` / the Teams message normalization path
  feeding evidence to Incident Manager
- **Symptom:** an inline Teams image is represented as a bare `<img>`
  tag, which `html_text.normalize_teams_content` produces NO text for
  (unlike `<attachment>`, which becomes `"[Attachment]"`) — an
  image-only message (the milestone's own real validation target,
  message `1789114360805`, whose own example payload shows
  `"text": ""`) would normalize to empty text and be silently discarded
  by the pre-existing `is_excludable_from_reasoning`'s "no meaningful
  content" rule, BEFORE `hosted_content_ids` could ever reach
  `incident_manager` — i.e. the single most important real-world case
  for this milestone (an image with no caption) would never even be
  seen.
- **Expected:** a message carrying real hosted content (an image) must
  never be excluded from reasoning merely because it has no text.
- **Root cause:** `is_excludable_from_reasoning`'s "no meaningful
  content" rule had no signal distinguishing "genuinely empty message"
  from "image-only message with real hosted content" — both normalized
  to empty text and were treated identically.
- **Corrective action:** added an explicit `has_hosted_content`
  parameter (default `False`, every pre-existing caller/behavior
  byte-for-byte unchanged) to `is_excludable_from_reasoning` that
  exempts ONLY the empty-text exclusion — a genuine system/event marker
  is still always excluded first, unconditionally, so rich content can
  never smuggle a system/event entry into evidence. Wired at
  `get_messages.py`'s call site via
  `has_hosted_content=bool(msg.hosted_content_ids)`.
- **Regression protection:** 3 new unit tests in
  `backend/tests/test_system_events.py`, plus integration coverage in
  `test_teams_get_messages_hosted_content.py` (population, system-event/
  pagination/coverage non-regression).
- **Live validation:** NOT performed for the first-slice milestone
  itself (no live Power Automate/Gemini credentials in that session);
  the underlying mechanism was subsequently exercised by the full 5.X
  live-validation pass (image vision/multi-image/visual-evidence), which
  required image-only Teams messages to reach `incident_manager`
  successfully.
- **Fix commit SHA(s):** `0407808805d8388d81602d2f66cdfa5b0164805f`
- **Related tests:** `test_system_events.py`,
  `test_teams_get_messages_hosted_content.py`
- **Related docs:** `docs/TEAMS_TOOL_CONTRACT.md` §4b
- **Notes:** see DEF-0014's own note for how this differs from the two
  other "image-only X" defects (DEF-0012, DEF-0014) found across this
  codebase's multimodal work — three distinct root causes, not merged.

===================================================================
DEF-0016 — model-driven ALL-images retrieval nondeterministically
retrieved only a subset of requested images
===================================================================

- **Status:** FIXED
- **Severity:** HIGH (a real, reproducible functional gap: a user asking
  for "all" images received an incomplete answer with no indication
  anything was missing)
- **Detected during:** live validation of the 5.X deterministic
  all-image retrieval work (real Vertex Gemini)
- **Parent milestone:** P10 — 5.X Teams Rich Content / Media Retrieval
  (deterministic all-image retrieval sub-milestone)
- **Legacy name:** "DETERMINISTIC ALL-IMAGE RETRIEVAL" (`CLAUDE.md`,
  under the full 5.X-scope entry) — this register's own audit found this
  specific nondeterminism issue had never been given its own `DEF-xxxx`
  ID despite being a genuine, confirmed, fixed defect distinct from
  DEF-0010/DEF-0011 (which are bugs found WHILE fixing this one, in the
  fix's own supporting `ordinal`/`order_map` machinery, not this defect
  itself)
- **Affected capability:** Gemini's own free-choice tool-calling
  behavior when asked to retrieve "all" hosted images for a message via
  the (then only available) single-image `teams_get_hosted_content` tool
- **Symptom:** in a real live run, Gemini sometimes retrieved only 2 of
  3 images despite being asked for all of them and all being within the
  configured count/byte limits — a model free-choice reliability
  problem, not a backend validation/gateway bug (every retrieval that
  WAS attempted succeeded normally).
- **Expected:** a user request for "all" images must deterministically
  result in every eligible image being retrieved, never left to model
  discretion.
- **Root cause:** the only retrieval mechanism available at the time
  required the model to individually choose and call
  `teams_get_hosted_content` once per image it decided to retrieve —
  this is inherent LLM sampling/tool-selection nondeterminism, not a
  deterministic code defect in the traditional sense, but it violates
  this codebase's own "agent vs. tool" boundary principle
  (`docs/AGENT_CONTRACT.md` §5: a deterministic capability must never be
  left to an agent-driven loop) and was treated as a confirmed defect on
  that basis — consistent with this register's own precedent for
  DEF-0006 (A5's one-command troubleshooting reliability), which is the
  same class of "model free-choice reliability problem" fixed with a
  deterministic mechanism rather than accepted as inherent.
- **Corrective action:** new deterministic tool
  `teams_get_all_hosted_content` (`backend/tools/teams/get_hosted_
  content.py`), added to `incident_manager.tools`: takes NO id-list
  parameter, reads the authoritative discovery order itself via
  `get_message_hosted_content_order`, provenance-checks every id, is
  idempotent per run, and is best-effort across siblings. The model's
  ONLY decision is which of the two retrieval tools to call — it never
  manually enumerates individual `hosted_content_id`s for a multi-image
  request.
- **Regression protection:**
  `backend/tests/test_teams_get_all_hosted_content.py`.
- **Live validation:** YES — re-validated live: a real 3-image Teams
  message was correctly retrieved (3 of 3, not 2 of 3) and rendered as
  "Visual evidence · 3 images analyzed," confirmed via backend log, API
  response capture, and browser screenshot.
- **Fix commit SHA(s):** `0407808805d8388d81602d2f66cdfa5b0164805f`
- **Related tests:** `test_teams_get_all_hosted_content.py`
- **Related docs:** `docs/TEAMS_TOOL_CONTRACT.md` §4b,
  `docs/AGENT_CONTRACT.md` §5
- **Notes:** `CLAUDE.md`'s own 5.X-full-scope narrative (as corrected by
  this audit pass) now cites this entry, DEF-0016, rather than its
  original, incorrect citation of DEF-0009 (a different, unrelated
  defect — the rich-content fast-path routing bug) for this specific
  nondeterminism issue. That citation was corrected in the same pass
  that added this entry — see this milestone's own closure report.

===================================================================
DEF-0017 — root-level XLSX/PDF ingestion produces a dangling
`parent_artifact_id`, crashing `IngestedKnowledgeDocument` construction
===================================================================

- **Status:** FIXED
- **Severity:** HIGH (any attempt to ingest a standalone/root-level XLSX
  or PDF file through the real pipeline crashed before a governed
  `KnowledgeObject` could ever be built — total, unconditional failure
  for two of A5's three supported root document formats)
- **Detected during:** P11-M03 (6A.3 — Multimodal Knowledge Ingestion &
  Provenance), while writing this milestone's own new tests for the
  `ingest_and_structure_local_file` composition
- **Parent milestone:** P11-M03
- **Legacy name:** none (found and fixed within 6A.3 itself)
- **Affected capability:** local-file ingestion
  (`backend/knowledge_ingestion/local_file_adapter.py`) of any root-level
  XLSX or PDF document — i.e. any file whose OWN top-level format is XLSX
  or PDF, as opposed to being embedded inside a DOCX (the embedded case
  was unaffected)
- **Symptom:** `pydantic_core.ValidationError` — "artifact
  '<id>' has parent_artifact_id 'root', which is not present in the same
  artifact list" — raised inside `IngestedKnowledgeDocument`'s own
  construction, before extraction results could ever be used.
- **Expected:** a root-level XLSX/PDF document's own direct-child
  artifacts (sheets/pages) should have `parent_artifact_id=None` —
  "embedded directly in the root document", exactly matching
  `extract_docx`'s own already-correct convention for a root-level DOCX,
  and exactly matching `KnowledgeArtifact`'s own documented lineage
  contract (`backend/knowledge/domain/artifacts.py`: "`parent_artifact_id
  =None` means embedded directly in the root document").
- **Root cause:** `backend/knowledge/ingestion/extractors/dispatch.py`'s
  `extract_root_document` called `extract_xlsx(..., container_artifact_id
  ="root", ...)` and `extract_pdf(..., container_artifact_id="root",
  ...)` — passing the literal STRING `"root"` as a sentinel, rather than
  `None` (the DOCX branch, two lines above, already correctly passes
  `container_artifact_id=None`). `extract_xlsx`/`extract_pdf` then set
  every direct-child artifact's `parent_artifact_id` to that literal
  string. Since no artifact with `artifact_id="root"` is ever actually
  added to the resulting document's own `artifacts` list (the root
  document itself is never represented as an artifact, per the domain
  model's own design), every such artifact's `parent_artifact_id` was
  dangling — a real, immediate violation of
  `validate_artifact_lineage`'s "every parent_artifact_id must resolve
  within the same list" invariant, first triggered the moment
  `local_file_adapter.py` (or anything else) constructed an
  `IngestedKnowledgeDocument`/`KnowledgeObject` from the extraction
  result.
- **Why it was never caught before 6A.3:** the real-corpus validation
  test (`test_knowledge_real_corpus_validation.py`, A5) calls
  `ingest_local_files` — which stops at building the
  `IngestedKnowledgeDocument` — against three real files, all DOCX; no
  existing automated test, synthetic or real, ever constructed an
  `IngestedKnowledgeDocument`/`KnowledgeObject` from a ROOT-level XLSX or
  PDF (every pre-6A.3 XLSX/PDF test called `extract_xlsx`/`extract_pdf`
  directly and only inspected the returned artifact list, never feeding
  it through the `IngestedKnowledgeDocument` constructor that actually
  enforces lineage). The defect was real from the moment root-level
  XLSX/PDF support was implemented in A5; it was invisible to every
  existing test until 6A.3 wrote the first test that actually exercised
  the full path.
- **Corrective action:** `extract_xlsx`/`extract_pdf`'s
  `container_artifact_id` parameter type widened from `str` to
  `Optional[str]` (matching `extract_docx`'s own existing signature);
  `dispatch.py`'s two root-level call sites changed to pass
  `container_artifact_id=None`. `deterministic_artifact_id`'s own basis
  string already treats a missing parent as the literal word `'root'`
  internally (`parent_artifact_id or 'root'`), so this fix changes NO
  artifact_id/content_hash/durable-storage object key that may already
  exist from a prior real ingestion run — only the STORED
  `parent_artifact_id` value itself changes, from the dangling string
  `"root"` to the correct `None`.
- **Regression protection:** `backend/tests/test_knowledge_local_file_adapter.py`
  (`test_structure_pdf_root_document_page_content_becomes_retrievable_sections`,
  `test_structure_xlsx_root_document_sheet_content_becomes_retrievable_sections`,
  `test_structure_section_artifact_ids_resolve_within_same_document`) and
  `backend/tests/test_knowledge_real_corpus_full_pipeline.py`
  (`test_real_standalone_xlsx_root_document_ingests_and_structures_successfully`,
  against the real `Rogers_Core_Outage_Impact_Agent_Surgical_Checklist.xlsx`
  file).
- **Live validation:** validated against a real, standalone XLSX file on
  this machine (`Rogers_Core_Outage_Impact_Agent_Surgical_Checklist.xlsx`,
  8 real sheets, 7 real native Excel Tables) — confirmed the exact same
  crash reproduces against real content before the fix (the pre-fix code
  path was exercised directly against this file during root-causing) and
  confirmed clean, successful, structurally-valid ingestion after it.
- **Fix commit SHA(s):** none yet — uncommitted at the time this entry
  was written; see this milestone's own closure report.
- **Related tests:** see "Regression protection" above.
- **Related docs:** `docs/KNOWLEDGE_CONTRACT.md` (A5 section, provenance/
  lineage contract).
- **Notes:** found purely through this milestone's own testing
  discipline (writing a genuinely new integration test before assuming
  existing behavior was correct), not through a reported symptom — a
  concrete instance of the Evidence Pack principle this milestone's own
  instruction introduced ("no important claim may be asserted without
  proof").

===================================================================
DEF-0018 — real PostgreSQL integration test fixture destructively
dropped the shared, permanently-migrated 6A.5 evidence-index table
===================================================================

- **Status:** FIXED
- **Severity:** HIGH (any run of a specific test file against the real,
  shared DEV Cloud SQL database silently destroyed a real, permanently
  Alembic-migrated production schema object, requiring a manual
  Alembic-bookkeeping repair to recover)
- **Detected during:** the 6A.5 "Schema Drift Repair + Final Live DEV
  Proof" corrective pass, triggered by the user's own independent live
  DEV query proving `slopanoc_knowledge_evidence_index` was `MISSING`
  while Alembic still recorded revision `a1f3c9e07b21` as applied
- **Parent milestone:** P11-M05 (6A.5)
- **Legacy name:** none (found and fixed within this corrective pass)
- **Affected capability:** `backend/tests/knowledge/test_hybrid_
  retrieval_repository_postgres.py`'s real-Postgres integration test
  suite for `EvidenceIndexRepository`
- **Symptom:** `slopanoc_knowledge_evidence_index` — a real table created
  by the real `a1f3c9e07b21` Alembic migration against the shared DEV
  Cloud SQL database — disappeared from that database after this test
  file (or a concurrently-running full-suite run that included it) ran,
  even though the Alembic `alembic_version` row still recorded the
  migration as applied, producing a genuine Alembic/physical-schema
  drift: Alembic bookkeeping said the table should exist; it did not.
- **Expected:** a real-PostgreSQL integration test file must clean up
  ONLY the rows it itself inserted — it must never issue table-level DDL
  (`DROP TABLE`, `TRUNCATE`) against a table that is, or may become, a
  real, permanently-migrated, shared schema object.
- **Root cause:** the file's own `repository` pytest fixture teardown
  unconditionally executed `DROP TABLE IF EXISTS slopanoc_knowledge_
  evidence_index` after EVERY test. This was harmless at the time it was
  written (the table did not yet exist as a real Alembic-migrated
  object), but once the real `a1f3c9e07b21` migration was successfully
  applied against the shared DEV database earlier in the same 6A.5
  milestone, this same fixture teardown began destructively dropping
  that real, shared, migrated object on every subsequent run of this
  test file against that database — confirmed as the actual mechanism by
  direct inspection of the fixture's own source line, not inferred from
  symptoms alone.
- **Why it was never caught before this corrective pass:** every earlier
  run of this test file (within the same 6A.5 milestone) executed either
  before the real migration existed, or in a state where the table's
  disappearance was masked by the file's own docstring assumption ("this
  file creates/destroys the table itself") — that assumption was true
  when written and became false, silently, the moment a separate part of
  the same milestone started relying on the table's persistence across
  test runs (the migration itself, and any future 6A.6+ consumer).
- **Corrective action:** the fixture no longer issues any table-level
  DDL in teardown. Every evidence_id this file inserts is now
  constructed through a new `_eid()` helper that applies a module-level
  `_TEST_EVIDENCE_ID_PREFIX` namespace; the fixture teardown now executes
  `DELETE FROM slopanoc_knowledge_evidence_index WHERE evidence_id LIKE
  '<prefix>%'` — deleting only rows this file's own tests inserted,
  never the table itself, and never any row inserted by anything else.
  `ensure_schema()` is still called (idempotent `CREATE ... IF NOT
  EXISTS` semantics) so the file remains correct whether or not the real
  migration has separately been applied.
- **Regression protection:** all 13 tests in `test_hybrid_retrieval_
  repository_postgres.py` re-run against the real, migrated table and
  pass; the table's continued existence and zero leftover row count were
  directly verified, via live query, both immediately after this file's
  own run and again after the full backend regression suite completed.
- **Live validation:** reproduced the exact real-world sequence this
  defect actually followed — real Alembic drift confirmed live
  (`alembic_version = a1f3c9e07b21`, `to_regclass(...) = NULL`), repaired
  via `alembic stamp 9b6df6490c0e` then `alembic upgrade head` (real
  physical schema recreated, columns/indexes/constraints reintrospected
  and confirmed to exactly match the migration), the corrected test file
  run against that real table, and the table's survival reconfirmed live
  afterward.
- **Fix commit SHA(s):** none yet — uncommitted at the time this entry
  was written; see this corrective pass's own closure report.
- **Related tests:** see "Regression protection" above.
- **Related docs:** `docs/KNOWLEDGE_CONTRACT.md` §27 (Phase 6A.5).
- **Notes:** the pgvector privilege grant/revocation volatility this
  milestone had already documented (§27.10) was the proximate trigger
  that exposed this defect (a concurrently-running background test run
  happened to execute this fixture's destructive teardown against the
  newly-real table before the drift was noticed) — but the defect itself
  is independent of that volatility: this fixture would have destroyed
  the table on its very next run even without any role-membership
  change, purely because the table had become a real, permanent,
  migrated object it was never updated to respect.

===================================================================
OPS-0001 — 6A.8 migration-test target isolation: Alembic Config
`sqlalchemy.url` overrides are not honored by this repository's `env.py`
===================================================================

- **Status:** RESOLVED (process/methodology finding — no application
  code was found defective; `env.py`'s behavior is confirmed intentional
  and was left unmodified)
- **Classification:** `OPS-xxxx` (operational/testing-methodology), NOT
  `DEF-xxxx` — nothing in the actual SLOPANOC runtime/migration code
  behaved incorrectly. The mistake was in a test methodology assumption
  made during 6A.8 implementation, not in any shipped code path.
- **Severity:** LOW-MODERATE (no data loss occurred; a local-only
  development artifact was briefly, harmlessly, and then fully
  reversibly mutated; zero shared/production/Cloud SQL state was ever
  touched)
- **Detected during:** 6A.8 (Experience Memory Foundation) migration
  validation, while attempting an isolated SQLite scratch-file test of
  the new `c7e2a4f9b83d` migration's upgrade/downgrade behavior
- **Parent milestone:** P11-M08 (6A.8)
- **Affected capability:** none in production — the finding is about how
  a *test* attempted to redirect Alembic, not about `alembic/env.py`'s
  real, in-force behavior
- **Symptom:** an attempt to point Alembic at an isolated scratch SQLite
  file via `alembic.config.Config.set_main_option("sqlalchemy.url", ...)`
  had no effect; Alembic instead ran the full historical migration chain
  against the real local development file `./slopanoc_sessions.db`
  (resolved via `Settings.resolve_database_url()`'s own zero-setup local
  default, since no `SLOPANOC_DATABASE_URL` was set for the test), and
  failed partway through at a pre-existing, unrelated, PostgreSQL-only
  migration step (`b85242503972`'s `ALTER COLUMN ... TYPE`, invalid
  SQLite syntax) — leaving that local file with six freshly-created but
  entirely EMPTY Alembic-owned tables and an `alembic_version` row stuck
  at `d2081e4fd455`.
- **Root cause:** `alembic/env.py`'s `_resolved_database_url()` calls
  `get_settings().resolve_database_url()` UNCONDITIONALLY — "the SAME
  resolver the FastAPI app itself uses" (the function's own docstring)
  — and never reads `context.config`'s `sqlalchemy.url` at all. This is
  DELIBERATE, existing, in-force design (confirmed by direct source
  read, not assumed): it guarantees a migration always targets the real
  configured application database, never a URL that could be silently
  overridden by test/tooling code, accidentally or otherwise. **This is
  not a bug** — it is the identical design principle already documented
  elsewhere in this codebase for `runtime_database_policy.py` ("THERE IS
  NO ENVIRONMENT VARIABLE THAT WEAKENS THIS POLICY"), applied to Alembic
  itself. The actual mistake was assuming, without first verifying
  against `env.py`'s own source, that a `Config`-level override was a
  safe way to redirect a migration-chain test.
- **Impact, verified before any corrective action:** ADK's own session
  tables in that local file (`sessions`=146, `events`=1664,
  `app_states`=2, `user_states`=40 rows) were completely unaffected —
  Alembic never manages those tables. The six newly-created Alembic-
  owned tables (`slopanoc_cases`, `slopanoc_knowledge_objects`,
  `slopanoc_chat_attachments`, `slopanoc_case_context_items`,
  `slopanoc_case_memberships`, `slopanoc_case_session_links`) were
  verified, by direct query, to contain exactly 0 rows each before any
  cleanup was attempted — they had never existed in this local file
  before this test (this file had apparently never had `alembic upgrade`
  run against it locally before). No Cloud SQL/shared/production state
  was ever involved.
- **Corrective action:** user-authorized bounded recovery, performed
  only after re-verifying every safety precondition immediately before
  any destructive statement: (1) a byte-for-byte backup
  (`slopanoc_sessions.pre_6a8_cleanup.db`) was created and its SHA-256
  checksum proven identical to the source before touching anything; (2)
  the complete `sqlite_master` inventory (28 objects) was captured; (3)
  the six empty accidental tables and the `alembic_version` table (never
  present in this file before the incident) were dropped, in dependency
  order; (4) the post-cleanup inventory (11 objects) was proven to
  differ from the pre-cleanup inventory by EXACTLY the 7 dropped tables
  plus their 10 owned indexes (17 objects total, 28-17=11) — no
  unrelated table, index, trigger, or view was affected; (5) ADK's own
  four table row counts were reconfirmed byte-for-byte identical
  (146/1664/2/40) after cleanup.
- **Prevention rule (recorded, not a code change):** Alembic migration-
  chain upgrade/downgrade tests in this repository MUST NOT rely on
  `Config.set_main_option("sqlalchemy.url", ...)` to redirect the
  target database — `env.py` will not honor it. A migration-chain test
  must either (a) set `SLOPANOC_DATABASE_URL` in the test process's own
  environment before invoking Alembic (the one input `resolve_database_
  url()` actually honors), pointed at a genuinely disposable target, or
  (b) validate against the real, already-provisioned Cloud SQL DEV
  instance directly, applying `upgrade` only (never a destructive
  `downgrade` against shared state), per the corrected 6A.8 migration-
  validation strategy this finding produced. `env.py` itself was NOT
  modified — its Config-URL-ignoring behavior is correct and remains
  exactly as designed.
- **Fix commit SHA(s):** none — this is a process finding with no code
  fix; the local SQLite file was restored to its exact prior state (see
  this milestone's own closure report, "Migration-Test Incident"
  section, for the full before/after evidence).
- **Related docs:** this milestone's own 6A.8 closure report (Experience
  Memory Foundation); `docs/GCP_INTELLIGENCE_RUNTIME.md` (Cloud SQL
  validation conventions); `backend/api/runtime_database_policy.py`
  (the analogous, already-established "no override mechanism" principle
  for the FastAPI runtime itself).
- **Notes:** no `docs/DEFECT_REGISTER.md` entry of this kind existed
  before this finding because no prior milestone had attempted to
  redirect an Alembic migration-chain test to an isolated target at
  all — every prior 6A.x migration (6A.2, 6A.5) was validated either
  against a from-empty SQLite scratch database seeded independently of
  Alembic's own resolver, or directly against live Cloud SQL. This
  finding is recorded so a future milestone does not repeat the same
  assumption.

===================================================================
DEF-0019 — rendered evidence section never surfaced the citable
`evidence_id`, causing a real model call to fabricate one
===================================================================

- **Status:** FIXED
- **Severity:** MEDIUM (the fail-closed grounding mechanism correctly
  caught the resulting fabricated reference and returned a safe
  `BLOCKED` result every time — no ungrounded content ever reached a
  user — but the underlying capability, "advisory grounded assessment
  citing real selected evidence," could never succeed for ANY request
  until fixed)
- **Detected during:** P11-M09 (6A.9 — Troubleshooting Manager &
  Intelligence Assembly), during this milestone's own mandatory
  controlled real-model validation (`backend/tests/troubleshooting_
  manager/test_real_model_validation.py`) — the first real Vertex AI
  call this milestone made
- **Parent milestone:** P11-M09
- **Legacy name:** none (found and fixed within 6A.9 itself)
- **Affected capability:** `backend/troubleshooting_intelligence/
  rendering.py`'s `render_troubleshooting_intelligence_as_text`, and
  therefore every real `troubleshooting_manager` model call that
  reasons over selected governed-Knowledge evidence
- **Symptom:** a real Vertex AI `troubleshooting_manager` response's
  `evidence_references_used[0].evidence_id` was `"KO-1 vv1"` — not a
  real `evidence_id` from the Intelligence Package — which correctly
  failed `backend.troubleshooting_intelligence.grounding.validate_
  evidence_references` and produced a safe, deterministic `BLOCKED`
  result (`response referenced unknown evidence id(s): ['KO-1 vv1']`).
- **Expected:** a real, grounded model response should be able to cite
  the exact, real `evidence_id` string of a selected evidence item it
  relied on, and that reference should pass grounding validation.
- **Root cause:** `render_troubleshooting_intelligence_as_text` reused
  6A.6's own frozen, unmodified `render_context_package_as_text`
  (`backend/context_engineering/rendering.py`) for the evidence section
  — that renderer's own evidence block labels each item by
  `knowledge_id`/`version_label`/`section_id` for HUMAN readability
  (e.g. `[1] KO-1 v1 / sec-1 (source)`) and never displays the item's
  own opaque `evidence_id` field anywhere. The model, needing SOME
  string to put in `EvidenceReferenceUsed.evidence_id`, synthesized one
  from the human-readable label it could actually see (`"KO-1 vv1"`,
  a garbled concatenation of `knowledge_id` and `version_label`) —
  a reasonable model behavior given what it was actually shown, not a
  model defect.
- **Why it was never caught before this milestone's own real-model
  pass:** every prior unit/integration test (Intelligence Assembly,
  grounding, runtime orchestration) either constructed `Evidence
  ReferenceUsed` objects directly in test code (never depending on a
  model reading the rendered text) or monkeypatched the model
  invocation entirely — none of them actually rendered the intelligence
  package to text AND had a real model read it AND tried to cite a
  real evidence id from what it read. This is exactly the class of
  defect a real-model validation pass exists to catch that pure unit
  tests structurally cannot.
- **Corrective action:** added `_render_evidence_reference_ids` to
  `backend/troubleshooting_intelligence/rendering.py` — a small,
  additive section listing, for every selected evidence item, its real
  `evidence_id` string explicitly (`evidence_id='ev-vswr-procedure'
  (KO-1 v1 / sec-1)`), inserted between the (unmodified) rendered
  context/evidence block and the SKILL METHODOLOGY section. 6A.6's own
  `render_context_package_as_text` was NOT modified, duplicated, or
  forked — this fix supplements it with the one missing, structurally
  necessary identifier, entirely from within the 6A.9 package.
- **Regression protection:** `backend/tests/troubleshooting_
  intelligence/test_rendering.py` (existing tests re-verified unaffected
  — trust-section ordering/DATA-delimiter proofs still hold); the fix
  was validated by re-running the exact real-model test that first
  exposed it.
- **Live validation:** re-ran the real Vertex AI validation suite
  immediately after the fix — all 4 real-model tests passed, including
  the specific test that had failed with the fabricated evidence id
  (`test_real_model_advisory_ready_for_well_formed_request`) and the
  Knowledge-vs-Experience trust-precedence test (`test_real_model_
  never_authorizes_action_governed_evidence_prohibits`), which had
  independently failed on the SAME grounding symptom before this fix.
- **Fix commit SHA(s):** none yet — uncommitted at the time this entry
  was written; see this milestone's own closure report.
- **Related tests:** `backend/tests/troubleshooting_manager/
  test_real_model_validation.py`, `backend/tests/troubleshooting_
  intelligence/test_rendering.py`.
- **Related docs:** `docs/INTELLIGENCE_ARCHITECTURE.md` §19 (Phase 6A.9).
- **Notes:** this defect could only ever manifest on the `SELECTED`
  path (a real model invocation) — the deterministic `NONE_REGISTERED`/
  `NONE_APPLICABLE`/`NONE_READY` short-circuit paths never render
  evidence text to a model at all, and were therefore never affected.

===================================================================
DEF-0020 — unisolated troubleshooting-manager unit tests silently
created a real, empty `slopanoc_experience_records` table in the local
dev `slopanoc_sessions.db` file
===================================================================

- **Status:** FIXED
- **Severity:** LOW-MEDIUM (never wrote or exposed any real data — the
  table was always exactly 0 rows, and no production code path reads
  from it either — but it represents a genuine, unauthorized schema
  mutation of a real local file made silently by an automated test run,
  which is exactly the class of incident OPS-0001 already flagged as a
  standing risk for this codebase's test suite)
- **Detected during:** the 6A.10 corrective pass ("Live Intelligence
  Wiring + Local SQLite Isolation"), via the user's own direct
  inspection of `slopanoc_sessions.db` after a prior 6A.10 regression
  run
- **Parent milestone:** P11-M10 (6A.10 — Dual-Specialist Orchestration);
  found and fixed in its own dedicated corrective pass, not reopening
  6A.10's own orchestration design
- **Legacy name:** none
- **Affected capability:** `backend/tests/troubleshooting_manager/
  test_troubleshooting_tool_unit.py` (the test file itself was the
  actor — no production code is defective)
- **Symptom:** `slopanoc_sessions.db` (the real local development
  ADK-session database) contained an unexpected `slopanoc_experience_
  records` table (plus its 5 named indexes) with exactly 0 rows, absent
  any `alembic_version` table — i.e. schema created outside Alembic's
  own migration chain, on a file no test should ever mutate.
- **Expected:** the automated test suite must never create, mutate, or
  otherwise touch the real local development database file; every test
  that exercises real persistence-backed code must use an isolated
  (in-memory or disposable) database of its own.
- **Root cause, confirmed by direct code trace (not guessed):**
  `test_troubleshooting_tool_unit.py`'s tests call the REAL
  `troubleshooting_manager()` FunctionTool wrapper (`backend/agents/
  team_manager/troubleshooting_tool.py`) directly, which calls the REAL
  `run_troubleshooting_assessment(request)` (`backend/agents/
  troubleshooting_manager/runtime.py`) with NO `experience_service`
  override. `runtime.py` forwards `experience_service=None` into
  `query_experience_support(..., service=None)` (`backend/agents/
  troubleshooting_manager/experience_support.py:44`), whose fallback,
  `service if service is not None else get_experience_memory_service()`,
  resolves the REAL, process-wide `get_experience_memory_service()`
  (`backend/experience_memory/sqlalchemy/service.py:271-278`) →
  `get_experience_memory_database()` → `ExperienceMemoryDatabase()`
  (`backend/experience_memory/sqlalchemy/db.py:70-73`), whose
  constructor, given no explicit `database_url`, resolves
  `Settings.resolve_database_url()` — the real local dev SQLite file
  when no `SLOPANOC_DATABASE_URL` override is set in the test process.
  `ExperienceMemoryService.query()` (`backend/experience_memory/
  sqlalchemy/service.py:191-201`) then unconditionally calls `await
  self._db.ensure_schema()` BEFORE executing its `SELECT` — creating the
  table and its indexes on that real file even for a query that
  ultimately returns zero rows (the `owner_id` filter never matches
  anything real). This is the SAME bug class as OPS-0001 (an
  Alembic-migration test that resolved against the real local DB by
  surprise) but with a different trigger: a plain service singleton's
  own no-argument default, not an Alembic `env.py` override.
- **Why it was never caught by the original 6A.9/6A.10 test-writing
  passes:** `test_dual_specialist_real_model.py` (6A.10) and
  `test_runtime.py`/`test_real_model_validation.py` (6A.9) already
  correctly monkeypatch `experience_support.get_experience_memory_
  service` to an isolated in-memory `ExperienceMemoryService()` for
  every test — this ONE file (`test_troubleshooting_tool_unit.py`) was
  simply written without that same isolation, since its own original
  focus (owner/case/session propagation, exception safety, the bounded-
  invocation guard) did not initially seem Experience-Memory-relevant.
- **Corrective action:** added an autouse `_isolated_experience_memory`
  fixture to `test_troubleshooting_tool_unit.py` (and to the new
  `test_troubleshooting_context_wiring_real_stack.py`, which has the
  SAME exposure via its own full-wrapper real-model test), monkeypatching
  `experience_support.get_experience_memory_service` to an isolated,
  in-memory-only `ExperienceMemoryService()` for the duration of every
  test in each file — the exact same pattern already proven correct in
  `test_dual_specialist_real_model.py`. Also added an autouse
  `_isolated_knowledge_repository` fixture (env-var override +
  `lru_cache.cache_clear()`, mirroring `test_p5_1j_knowledge_tools.py`'s
  own established `isolated_repo` fixture) to the same file, since the
  SAME corrective pass's new live-Evidence wiring (`context_support.py`)
  introduced an analogous, previously-nonexistent exposure to the real
  local Knowledge database for this file's tests.
- **Regression protection:** re-ran the fixed test file with a SHA-256
  hash of `slopanoc_sessions.db` captured immediately before and after
  — byte-identical, proving no write of any kind occurred; re-ran the
  full 6A.9/6A.10 focused suite (including real-model and the new
  real-Cloud-SQL-gated tests) with the same before/after hash check;
  re-ran the full backend regression suite (3863 passed, 1 skipped, 2
  pre-existing unrelated flaky failures) with the same hash check
  afterward — unchanged throughout.
- **Cleanup performed:** the pre-existing, already-created (0-row)
  `slopanoc_experience_records` table and its 5 indexes were removed
  from `slopanoc_sessions.db` via a single scoped `DROP TABLE` (verified
  0 rows immediately before the drop; never a `DELETE`-only cleanup
  since the goal here was removing an entire unauthorized table, not
  rows within an intentional one) — proven via a full `sqlite_master`
  before/after comparison (26 objects → 19 objects, an exact
  reduction of 1 table + 5 named indexes + 1 autoindex) that every ADK
  table (`sessions`=146, `events`=1664, `app_states`=2, `user_states`=40,
  `adk_internal_metadata`=1 rows) and every pre-existing Case table
  (`slopanoc_cases`/`slopanoc_case_memberships`/`slopanoc_case_session_
  links`/`slopanoc_case_context_items`, all already 0 rows, all
  legitimate existing local-dev schema unrelated to this defect) were
  left completely untouched.
- **Fix commit SHA(s):** none yet — uncommitted at the time this entry
  was written; see the 6A.10 corrective-pass closure report.
- **Related tests:** `backend/tests/troubleshooting_manager/
  test_troubleshooting_tool_unit.py`, `backend/tests/troubleshooting_
  manager/test_troubleshooting_context_wiring_real_stack.py`,
  `backend/tests/troubleshooting_manager/test_dual_specialist_real_
  model.py` (the already-correct reference pattern).
- **Related docs:** this file's own OPS-0001 entry (the same bug class,
  a different trigger); `docs/INTELLIGENCE_ARCHITECTURE.md` §20 (Phase
  6A.10).
- **Notes:** this is a TEST-ISOLATION defect only — no production code
  path was changed by this fix (`experience_support.py`/`runtime.py`/
  `service.py`/`db.py` are all byte-for-byte unmodified); a correctly
  configured live deployment SHOULD resolve `get_experience_memory_
  service()` to the real, explicitly-configured Cloud SQL database —
  that is the intended, correct production behavior this fix does not
  change.

===================================================================
DEF-0021 — a second, previously-mischaracterized instance of DEF-0020's
own bug class: an unisolated stale-case-hint test silently created 4
real `slopanoc_cases`/`_case_memberships`/`_case_session_links`/
`_case_context_items` tables in the local dev `slopanoc_sessions.db`
file, which the prior 6A.10 corrective-pass closure incorrectly
described as legitimate, pre-existing local-dev schema
===================================================================

- **Status:** FIXED
- **Severity:** LOW-MEDIUM (same class as DEF-0020 — never wrote or
  exposed real data, all 4 tables always exactly 0 rows — but the prior
  corrective pass's OWN closure report made an unverified, INCORRECT
  claim about these tables being legitimate application schema, which
  this entry corrects)
- **Detected during:** the SECOND 6A.10 corrective pass ("Final Trust &
  SQLite Baseline Closure"), triggered by the user's own explicit
  rejection of the first corrective pass's characterization: "the four
  Case tables described only as 'pre-existing' relative to the
  corrective pass... that is insufficient."
- **Parent milestone:** P11-M10 (6A.10); found and fixed in a corrective
  pass, not reopening 6A.10's own orchestration design
- **Legacy name:** none
- **Affected capability:** `backend/tests/troubleshooting_manager/
  test_troubleshooting_tool_unit.py`'s `test_stale_case_hint_never_
  aborts_never_fabricates_case` (the test file itself was the actor — no
  production code is defective)
- **Symptom:** `slopanoc_sessions.db` contained 4 unexpected tables
  (`slopanoc_cases`, `slopanoc_case_memberships`, `slopanoc_case_
  session_links`, `slopanoc_case_context_items`), each with exactly 0
  rows, plus their own indexes — absent any `alembic_version` table
  (this file has never been Alembic-managed).
- **Root cause, confirmed by an isolated, disposable-temp-file
  reproduction (never guessed, never asserted against the real file):**
  `test_stale_case_hint_never_aborts_never_fabricates_case` called
  `troubleshooting_tool.py`'s `_build_context_package` with a truthy,
  nonexistent `ACTIVE_CASE_ID_STATE_KEY` in `tool_context.state`, WITHOUT
  mocking `get_case_service` (unlike the test immediately above it in
  the same file, which correctly does). `_resolve_case_context` therefore
  called the REAL, process-wide `get_case_service()` singleton
  (`backend/cases/service.py:435-437`, `@lru_cache(maxsize=1)`) →
  `CaseService.get_case()` (`backend/cases/service.py:201-205`), whose
  FIRST line unconditionally calls `await self._db.ensure_schema()`
  (`Base.metadata.create_all`) BEFORE its own not-found check — creating
  all 4 Case tables on whatever database `CaseDatabase()`'s own
  no-argument default resolves to (`backend/cases/db.py:66-67,96-101`:
  `Settings.resolve_database_url()`, the real local dev SQLite file when
  no `SLOPANOC_DATABASE_URL` override is set) even though the case was
  never found and a `SafeErrorException` was correctly raised and caught
  immediately afterward. Exactly the same bug CLASS as DEF-0020 (`get_
  experience_memory_service()`'s own `.query()` calling `ensure_schema()`
  unconditionally before its own zero-result read) — a `.get_case()`-
  style read-before-existence-check pattern, mirrored across two
  independent domains (Case, Experience Memory) in this codebase.
  Reproduced in isolation, safely, against a disposable temp SQLite file
  (never the real one): calling `get_case_service().get_case("alice",
  "case-does-not-exist")` against a freshly-created, empty temp database
  raised the expected `SafeErrorException` AND created exactly the same
  4 tables (`slopanoc_case_context_items`, `slopanoc_case_memberships`,
  `slopanoc_case_session_links`, `slopanoc_cases`) in that disposable
  file — conclusive, direct proof of the mechanism.
- **A GENUINE PROCESS ERROR IN THE PRIOR 6A.10 CORRECTIVE PASS:** that
  pass's own closure report described these same 4 tables as "pre-
  existing" and implicitly legitimate local-dev schema, without
  verifying that claim against the actual architecture. It was wrong:
  since the POST-A5 "Cloud SQL-only runtime hardening" refinement
  (`backend/api/runtime_database_policy.py`, wired into `_lifespan`), a
  REAL local backend startup FAILS FAST unless both persistence domains
  resolve to PostgreSQL — meaning a genuine, normal local-dev run of the
  real application can NEVER legitimately create these SQLite Case
  tables anymore. There is no code path, other than an unisolated test,
  by which `slopanoc_cases` et al. could appear in a local SQLite file
  today. This entry corrects that prior mischaracterization.
- **Corrective action:** `test_stale_case_hint_never_aborts_never_
  fabricates_case` now routes through an isolated, in-memory `CaseService
  ()` (mirrors the immediately-preceding test's own already-correct
  pattern) via the same `module.get_case_service = lambda: case_service`
  monkeypatch style already used elsewhere in this file.
- **Regression protection:** re-ran the fixed test alone and the full
  file with a SHA-256 hash of `slopanoc_sessions.db` captured immediately
  before and after — byte-identical; re-ran the full backend regression
  suite with the same hash check afterward.
- **Cleanup performed:** the 4 pre-existing (0-row) Case tables and their
  indexes were removed from `slopanoc_sessions.db` via 4 scoped `DROP
  TABLE` statements (row counts re-verified 0 immediately before each
  drop) — proven via a full `sqlite_master` before/after comparison that
  only those 4 tables and their own indexes disappeared; every ADK table
  (`sessions`=146, `events`=1664, `app_states`=2, `user_states`=40,
  `adk_internal_metadata`=1 rows) was left completely untouched.
- **Fix commit SHA(s):** none yet — uncommitted at the time this entry
  was written; see the second 6A.10 corrective-pass closure report.
- **Related tests:** `backend/tests/troubleshooting_manager/
  test_troubleshooting_tool_unit.py`.
- **Related docs:** this file's own DEF-0020 entry (the same bug class,
  a different domain); `docs/INTELLIGENCE_ARCHITECTURE.md` §20 (Phase
  6A.10).
- **Notes:** this is a TEST-ISOLATION defect only — `CaseService.get_
  case()`/`CaseDatabase.ensure_schema()` are byte-for-byte unmodified,
  and their "always ensure schema before any operation" behavior remains
  correct and intentional for a real, properly-configured deployment.

===================================================================
DEF-0022 — [VALIDATION / TEST-FIXTURE DEFECT, NOT A PRODUCTION DEFECT] a
shared 6A.9 test fixture named "conflicting" never actually produced a
CONFLICTING TELCO Context state, because it conflicted on a MULTI-
cardinality dimension
===================================================================

- **Classification (6A.11 Pass 2 correction, per explicit instruction):
  VALIDATION / TEST-FIXTURE DEFECT — NOT a production Context-engine
  defect.** `backend/context/domain/models.py`'s `reduce_dimension`/
  `DIMENSION_CARDINALITY` table (the actual production Context-engine
  code) is CORRECT and was NOT modified by this fix, and required no
  correction: it behaved exactly as documented for a MULTI-cardinality
  dimension (`FAULT`) both before and after this entry was written. The
  defect is entirely confined to one shared PYTEST FIXTURE (`backend/
  tests/troubleshooting_manager/_fixtures.py`'s `context_package_fault_
  conflicting`), which asserted a shape that does not exercise a
  conflict at all given FAULT's own documented MULTI cardinality. No
  production file was touched to resolve this entry. This paragraph
  supersedes any earlier reading of this entry as a production
  architecture concern.
- **Status:** FIXED
- **Severity:** LOW (test-fixture-only defect; the frozen 6A.2 domain
  logic it exercised, `reduce_dimension`, is correct and unmodified — it
  is the ONLY reason this fixture's mislabeling is even detectable: the
  function behaved exactly as documented, the fixture just didn't
  exercise the case its name promised)
- **Detected during:** P11-M11 Pass 1 (6A.11 — Integrated TELCO
  Validation), while writing the mandatory integrated CONFLICTING-Context
  regression test (instruction section 26) and finding `evaluate_skill_
  readiness` reported `READY`, not the expected `NOT_READY`
- **Parent milestone:** P11-M09 (6A.9), where the fixture was originally
  authored; found and fixed during P11-M11
- **Legacy name:** none
- **Affected capability:** `backend/tests/troubleshooting_manager/
  _fixtures.py`'s `context_package_fault_conflicting` (a shared test
  fixture only — no production code is defective)
- **Symptom:** a test asserting `readiness.status == SkillReadinessStatus
  .NOT_READY` against a package built by this fixture failed with
  `READY` instead.
- **Root cause, confirmed by direct reading of `backend/context/domain/
  models.py`'s own `reduce_dimension`/`DIMENSION_CARDINALITY` table:**
  the fixture asserted two different `FAULT` values ("VSWR Over
  Threshold", "Cell Down") from two different origins, on the assumption
  that would produce `CONFLICTING`. `FAULT` is a MULTI-cardinality
  dimension (`ContextDimension.FAULT: DimensionCardinality.MULTI`,
  instruction-explicit per 6A.2's own closure — multiple genuinely
  concurrent faults/alarms are never a conflict merely for being
  different). `reduce_dimension`'s own documented rule: "only VALUE
  assertion(s), MULTI dimension -> KNOWN, every distinct canonical_value
  accepted" — so this fixture always resolved to `KNOWN` with both
  values in `accepted`, never `CONFLICTING`. `CONFLICTING` for a VALUE-
  only case is reachable ONLY for a SINGULAR-cardinality dimension with
  2+ distinct canonical values (e.g. `VENDOR`).
- **Why this was never caught in 6A.9's own closure:** the fixture was
  `import`ed by `test_skill_resolution.py` (confirmed by grep) but never
  once actually passed to `evaluate_skill_readiness`/`resolve_skill_for_
  troubleshooting`/`run_troubleshooting_assessment` or asserted against
  anywhere in the existing 6A.9 test suite — a dead import, not a tested
  code path. 6A.9's own closure report never claimed to have proven the
  CONFLICTING case end-to-end; it is P11-M11 (6A.11 Pass 1) that first
  attempted to actually exercise it, per this pass's own explicit
  instruction (section 26).
- **Corrective action:** `context_package_fault_conflicting` now asserts
  ONE real, non-conflicting `FAULT` value (so a Skill requiring only
  `fault`, like the current production Skill, can still legitimately
  reach it) PLUS two genuinely conflicting `VENDOR` assertions (SINGULAR
  cardinality, "Ericsson" vs. "Nokia", different origins) — the function
  NAME and SIGNATURE are unchanged (the one pre-existing, dead import in
  `test_skill_resolution.py` is unaffected), only its internal assertion
  set was corrected so its behavior now genuinely matches its name.
  `backend/context/domain/models.py`'s `reduce_dimension`/`DIMENSION_
  CARDINALITY` were NOT touched — they were already correct.
- **Regression protection:** `backend/tests/troubleshooting_manager/
  test_6a11_conflicting_context_integrated.py` (NEW, P11-M11 Pass 1) --
  proves, using the CORRECTED fixture: (1) the conflicting `VENDOR`
  survives, unresolved, into the assembled `ContextPackage.telco_context`
  even while the production Skill (which only requires `fault`) still
  legitimately proceeds; (2) a LOCALLY-CONSTRUCTED test `SkillDefinition`
  requiring `vendor` correctly reports `NOT_READY` with an explicit
  `"...CONFLICTING"` reason via the real, unmodified `evaluate_skill_
  readiness`; (3) determinism across 5 repeated evaluations — never an
  arbitrary winner.
- **Fix commit SHA(s):** none yet — uncommitted at the time this entry
  was written; see the P11-M11 Pass 1 closure report.
- **Related tests:** `backend/tests/troubleshooting_manager/
  test_6a11_conflicting_context_integrated.py`, `test_skill_
  resolution.py` (its own dead import, unaffected).
- **Related docs:** `docs/INTELLIGENCE_ARCHITECTURE.md` §9/§11 (6A.2
  CONFLICTING semantics, unchanged).
- **Notes:** this is a genuine, if narrow, illustration of why "a
  fixture exists and is imported" is not evidence it was ever actually
  exercised — the same discipline this codebase already applies to
  production code (SEARCH RESULT != EVIDENCE USED) applies equally to
  test infrastructure.

===================================================================
DEF-0023 — [OPEN — NARROWED] Troubleshooting Manager's hybrid Knowledge
path has no production Evidence-index population/reconciliation
mechanism
===================================================================

- **Status:** OPEN — NARROWED (not implemented by this entry; recorded
  for a future milestone).
- **Severity:** MEDIUM (Troubleshooting Manager's own advisory path
  degrades safely to `NEEDS_INFORMATION` rather than fabricating an
  answer — confirmed live, see the live acceptance regression under
  DEF-0024 below, where team_manager's own routing sent a troubleshooting
  question to `troubleshooting_manager` and it correctly reported it
  needed more information rather than inventing content — but the
  specialist is effectively unable to ground an answer in real governed
  Knowledge until this is closed).
- **Detected during:** the Phase 6A Live Acceptance audits (Retrieval
  Path Reconciliation, then Troubleshooting Specialist Routing & Hybrid
  Evidence Reconciliation) that preceded this corrective pass, and
  re-confirmed live during this pass's own live acceptance regression.
- **Affected capability:** `backend/agents/troubleshooting_manager/
  context_support.py`'s `query_selected_evidence` — composes the real,
  unmodified 6A.4 `narrow_knowledge` → 6A.5 `hybrid_retrieve` →
  `select_evidence` pipeline, which depends on `slopanoc_knowledge_
  evidence_index` (pgvector) actually containing indexed rows for the
  governed corpus. There is no production code path anywhere in this
  repository that populates that index from `slopanoc_knowledge_objects`
  — 6A.5's own closure explicitly built the indexing mechanism
  (`index_knowledge_object`) but never wired it to run automatically on
  ingestion/governance, and no later milestone has closed that gap.
- **Symptom:** a `troubleshooting_manager` request against a real
  governed corpus with no evidence-index rows resolves
  `permitted_knowledge_ids` (6A.4, correct) but then finds zero hybrid
  retrieval hits (6A.5, correct given an empty index) and zero evidence
  to select — `run_troubleshooting_assessment` correctly reports
  `NEEDS_INFORMATION` rather than fabricating an answer (this is the
  SAFE, INTENDED fail-closed behavior 6A.9 built; it is not itself a
  defect), but the specialist is functionally unable to give a grounded
  troubleshooting assessment for any real corpus content until indexing
  exists.
- **Root cause:** a missing production wiring/reconciliation step, not a
  logic defect in any of 6A.4/6A.5/6A.9/6A.10's own frozen code — all of
  those were independently confirmed correct by direct audit during the
  Phase 6A Live Acceptance audits.
- **Explicitly OUT OF SCOPE for this corrective pass, per its own
  instruction:** "do NOT populate the hybrid Evidence index." This entry
  exists so a future milestone (e.g. a dedicated 6A.5/6A.9 follow-up)
  builds an explicit, deterministic indexing/reconciliation mechanism —
  most likely triggered at governance time (`approve_version`) or via a
  bounded backfill job — rather than rediscovering this gap from a live
  symptom again.
- **Related tests:** none added by this pass (explicitly out of scope).
- **Related docs:** `docs/KNOWLEDGE_CONTRACT.md` §27 (6A.5), §28 (6A.6);
  `docs/INTELLIGENCE_ARCHITECTURE.md` §19/§20 (6A.9/6A.10).
- **Notes:** this entry deliberately supersedes any earlier, broader
  framing of "the hybrid Evidence index is empty" as a defect in
  `incident_manager`'s own retrieval path — `incident_manager` never
  uses the 6A.4/6A.5 hybrid path at all (it uses the independent,
  unaffected 5.1G/5.1H lexical `KnowledgeRetrievalService`, confirmed
  live and working correctly throughout this pass's own validation) — the
  gap is scoped exclusively to `troubleshooting_manager`'s own hybrid
  Evidence consumption.

===================================================================
DEF-0024 — Alarm/procedure enumeration collapsed into one governed
section, allowing a follow-up question to surface a different
procedure's command
===================================================================

- **Status:** FIXED.
- **Severity:** HIGH (a real, live-observed case where a troubleshooting
  follow-up question — "give me the first cmd" — could surface an
  operational command belonging to a DIFFERENT alarm's restart procedure
  than the one actually being discussed; in this project's own domain,
  presenting the wrong restart command as though it were approved
  guidance for the active alarm is a genuine operational-safety concern).
- **Detected during:** live UI acceptance testing (see the two prior
  Phase 6A Live Acceptance audit passes: Retrieval Path Reconciliation,
  then Troubleshooting Specialist Routing & Hybrid Evidence
  Reconciliation), fixed and closed by this dedicated corrective
  implementation pass.
- **Affected capability:** `backend/knowledge/processing/processor.py`'s
  `HeadingStructureProcessor` (structural segmentation) and `backend/
  agents/incident_manager/evidence.py`'s troubleshooting-guidance capture
  (command grounding).
- **Reproduction (confirmed live, before this pass):** the real, already-
  governed `A5-VALIDATION-DOCUMENT1` object contained exactly ONE
  section for its entire content — its real source text enumerates 8
  independent, separately-named alarm procedures (HW Partial Fault, HW
  Fault, Linearization Disturbance - Performance Degraded, No Connection,
  RET Failure, RET Not Calibrated, SW Error, VSWR Over Threshold) using a
  bare parenthesis-numbered marker (`"1) HW Partial Fault"`, `"8) VSWR
  Over Threshold"`, ...) rather than Markdown `#` heading syntax, which
  `HeadingStructureProcessor` (Phase 5.1D) did not recognize at all — the
  entire alarm-specific-actions block collapsed into one governed
  section. Turn 1 of a live conversation ("I have a VSWR over threshold
  alarm, how do I troubleshoot it?") correctly retrieved and grounded on
  that one section. Turn 2 ("give me the first cmd") re-ran retrieval,
  again matched the SAME single, undifferentiated section (since it is
  the only section that exists, and it happens to also contain the HW
  Partial Fault procedure's own real restart command), and the model
  incorrectly surfaced that command as though it were the VSWR
  procedure's own next step — even though the governed VSWR Over
  Threshold procedure itself explicitly states "No restart allowed."
- **Root cause (two independent, compounding gaps, both closed by this
  pass):**
  1. STRUCTURAL: `HeadingStructureProcessor` recognized only ATX
     Markdown `#` heading syntax as a section boundary. A real document
     enumerating multiple independent, explicitly-named operational
     procedures using a different, equally-common convention (a bare
     `N) <name>` marker) produced no independently-selectable sections
     at all for those procedures — confirmed by direct re-processing of
     the real, live governed payload (see EVIDENCE below).
  2. GROUNDING: even with correct segmentation, nothing previously
     verified that a command a model proposed in `troubleshooting_
     guidance.command` was actually present in the specific governed
     section(s) genuinely selected for THIS turn — `COMMAND TRUST AND
     PRESERVATION` (A5) only required verbatim fidelity to *some* source
     text, never confinement to the currently-active procedure's own
     section. A model conflating two procedures within a single
     retrieved section (or, in principle, across several ambiguously
     multi-selected sections) had no deterministic backstop.
  A third, related but SEPARATE finding from the routing-reconciliation
  audit — team_manager's specialist routing for a troubleshooting-phrased
  question does not always choose `incident_manager` — was investigated
  and found to be an independent, pre-existing model-routing behavior,
  explicitly out of this pass's scope to alter (see its own note under
  EVIDENCE below); it does not change the root cause above, since
  `incident_manager`'s own pipeline is where the unsafe command
  substitution actually occurred and is fixed here regardless of which
  specialist a given live turn happens to route through.
- **Fix (generic, deterministic, non-alarm-specific — no LLM/semantic
  parsing, no hardcoded alarm/vendor vocabulary anywhere in production
  code):**
  1. `backend/knowledge/processing/processor.py` gained a second,
     equally generic structural heading marker,
     `_NUMBERED_PROCEDURE_HEADING_PATTERN` (`^(\d+)\)\s+(\S.*)$`),
     recognized identically alongside the existing `#` ATX pattern.
     Validated safe against the real corpus BEFORE implementation (a
     read-only scan of all 4 real governed objects confirmed this exact
     `N)` syntax occurs nowhere except Document1's own alarm
     enumeration) and AFTER (re-processing all 4 real objects through
     the fixed processor shows only Document1 changes section count —
     Aurora, Rogers 4G, and Rogers 4G/5G are byte-for-byte unaffected).
  2. `backend/agents/incident_manager/evidence.py` gained
     `enforce_procedure_scoped_command_grounding` — a deterministic,
     identity/content-based safeguard, wired into the existing
     `after_agent_callback` chain immediately before troubleshooting
     guidance is rendered: any `command` (in `NEXT_STEP` or per-step in
     `FULL_PROCEDURE` mode) is verified as a verbatim substring of the
     run's own actually-SELECTED Knowledge evidence sections for this
     turn (`snapshot_selected_knowledge_evidence`) — never merely
     "somewhere in governed knowledge." A command not grounded in every
     currently-selected section (never grounded in only one of several
     ambiguously co-selected sections) is stripped and replaced with a
     fixed, deterministic fallback statement; a genuinely
     command-less/no-evidence-selected turn is unaffected.
  3. `backend/agents/team_manager/prompts.py` gained a "GOVERNED-
     KNOWLEDGE FOLLOW-UP CONTINUITY" paragraph: since `incident_manager`
     is invoked fresh on every delegation and structurally never sees
     this conversation's own prior turns (confirmed by direct reading of
     installed `google-adk`'s `AgentTool.run_async`, which always
     constructs a brand-new `InMemorySessionService`/session per call),
     the responsibility for resolving a short referential follow-up
     ("give me the first cmd") into an explicit, self-contained
     `question` naming the actual active topic belongs to `team_manager`
     — which genuinely retains conversation history — never to
     `incident_manager`. `backend/agents/incident_manager/prompts.py`'s
     own paragraph was corrected in place to remove an earlier, FALSE
     claim (from this same pass's first draft) that incident_manager
     could resolve continuity "from conversation history already visible
     to you" — it cannot; its own responsibility is narrower: ground
     retrieval in whatever `question` it is actually given. Both prompt
     additions are deliberately generic — no alarm/vendor-specific
     vocabulary in team_manager's own prompt, enforced by the
     pre-existing `test_prompt_never_dumps_troubleshooting_methodology`
     test (which this pass's own first draft accidentally violated and
     then corrected before closing).
- **EVIDENCE:**
  - Real Document1 before/after (read from the live Cloud SQL DEV
    database, read-only): BEFORE — 1 section, 1949 chars. AFTER
    (re-processed through the fixed `HeadingStructureProcessor`) — 9
    sections: 1 preamble/preconditions section plus 8 independently
    headed procedure sections (`HW Partial Fault`, `HW Fault`,
    `Linearization Disturbance - Performance Degraded`, `No Connection`,
    `RET Failure`, `RET Not Calibrated`, `SW Error`, `VSWR Over
    Threshold`).
  - Real-corpus non-regression (all 4 real governed objects,
    re-processed through the fixed processor): `E2E-KM-AURORA-001`
    before=2 after=2; `A5-VALIDATION-DOCUMENT1` before=1 after=9
    (the fix); `A5-VALIDATION-ROGERS-4G` before=20 after=20;
    `A5-VALIDATION-ROGERS-4G5G` before=20 after=20 — the new marker
    changes ONLY Document1.
  - Governed-knowledge live validation (real Cloud SQL DEV, canonical
    path only — `materialize_candidate` → `approve_version` →
    `SqlAlchemyKnowledgeRepository.add()`, never a raw SQL edit, never
    overwriting the existing approved v1 payload in place): a real
    `v2-def0024-validation` version, declaring `supersedes=["v1"]`, was
    added for `A5-VALIDATION-DOCUMENT1` — confirmed 9 real sections;
    confirmed v1 remained byte-for-byte unchanged before/after; confirmed
    the real, unmodified 5.1E `resolve_current_version` correctly
    resolved v2 (not v1) as current once effective. The validation
    version was then removed via a scoped, identity-exact
    `DELETE ... WHERE knowledge_id = ... AND version_label = ...`
    (never a table-level DDL statement, never touching v1 or any other
    knowledge_id) — restoring the DEV corpus to its exact pre-validation
    state (only v1 remains), matching this project's own established
    live-validation-cleanup discipline.
  - Live acceptance regression, `incident_manager` driven directly
    through a real ADK Runner and the real, configured Vertex/Gemini
    model (against an isolated repository seeded with the real, fixed,
    9-section Document1 content — the shared Cloud SQL DEV corpus was
    intentionally left at its restored baseline per the paragraph
    above): Turn 1 ("What should be checked for a VSWR Over Threshold
    alarm?") correctly answered diagnosis-only, no restart, no
    fabricated command. Turn 2 (the self-contained, team-manager-resolved
    equivalent of "give me the first cmd" — "What is the first command
    for troubleshooting a VSWR Over Threshold alarm?") correctly returned
    `command: null` with the real "no restart allowed, diagnosis only"
    guidance — the HW Partial Fault restart command
    (`accn FieldReplaceableUnit=RRU-9 restartunit 1 1 1`) did NOT leak
    into this answer. Turn 3 (an explicit topic change — "What is the
    first command for troubleshooting a HW Partial Fault alarm?")
    correctly surfaced that procedure's own real command verbatim,
    proving the new safeguard is scoped to the active procedure, never
    globally suppressive.
  - Separately, `team_manager`'s own routing decision for a
    troubleshooting-phrased question (via the real `team_manager` agent,
    same real Gemini stack) was observed, in this environment/session, to
    favor `troubleshooting_manager` over `incident_manager` for this
    exact scenario — a real, honestly-recorded model-routing observation,
    consistent with DEF-0023 above and with this project's own
    previously-documented "live wording/decision-sensitivity" pattern
    (A5 Test D, 6A.10 real-model routing variance) — NOT a new defect,
    and explicitly out of this pass's scope to alter (`do NOT modify Team
    Manager routing`). It does not undermine the live acceptance proof
    above, which validates the specific `incident_manager` pipeline this
    pass's own code changes apply to.
- **Regression:** full backend suite subset covering
  `backend/tests/knowledge/`, `backend/tests/context_engineering/`,
  `backend/tests/troubleshooting_manager/`,
  `backend/tests/troubleshooting_intelligence/`, plus every
  incident_manager/knowledge/troubleshooting/evidence/p5_1/p4b/p4a/
  provenance/DEF-0024-keyword-matched test elsewhere in the suite: clean
  except 2 pre-existing, independently-confirmed real-Gemini-output-
  variance failures in unrelated, untouched files
  (`test_6a11_pass2_stress_matrix.py::test_knowledge_injection_treated_
  as_data_never_an_instruction`,
  `test_real_model_validation.py::test_real_model_advisory_ready_for_
  well_formed_request` — both from 6A.9/6A.11, both confirmed to pass
  standalone, both with an empty scoped `git diff` against every file
  this pass touched).
- **Fix commit SHA(s):** none yet — uncommitted at the time this entry
  was written; see this corrective pass's own closure report.
- **Related tests:** `backend/tests/test_def_0024_procedure_grounding.py`
  (NEW — 17 tests covering generic segmentation, non-hardcoding,
  independent procedure selectability, cross-procedure command-leak
  prevention, ambiguous multi-selection fail-closed behavior, and
  multi-turn continuity semantics), `backend/tests/knowledge/
  test_processing_reference_processor.py` (existing genericity/no-
  hardcoded-vocabulary AST proofs, re-confirmed unaffected),
  `backend/tests/test_evidence_troubleshooting_guidance.py` (4
  pre-existing tests updated to establish real selected-evidence state,
  the only pre-existing test content this pass changed for a reason
  other than a legitimate length-assertion update),
  `backend/tests/test_knowledge_real_corpus_full_pipeline.py` (one
  pre-existing safety-critical assertion —
  `test_document1_vswr_prohibition_survives_into_a_real_section` —
  corrected to scope its check to the real, now-independently-segmented
  VSWR procedure section rather than "the first VSWR mention across
  every matching section concatenated together," which stopped being a
  sound proxy once segmentation became genuinely granular).
- **Related docs:** `docs/KNOWLEDGE_CONTRACT.md` (Phase 5.1D structural
  processing, §24/§26 multimodal/narrowing), `docs/TROUBLESHOOTING_
  STRATEGY.md` (the non-negotiable one-check-at-a-time/evidence-
  interpreted-before-proceeding principles this fix directly protects).
- **Notes:** the deterministic grounding safeguard
  (`enforce_procedure_scoped_command_grounding`) is the primary, durable
  safety mechanism — it holds regardless of prompt wording quality or
  future model behavior drift; the two prompt additions (team_manager's
  follow-up-resolution responsibility, incident_manager's corrected
  scope-to-given-question responsibility) are a best-effort layer on top,
  not a substitute for it.

===================================================================
DEF-0025 — [DEFERRED / OPEN] no Knowledge inventory/catalog capability
exists to enumerate governed documents and their structural coverage
===================================================================

- **Status:** OPEN — DEFERRED (unaffected by this pass; explicitly out
  of scope per this corrective pass's own instruction — "do NOT
  implement DEF-0025").
- **Severity:** LOW/MEDIUM (an operability/observability gap, not a
  correctness defect — nothing currently retrieves or presents incorrect
  content because of this gap).
- **Detected during:** the Phase 6A Live Acceptance audits preceding this
  corrective pass.
- **Affected capability:** none currently implemented — this entry
  records the ABSENCE of a capability, not a defect in an existing one.
  There is no existing tool, endpoint, or admin surface that lets an
  operator ask "what governed documents exist, what alarms/procedures do
  they actually cover, and how are they structurally segmented?" — the
  DEF-0024 investigation itself required a bespoke, one-off, read-only
  Cloud SQL query to discover Document1's real section shape, rather than
  any existing inventory/catalog mechanism.
- **Root cause:** not applicable — a missing capability, not a bug.
- **Explicitly OUT OF SCOPE for this corrective pass, per its own
  instruction.** Recorded here, unchanged in status, so a future
  milestone can design this deliberately (a "MOP Acceptance Gateway" or
  similar admin/ingestion-quality surface was explicitly named as
  something this pass must NOT build) rather than accreting ad-hoc
  scripts.
- **Related docs:** `docs/KNOWLEDGE_CONTRACT.md` §25 (Knowledge Asset
  Metadata Standard — a plausible future home for document-level
  coverage/inventory fields, not built here).
- **Notes:** unaffected by DEF-0024's fix — the new `N)` segmentation
  marker makes a document's real structure MORE inspectable once
  processed, but does not itself provide any inventory/catalog surface.

===================================================================
DEF-0026 — Governed-Knowledge completion remediation lost topic identity
on a context-poor follow-up ("give me the first cmd")
===================================================================

- **Status:** FIXED.
- **Severity:** HIGH (same operational-safety class as DEF-0024 -- a
  short, context-poor follow-up question could surface a different
  governed procedure's own command than the one actually being
  discussed, or fail closed for a question that should have succeeded).
- **Detected during:** the same Phase 6A Live Acceptance audit pass that
  found DEF-0024; fixed and closed in the corrective implementation pass
  immediately following it (recorded here for the register's own
  completeness -- this entry documents already-implemented, already-
  tested code found in the working tree, not new work performed by a
  later pass).
- **Affected capability:**
  `backend/agents/team_manager/governed_knowledge_completion.py`'s
  `enforce_governed_knowledge_at_completion` -- the bounded, deterministic
  remediation `chat_service.py` triggers whenever a turn declared
  `requires_governed_knowledge=true` but finished with no selected
  governed evidence.
- **Reproduction:** a genuinely successful VSWR-scoped turn, followed by
  a short, topic-free follow-up ("give me the first cmd"). The
  remediation built its nested `IncidentManagerRequest` from the raw
  current-turn text alone (`chat_service.py`'s `_remediation_question`),
  with no way to recover which governed procedure the conversation had
  just been discussing -- the remediation's own `incident_manager`
  sub-run either failed closed after its own bounded compliance retry
  exhausted itself, or confidently selected a real, genuinely-grounded
  command from the WRONG sibling procedure (DEF-0024's own procedure-
  scoped grounding safeguard correctly does not strip it in that case,
  because it IS verbatim-grounded in the section that was, wrongly,
  selected).
- **Root cause:** a lost-topic-identity gap at ONE specific remediation
  boundary -- team_manager's own "GOVERNED-KNOWLEDGE FOLLOW-UP
  CONTINUITY" prompt paragraph only has a chance to run during team_
  manager's OWN first delegation attempt, never during this
  deterministic, Python-authored remediation (which exists precisely
  because that first attempt already failed to leave verifiable evidence
  behind). Not a provenance/grounding defect -- `enforce_procedure_
  scoped_command_grounding` (DEF-0024) behaved exactly as designed in
  both failure modes; the underlying problem was upstream of it.
- **Fix (generic, deterministic, no NLP/keyword routing):**
  `backend/api/governed_evidence_continuity.py` (NEW) -- a plain,
  overwritable session-state value (`LAST_SELECTED_GOVERNED_EVIDENCE_
  STATE_KEY`, mirroring `state_sync.py`'s own "current continuity
  anchor" pattern, never a per-turn accumulating history) holding the
  stable IDENTITY (never the prose) of the governed evidence a prior,
  genuinely successful turn in the SAME session actually selected.
  `revalidate_prior_governed_evidence` re-confirms each stored
  `(knowledge_id, version_label, section_id)` against the REAL, live
  repository (still `APPROVED`, still the currently-resolved version,
  section still present) before it is ever trusted for anything --
  never blind trust of stale identity. `detect_explicit_sibling_topic_
  override` performs one plain, case-insensitive VERBATIM SUBSTRING
  check of the current question against every OTHER real section
  heading already known to exist in the SAME governed document -- an
  explicit topic change in the current question always wins,
  structurally, never only via prompt compliance. Exactly one surviving,
  revalidated identity scopes the remediation's own question text
  (`build_scoped_remediation_question`, a deterministic template, never
  model-generated prose); more than one distinct surviving identity is
  genuinely ambiguous and short-circuits to a deterministic
  clarification WITHOUT invoking `incident_manager` at all
  (`build_ambiguous_procedure_clarification`); zero valid prior identity
  preserves the original, unscoped behavior exactly, with a more useful
  failure clarification when it still fails
  (`build_scoped_failure_clarification`/`GENERIC_MISSING_PROCEDURE_
  CLARIFICATION`). `enforce_governed_knowledge_at_completion` gained a
  `prior_governed_evidence` parameter the caller (`chat_service.py`)
  populates from durable session state; `chat_service.py` also writes
  that state after every turn via `build_last_selected_governed_
  evidence_state_update` (a pure function over the turn's own trusted,
  already-provenance-validated `selected_knowledge_evidence` -- an empty
  list is a no-op, so a failed/non-governed turn never blanks out a
  previously valid continuity anchor). SEARCH RESULT != EVIDENCE USED
  remains completely intact -- this module never selects evidence
  itself and never lets stored identity bypass a real `knowledge_
  search`/`knowledge_select_evidence` round trip; it only ever changes
  what QUESTION TEXT the remediation's own real `incident_manager`
  sub-run receives, and separately decides when a stored identity is
  even still safe to reference at all.
- **Regression:** `backend/tests/test_def_0026_governed_evidence_
  continuity.py` (26 tests -- revalidation success/staleness/supersession/
  archival/missing-section cases, deduplication, explicit-sibling-topic-
  override detection, deterministic clarification/question-augmentation
  wording, zero/one/many-candidate remediation-boundary behavior,
  real-Runner-driven end-to-end proofs that a scoped follow-up stays on
  topic and an explicit topic change is never overridden by stale
  identity) -- all passing; re-confirmed passing as part of the DEF-0027
  corrective pass's own regression run (below), unmodified by it.
- **Fix commit SHA(s):** none yet -- uncommitted at the time this entry
  was written (backfilled into the register by the DEF-0027 corrective
  pass; the code and its own tests were already present and passing in
  the working tree before that pass began).
- **Related tests:** `backend/tests/test_def_0026_governed_evidence_
  continuity.py`.
- **Related docs:** `backend/api/governed_evidence_continuity.py`'s own
  module docstring (the full design record); DEF-0024 above (the
  complementary, procedure-scoped command-grounding safeguard this entry
  keeps completely unmodified and untouched).
- **Notes:** complementary to DEF-0024, never overlapping -- DEF-0026
  keeps the CORRECT procedure identity selected across a context-poor
  remediation boundary; DEF-0024 keeps any COMMAND grounded in whatever
  procedure was actually, genuinely selected for the turn.

===================================================================
DEF-0027 — Composite/paraphrased command for a conditional procedure
falsely reported as "the approved procedure does not specify a command"
===================================================================

- **Status:** FINAL CORRECTIVE PASS IMPLEMENTED AND REGRESSION-TESTED;
  PENDING MANUAL BROWSER UI ACCEPTANCE (see "LIVE ACCEPTANCE AUDIT
  FINDING" and "FINAL CORRECTIVE PASS" below -- the FIRST corrective pass
  recorded further down in this entry was marked FIXED/PENDING
  ACCEPTANCE, then FAILED real manual browser acceptance with a
  HIGH-severity cross-document contamination symptom; this entry was
  reopened to OPEN, root-caused by a dedicated audit, and closed again by
  a second, FINAL corrective pass -- not yet re-confirmed by a human at
  the time this update was written).
- **Severity:** HIGH (same operational-safety class as DEF-0024/DEF-0026
  -- a real governed procedure that specifies DIFFERENT commands
  depending on an unknown condition, e.g. which specific unit/equipment
  is affected, could be reduced by the model to one fabricated composite/
  paraphrased command string; the existing DEF-0024 safeguard correctly
  rejected it, but the resulting user-facing message falsely implied the
  procedure has no command at all -- an operationally misleading, not
  merely cosmetic, failure).
- **Detected during:** live UI acceptance testing of `A5-VALIDATION-
  DOCUMENT1` v2-def0024-fix's own real "HW Partial Fault" section (a
  genuinely conditional procedure: a restart command for an RRU, a
  DIFFERENT restart command for an AAS, and explicitly no restart at all
  for a SupportUnit).
- **Affected capability:**
  `backend/agents/incident_manager/evidence.py`'s `enforce_procedure_
  scoped_command_grounding` (DEF-0024) and `_capture_and_render_
  troubleshooting_guidance`'s fixed fallback-text selection.
- **Reproduction (confirmed live, before this pass):** "how do i handle
  HW Partial Fault?" -- the model understood the procedure and its
  conditions, but generated a composite/paraphrased command string
  combining pieces of the RRU and AAS branches rather than asking which
  unit was affected. DEF-0024's own verbatim-grounding check correctly
  rejected the fabricated string (it does not appear literally in the
  selected section), but the UI then stated "The approved procedure does
  not specify a command for this step" -- FALSE: the active procedure
  genuinely DOES specify commands; the real problem was that the
  required equipment condition was not yet known and the model's own
  generated command was not safely grounded.
- **Root cause (two independent, compounding gaps, both closed by this
  pass):**
  1. COMMAND AUTHORIZATION SCOPE: DEF-0024's own rule required a command
     to be grounded in EVERY currently-selected section whenever more
     than one was selected this turn -- correct as a fail-closed
     default, but too broad when a genuinely ACTIVE procedure (e.g. "HW
     Partial Fault", explicitly named by the current question) is
     selected ALONGSIDE a merely SUPPORTING sibling section (e.g. "HW
     Fault") whose own real command the active procedure never repeats
     -- there was no deterministic way to identify which selected
     section was actually "active" for this turn.
  2. FALLBACK WORDING: a single, generic fallback sentence ("does not
     specify a command") was used for every rejection uniformly,
     including the case where the active section genuinely DOES contain
     real commands but the model's SPECIFIC proposal failed exact
     verbatim grounding (a composite/paraphrased string) -- actively
     misleading in that case.
- **Fix (generic, deterministic, non-alarm-specific -- no LLM/semantic
  parsing, no hardcoded alarm/vendor/equipment vocabulary anywhere in
  production code):**
  1. `_resolve_active_section_id` (evidence.py, NEW) -- a small,
     deterministic ACTIVE PROCEDURE resolver: exactly one selected
     section is trivially active; with more than one selected, the
     current turn's own trusted `chat_topic`/`question` text (extracted
     by `_extract_incoming_question_text`, the same "this invocation's
     real top-level Content" guarantee `MultimodalAgentTool`/`capture_
     known_applicability_context` already rely on) is checked for a
     verbatim, case-insensitive occurrence of exactly ONE candidate's
     own real, already-retrieved section heading -- zero or more than
     one match is reported unresolved, never guessed.
  2. `_evaluate_command` (evidence.py, NEW) replaces "grounded in every
     selected section" with "grounded in the ACTIVE PROCEDURE section"
     wherever one can be identified, while PRESERVING DEF-0024's own
     fail-closed default and its own universal-safe-command exception
     (a command genuinely present in EVERY currently-selected section is
     never blocked regardless of which one is "active") for the
     genuinely ambiguous case.
  3. `CommandGroundingReason` (evidence.py, NEW, a typed enum:
     `TRUE_ABSENCE`/`MISSING_CONDITION`/`GROUNDING_REJECTED`/
     `AMBIGUOUS_PROCEDURE`) -- a rejected command's WHY is now decided
     from the same identity/content signals the grounding check itself
     already used (a generic, deterministic token-overlap classifier,
     `_classify_absence_or_rejection`, mirroring this codebase's own
     established 5.1G `TokenOverlapRelevanceScorer` philosophy -- never
     a semantic/NLP judgment, and never a factor in the underlying
     safety decision, only in which fixed explanatory sentence is
     shown), never inferred from final free-text strings.
     `enforce_procedure_scoped_command_grounding_with_reason` is the new
     3-tuple-returning function carrying this reason; the original
     `enforce_procedure_scoped_command_grounding` becomes a thin,
     backward-compatible 2-tuple wrapper over it (every pre-existing
     DEF-0024 caller/test is unaffected). `MISSING_CONDITION` is never
     assigned by this module's own code -- it names the SAFE, well-
     behaved state where the model itself correctly leaves `command`
     unset and asks for the missing condition instead.
  4. `backend/agents/incident_manager/prompts.py` gained one new
     "CONDITIONAL COMMAND HANDLING" paragraph: when the applicable
     Approved procedure specifies different commands depending on an
     unknown condition, the model must never guess/average/combine them
     into a composite command -- it must leave `command` unset and ask
     for the missing condition via `next_action`/`evidence_requested`
     instead. Advisory (deterministic validation above remains the
     authoritative backstop) -- consistent with this codebase's own
     "prompt instructions alone are not a sufficient control" principle.
- **EVIDENCE (real Cloud SQL DEV corpus, read-only, via the Cloud SQL
  Auth Proxy, no mutation):** `A5-VALIDATION-DOCUMENT1` v2-def0024-fix
  reconfirmed present, `APPROVED`, current, 9 sections. Its real "HW
  Partial Fault" section content confirmed to contain, verbatim, both
  `accn FieldReplaceableUnit=RRU-9 restartunit 1 1 1` and `accn
  FieldReplaceableUnit=AAS-1 restartunit 1 1 1`, plus a `SupportUnit=---`
  / "No restart" branch. The new `_evaluate_command`/`_resolve_active_
  section_id` logic was run directly against this REAL fetched section
  content (never a synthetic placeholder): the real RRU command and the
  real AAS command each independently pass when "HW Partial Fault" alone
  is selected; a composite of both is rejected as `GROUNDING_REJECTED`;
  with "HW Partial Fault" (active, explicitly named by the current
  question) and "HW Fault" (supporting) both selected, the real RRU
  command passes even though it is absent from "HW Fault"'s own content
  (the key relaxation from DEF-0024's own "grounded in every selected
  section" default); the SAME dual-selection with NO question given
  still correctly fails closed as `AMBIGUOUS_PROCEDURE`, preserving
  DEF-0024's own original safe default exactly when active-procedure
  resolution is unavailable.
- **Regression:** `backend/tests/test_def_0027_conditional_command_
  safety.py` (NEW -- 15 tests covering active-procedure identity
  resolution, supporting-evidence-cannot-veto/cannot-authorize, unknown-
  condition safe handling, correct per-branch command resolution once
  known, composite/paraphrased rejection, and the full typed fallback-
  reason model); `backend/tests/test_def_0024_procedure_grounding.py`
  (17 tests, unmodified, all still passing -- the backward-compatible
  wrapper preserves every existing behavior exactly);
  `backend/tests/test_def_0026_governed_evidence_continuity.py` (26
  tests, unmodified, all still passing); `backend/tests/test_evidence_
  troubleshooting_guidance.py` (unmodified, all still passing); full
  backend regression suite -- see this defect's own closure report for
  exact pass/fail counts.
- **Fix commit SHA(s):** none yet -- uncommitted at the time this entry
  was written; per explicit instruction, this corrective pass does not
  commit or push.
- **Related tests:** `backend/tests/test_def_0027_conditional_command_
  safety.py`.
- **Related docs:** DEF-0024 above (the safeguard this pass extends,
  never redesigns); DEF-0026 above (the complementary follow-up-identity
  safeguard, completely untouched by this pass).
- **Notes:** deliberately does NOT introduce a `conditional_commands[]`
  field, a Request Contract, hybrid Evidence-index population (DEF-0023),
  a Knowledge inventory capability (DEF-0025), or any Team Manager
  specialist-routing change -- all explicitly out of this pass's own
  scope.

===================================================================
LIVE ACCEPTANCE AUDIT FINDING (the FIRST corrective pass above FAILED
real manual browser acceptance -- reopened, root-caused, and closed
again by the FINAL CORRECTIVE PASS below)
===================================================================

- **Real browser sequence that failed:** "how do i handle HW Partial
  Fault?" correctly asked for unit type; RRU/AAS follow-ups correctly
  returned their own real governed commands (PASS). But the SAME first
  question, on a separate occasion, produced a large mixed procedure
  combining Document1's own real "HW Partial Fault" content WITH an
  unrelated `MOP_Rogers ERICSSON_4G5G_Resource_Timeout...` MOP -- SSH/
  AMOS steps, DUS Radio/Baseband Radio handling, a "wait 5 minutes"
  instruction, a ticket-escalation instruction, and a bare confirmation
  token rendered as `"Run:\n\ny"`. The follow-up "it's a SupportUnit"
  -- whose real governed rule is `SupportUnit=--- -> No restart` -- was
  answered instead with an RRU restart template sourced from the Rogers
  MOP. **HIGH severity: an explicit governed no-restart safety rule was
  not honored.**
- **Root cause, confirmed by direct code trace (a dedicated audit-only
  pass, zero files changed):**
  1. `knowledge_search`'s own ranking (5.1G `TokenOverlapRelevanceScorer`
     -- pure lexical overlap, no 6A.4 deterministic narrowing wired into
     `incident_manager`'s path, a pre-existing, already-documented scope
     limit shared with DEF-0023) can legitimately return candidates from
     an entirely unrelated governed document when their text happens to
     overlap lexically; `knowledge_select_evidence`'s own only gate
     (`validate_evidence_selection`, 5.1H) checks identity/authenticity
     ONLY, never topical/procedural relevance -- nothing prevented the
     model from selecting evidence from two different governed documents
     in the same turn.
  2. The FIRST corrective pass's own active-procedure enforcement
     (`enforce_procedure_scoped_command_grounding`) was scoped
     EXCLUSIVELY to `TroubleshootingGuidance.command`/`TroubleshootingStep
     .command` -- `interpretation`/`next_action`/`evidence_requested`/
     `TroubleshootingStep.action` (all free-form prose) were never
     validated against selected-evidence content at all, so unrelated
     operational content reached the user through those fields even when
     `command` itself happened to be correctly withheld.
  3. DEF-0026's own prior-governed-evidence continuity
     (`enforce_governed_knowledge_at_completion`) was, and remains,
     invoked ONLY when a turn's `selected_knowledge_evidence` is EMPTY
     (`chat_service.py`'s own gate) -- a turn that selected SOMETHING,
     even something from an unrelated document, bypassed this safety net
     entirely, since "empty" was the only trigger condition.
  4. The command-grounding check itself (`command in active_content`, a
     literal substring test) had no minimum meaningful-length/shape
     requirement -- a single-character token like `"y"` is virtually
     guaranteed to appear inside any real section's own text, which is
     exactly how it passed grounding trivially.
- **Classification:** the missing typed Request Contract (Phase 6A.13,
  not yet built) was evaluated as a POSSIBLE prerequisite and explicitly
  REJECTED for this fix -- the audit found the ALREADY-EXISTING typed
  `KnowledgeEvidenceSelectionKey`/DEF-0026 continuity infrastructure
  sufficient to close this defect locally, once (a) its trigger condition
  was widened beyond "evidence is empty" and (b) active-procedure
  enforcement was widened beyond `command` alone.
- **Related docs:** this defect's own dedicated audit report (delivered
  as this pass's own closure report, not a separate file) traces the
  exact call graph for both failing turns and answers the full 16-
  question audit protocol.

===================================================================
FINAL CORRECTIVE PASS -- Active Procedure Enforcement Across Full
Operational Guidance
===================================================================

Three fixes, all additive to the FIRST corrective pass above (nothing
from it was reverted or redesigned):

- **Fix #1 (prior-anchor consistency even when evidence is non-empty):**
  `backend/api/governed_evidence_continuity.py` gained `detect_governed_
  evidence_anchor_mismatch` -- extends the SAME revalidation/override
  machinery DEF-0026 already provides to the NORMAL (non-remediation)
  completion boundary. `backend/api/chat_service.py`'s own governed-
  knowledge completion gate now also fires `enforce_governed_knowledge_
  at_completion` when this turn's own selected evidence is non-empty but
  (a) shares no `knowledge_id` with a single, revalidated prior anchor,
  and (b) the raw current-turn text does not verbatim-name any real
  heading of what was actually selected this turn -- an explicit new
  topic (e.g. "how do I handle Resource Activation Timeout?") still
  correctly overrides the anchor. An exception raised by this NEW
  consistency check itself fails CLOSED (forces remediation defensively)
  rather than silently trusting an unverified answer.
- **Fix #2 (full operational-guidance procedure boundary):**
  `backend/agents/incident_manager/evidence.py` gained `_guidance_scope_
  established` -- the ENTIRE `TroubleshootingGuidance` object (never only
  `command`) is now suppressed whenever this turn's selected governed
  evidence spans more than one distinct `knowledge_id` (genuinely
  different governed documents), replacing `interpretation`/`next_action`/
  `command`/`evidence_requested`/`full_procedure_steps` all with `None`/
  `[]` and a new, distinct fallback sentence
  (`CommandGroundingReason.CROSS_PROCEDURE_EVIDENCE`, "This request
  touched more than one governed procedure..."). Deliberately narrower
  than it could be: a SAME-document multi-section ambiguity (DEF-0024's
  own original VSWR + HW Partial Fault co-selection scenario) is
  completely unaffected -- this fires only on genuine CROSS-DOCUMENT
  evidence, the exact shape of the real, reported defect, never widening
  DEF-0024's own already-accepted same-document behavior. No sentence-
  level/semantic verification of the prose itself is attempted (per
  explicit instruction) -- document identity is the only deterministic
  signal used.
- **Fix #3 (command shape/meaningfulness guard):** `evidence.py` gained
  `_is_confirmation_token` -- a small, generic (natural-language
  confirmation semantics, never vendor-specific) denylist (`y`/`n`/
  `yes`/`no`/`0`/`1`/`ok`/`true`/`false`, exact match after trim+
  casefold, never a substring match) checked BEFORE any substring
  grounding is attempted, closing the `"Run:\n\ny"` soundness gap.
  Audited against the real existing command/test corpus first: `"alt"`
  (a real, legitimate 3-character AMOS command already relied upon by
  `test_evidence_troubleshooting_guidance.py`'s own pre-existing fixture)
  is deliberately NOT in this denylist and remains fully valid -- a
  length-only heuristic was considered and rejected specifically because
  it would have wrongly rejected `"alt"`.

- **Regression:** `backend/tests/test_def_0027_final_corrective_pass.py`
  (NEW -- 29 tests covering prior-anchor consistency, cross-document
  whole-guidance suppression across restart/reset/escalation/no-restart-
  override scenarios, the confirmation-token denylist including a
  parametrized sweep and a negative "alt" proof, and RRU/AAS/SupportUnit
  non-regression); `backend/tests/test_def_0024_procedure_grounding.py`
  (17, unmodified, all still passing); `backend/tests/test_def_0026_
  governed_evidence_continuity.py` (26, unmodified, all still passing);
  `backend/tests/test_def_0027_conditional_command_safety.py` (15,
  unmodified, all still passing); `backend/tests/test_evidence_
  troubleshooting_guidance.py` (unmodified, all still passing); full
  backend regression suite -- see this pass's own closure report for
  exact pass/fail counts.
- **Fix commit SHA(s):** none yet -- uncommitted; per explicit
  instruction, this corrective pass does not commit or push.
- **Related tests:** `backend/tests/test_def_0027_final_corrective_
  pass.py`.
- **Notes:** deliberately does NOT introduce a `conditional_commands[]`
  field, a Request Contract (Phase 6A.13), hybrid Evidence-index
  population (DEF-0023), a Knowledge inventory capability (DEF-0025), or
  any Team Manager specialist-routing change -- all explicitly out of
  this pass's own scope, exactly as the first pass. Live browser UI
  acceptance (per this pass's own closure report's exact required test
  conversations) remains the final gate before this entry may be marked
  fully CLOSED. Per this pass's own explicit instruction: if this FINAL
  correction still fails live acceptance, the next step is to classify
  Phase 6A.13 (Request Contract Foundation) as a prerequisite rather than
  attempting a third local patch -- not to be decided by this entry
  alone.

===================================================================
DEF-0028 — Free-form specialist prose (never `TroubleshootingGuidance`)
and PROCEDURE/INFORMATION-classified requests both bypassed the Phase
6A.14 deterministic command-safety boundary
===================================================================

- **Status:** FIXED AND REGRESSION-TESTED; PENDING FINAL LIVE BROWSER
  ACCEPTANCE (same "implemented + automated-tested, human acceptance
  still open" status class as DEF-0027's own first pass).
- **Severity:** HIGH — same operational-safety class as DEF-0024/
  DEF-0026/DEF-0027: a real governed command tied to an asset identifier
  the user never actually supplied (`RRU-9`, an EXAMPLE value inside a
  governed document, not a confirmed live target) reached the user
  completely unvalidated.
- **Detected during:** a dedicated Phase 6A.14 live-acceptance audit
  (audit-only, zero files changed) of "how do i handle HW Partial
  Fault?" as a single, first-turn question with no unit specified.
- **Affected capability:** Phase 6A.14's own `RequestExecutionDecision`/
  `enforce_execution_decision_on_guidance`
  (`backend/agents/team_manager/request_execution_policy.py`) — both
  gaps existed in that same pass's own first implementation, not in
  DEF-0024/0026/0027's own, separately-scoped grounding machinery.
- **Reproduction (confirmed by direct trace, before this pass):** the
  live response contained BOTH `accn FieldReplaceableUnit=RRU-9
  restartunit 1 1 1` and `accn FieldReplaceableUnit=AAS-1 restartunit 1
  1 1` in the same answer — no `"Run:\n\n"` marker, no numbered steps,
  i.e. the unmistakable signature of `render_troubleshooting_guidance`'s
  own deterministic output never having run at all.
- **Root cause (two independent, compounding gaps, both closed by this
  pass):**
  1. **Free-form output bypass:** `incident_manager` answered via its
     own free-form `summary` field, never populating `Troubleshooting
     Guidance`. DEF-0024/0026/0027's grounding AND Phase 6A.14's own
     first-pass `enforce_execution_decision_on_guidance` both examine
     `TroubleshootingGuidance` exclusively — neither mechanism ever saw
     this response at all, regardless of how safe each one is on its own
     terms.
  2. **Intent-scope bypass:** even when `TroubleshootingGuidance` IS
     populated, Phase 6A.14's own first-pass missing-context gate
     (`_TARGET_SPECIFIC_INTENTS`) applied only to `COMMAND`/
     `TROUBLESHOOTING` intents — a request `team_manager` classified
     `PROCEDURE` or `INFORMATION` (both plausible classifications for
     "how do i handle X") bypassed the gate entirely regardless of
     `missing_context`.
- **Fix (generic, deterministic, no text/regex command parser — every
  decision is derived from already-validated structured data, never
  from scanning response text for command-shaped substrings):**
  1. `_TARGET_SPECIFIC_INTENTS` widened to include `PROCEDURE`/
     `INFORMATION` alongside `COMMAND`/`TROUBLESHOOTING` (closes gap 2).
     `KNOWLEDGE_INVENTORY`/`ACTION` remain deliberately excluded — both
     already have their own, earlier, separate branches in `derive_
     execution_decision`.
  2. `enforce_execution_decision_on_guidance` redesigned from
     selectively stripping only `command`/`step.command` to suppressing
     the ENTIRE `TroubleshootingGuidance` object — `interpretation`/
     `next_action`/`evidence_requested`/every `TroubleshootingStep
     .action` are now all blanked (`full_procedure_steps` emptied to
     `[]`) whenever `may_emit_command=False` — closing the sub-case where
     a command could otherwise still be embedded in narrative fields
     while the structured `command` field itself is correctly left
     unset.
  3. `requires_unstructured_response_backstop` (NEW) + its `backend/api/
     chat_service.py` completion-boundary wiring — a purely decision-
     driven backstop for the case where NO `TroubleshootingGuidance`
     exists to enforce against at all: when this turn's own validated,
     CURRENT `RequestContract`/`RequestExecutionDecision` positively
     shows unresolved target/condition context (`NEEDS_INFORMATION`/
     `AMBIGUOUS`), `final_text` is unconditionally replaced with a
     deterministic clarification (`command_suppression_fallback_text`),
     regardless of whatever free-form prose the specialist actually
     produced. Deliberately does NOT fire for `INVALID_CONTRACT` (an
     absent contract is a materially weaker signal than one that
     positively proves unresolved context — firing on it too would
     over-block ordinary turns merely because `record_request_contract`
     was not called) or `REQUIRES_APPROVAL`/`UNSUPPORTED_CAPABILITY`
     (both already handled by their own, separate, pre-existing paths).
  4. Prompt strengthening, advisory only (`backend/agents/incident_
     manager/prompts.py`'s "ITERATIVE TROUBLESHOOTING" paragraph now
     mandates `troubleshooting_guidance` for ANY command-bearing answer
     regardless of how the request itself was phrased; `backend/agents/
     team_manager/prompts.py`'s `missing_context` guidance now explicitly
     covers a multi-branch conditional procedure even when the model's
     own answer plans to describe several branches together rather than
     ask one narrow question) — the deterministic mechanisms above
     remain the actual, authoritative safety boundary regardless of
     model compliance.
- **Regression:** `backend/tests/test_6a14_final_corrective_pass.py`
  (NEW — 25 tests: widened-intent-scope proofs, whole-guidance
  suppression covering `interpretation`/`next_action`/`step.action`/
  `evidence_requested`, the backstop's precise trigger conditions
  including the deliberate `INVALID_CONTRACT`/`REQUIRES_APPROVAL`
  non-firing cases, full `chat_service.py` integration reproductions of
  the exact live defect shape — no `troubleshooting_guidance`, free-form
  `summary` containing both real commands — a matching INFORMATION-
  intent-with-structured-guidance variant, and non-regression proofs
  that a fully-resolved ALLOW turn and an ordinary governed-knowledge-
  free INFORMATION turn are both completely unaffected);
  `backend/tests/test_6a13_request_contract_foundation.py` (unmodified,
  all still passing); `backend/tests/test_6a14_deterministic_request_
  execution.py` (one pre-existing test updated for the whole-guidance-
  suppression redesign — `full_procedure_steps` is now asserted `[]`
  rather than checking one step's own `.command` — every other test
  unmodified, all still passing); `backend/tests/test_def_0024_
  procedure_grounding.py`/`test_def_0026_governed_evidence_continuity
  .py`/`test_def_0027_conditional_command_safety.py`/`test_def_0027_
  final_corrective_pass.py` (all unmodified, all still passing); full
  backend regression suite — see this pass's own closure report for
  exact pass/fail counts.
- **Fix commit SHA(s):** none yet — uncommitted at the time this entry
  was written; per explicit instruction, this corrective pass does not
  commit or push.
- **Related tests:** `backend/tests/test_6a14_final_corrective_pass.py`.
- **Related docs:** DEF-0024/DEF-0026/DEF-0027 above (this pass's
  enforcement point is structurally distinct from and additive to all
  three — none of their own grounding/continuity/active-procedure logic
  was touched); `docs/INTELLIGENCE_ARCHITECTURE.md` (Phase 6A.13/6A.14's
  own `RequestContract`/`RequestExecutionDecision` contracts, extended
  here, not redesigned).
- **Notes:** deliberately does NOT start Phase 6A.15, does NOT redesign
  the `RequestContract`/`RequestExecutionDecision` architecture, does NOT
  add another agent, does NOT implement DEF-0023/DEF-0025, does NOT
  change Team Manager specialist routing, and does NOT introduce any
  generic command regex/text-parsing engine — every new safety decision
  is derived exclusively from already-validated structured fields
  (`RequestExecutionDecision.status`/`.may_emit_command`), never from
  scanning response text. Per explicit instruction, this entry
  deliberately leaves the pre-existing "the applicability of this
  information is currently unknown" sentence completely untouched — it
  was confirmed correct/expected by the prior 6A.14 audit and is not a
  symptom of this defect. Live browser UI acceptance remains the final
  gate before this entry may be marked fully CLOSED.

===================================================================
DEF-0029 — Governed-evidence continuity ambiguity check never narrows an
already-ambiguous candidate set using the current turn's own text,
validated RequestContract.subject, or a prior active-procedure anchor
===================================================================

- **Status:** FIXED AND REGRESSION-TESTED; PENDING FINAL LIVE BROWSER
  ACCEPTANCE (same status class as DEF-0027/DEF-0028's own first passes).
- **Severity:** HIGH — the same operational-safety class as DEF-0024/
  DEF-0026/DEF-0027/DEF-0028: a genuinely successful turn's own topic
  resolution was discarded, forcing the user to re-answer a question they
  had already, in effect, answered — a usability defect with safety
  adjacency (the underlying command-safety mechanisms, DEF-0024/0027, were
  never bypassed; this defect only ever produced an incorrect
  CLARIFICATION, never an incorrect command).
- **Detected during:** a dedicated audit (audit-only, zero files changed)
  of a real live conversation: Turn 1 ("how do i handle HW Partial
  Fault?") correctly asked for unit context, but genuinely selected both
  the ACTIVE "HW Partial Fault" section and a merely SUPPORTING sibling
  "HW Fault" section. Turn 2 ("it's an RRU") and Turn 3 (the exact
  repeated heading "HW Partial Fault") both incorrectly re-asked "Do you
  mean the HW Partial Fault or HW Fault procedure?".
- **Affected capability:** `backend/api/governed_evidence_continuity.py`'s
  `LAST_SELECTED_GOVERNED_EVIDENCE_STATE_KEY`/`enforce_governed_knowledge_
  at_completion`'s own `len(effective_candidates) > 1` ambiguity
  short-circuit (DEF-0026).
- **Root cause, confirmed by direct code trace (the preceding audit's own
  report):**
  1. `LAST_SELECTED_GOVERNED_EVIDENCE_STATE_KEY` persists EVERY distinct
     selected identity from a turn — both the ACTIVE procedure and any
     merely SUPPORTING sibling section — as equally authoritative
     candidates, with no concept of "active" vs. "supporting" surviving
     past the one turn that selected them (DEF-0024/0027's own turn-local
     `_resolve_active_section_id` distinction is discarded at turn
     completion).
  2. `detect_explicit_sibling_topic_override` (DEF-0026) is deliberately,
     correctly scoped to finding a heading OUTSIDE the current candidate
     set to justify abandoning all of them — it structurally EXCLUDES a
     candidate's own heading from ever being detected as an "override"
     (`if sibling in own_headings: continue`), so it was never capable of
     resolving "the user named exactly ONE of the already-ambiguous
     candidates" — a different question it was never designed to answer.
  3. `len(effective_candidates) > 1` (`governed_knowledge_completion.py`)
     short-circuited unconditionally to `build_ambiguous_procedure_
     clarification`, with no check of the current turn's own text, no
     consumption of the validated 6A.13 `RequestContract.subject`
     (confirmed absent from this module's own imports entirely), and no
     persisted single-identity "active procedure" concept to fall back
     on.
- **Fix (generic, deterministic, no fuzzy/semantic matching, no new LLM
  call, no specialist-routing change):**
  1. NEW `ACTIVE_GOVERNED_PROCEDURE_STATE_KEY` (`governed_evidence_
     continuity.py`) persists exactly ONE `KnowledgeEvidenceSelectionKey`
     — the authoritative continuation anchor — kept separate from the
     unmodified, still-full `LAST_SELECTED_GOVERNED_EVIDENCE_STATE_KEY`.
     Written via `compute_fresh_active_procedure_anchor`, which reuses
     `resolve_active_section_id` (DEF-0024/0027's own turn-local
     resolver, made public, zero behavior change) against THIS turn's own
     fresh selection and raw question text — never overwrites the
     existing anchor when the result is ambiguous or absent.
  2. NEW three-step precedence chain
     (`resolve_active_candidate_among_ambiguous`), consulted ONLY when the
     pre-existing revalidation/override logic already found more than one
     genuinely ambiguous candidate: (a) `resolve_explicit_current_
     candidate` — does the CURRENT turn's raw text verbatim name exactly
     one of the ambiguous candidates' own headings (a NEW, deliberately
     separate function from `detect_explicit_sibling_topic_override`,
     which is completely unmodified); (b) the CURRENT, already
     provenance-verified 6A.13 `RequestContract.subject` (never a raw
     model claim — only trusted when its own `run_id` matches the
     current turn and it does not itself declare `ambiguity=true`); (c)
     the existing, re-validated `ACTIVE_GOVERNED_PROCEDURE_STATE_KEY`
     anchor.
  3. `chat_service.py`'s own `current_turn_request_contract` computation
     was moved earlier in the method (a pure re-ordering, never a
     duplicate contract read/generation) so it is available BEFORE the
     governed-evidence continuity disambiguation runs, closing the
     ordering gap the audit identified.
  4. The pre-existing DEF-0027 "anchor mismatch" consistency check
     (`detect_governed_evidence_anchor_mismatch`'s caller) now compares
     against the SAME new, single-identity `ACTIVE_GOVERNED_PROCEDURE_
     STATE_KEY` instead of `LAST_SELECTED_GOVERNED_EVIDENCE_STATE_KEY`
     gated by an incidental `len == 1`, making it strictly more precise
     (never accidentally inert merely because a genuinely successful turn
     selected both an active procedure and a supporting sibling).
- **Regression:** `backend/tests/test_6a14_active_procedure_continuity.py`
  (NEW — 24 tests covering the write boundary, all three precedence
  steps individually and in combination, revalidation/staleness,
  unresolved-ambiguity non-regression, zero/multi-match non-fabrication,
  explicit topic change, cross-session isolation, RRU/AAS/SupportUnit/
  VSWR non-regression via the real DEF-0024/0027 mechanism, and a full
  `chat_service.py` wiring proof); DEF-0024 (17)/DEF-0026 (26)/DEF-0027
  both passes (44)/6A.13 (45)/6A.14 (33)/DEF-0028 (30) all re-run
  unmodified except for widening two pre-existing test-double signatures
  (`fake_remediation`/`fake_governed` in `test_p5_1j_governed_completion_
  gate.py`/`test_p5_1j_source_requirements_gate.py`, 7 call sites) to
  accept the two new keyword-only parameters — the only existing test
  content this pass changed, and only because those mocks stand in for
  the real function's own signature. Full backend suite — see this pass's
  own closure report for exact counts. No frontend file touched.
- **Fix commit SHA(s):** none yet — uncommitted; per explicit instruction,
  this corrective pass does not commit or push.
- **Related tests:** `backend/tests/test_6a14_active_procedure_continuity
  .py`.
- **Related docs:** DEF-0024/DEF-0026/DEF-0027/DEF-0028 above (this pass's
  new precedence chain is additive to all of them; none of their own
  grounding/continuity/execution-policy logic was touched);
  `docs/AGENT_CONTRACT.md` §6a (6A.13/6A.14's own contract).
- **Notes:** deliberately does NOT start Phase 6A.15, does NOT redesign
  `RequestContract`, does NOT add another agent, does NOT implement
  DEF-0023, does NOT change Team Manager specialist routing, and does NOT
  claim multi-active-procedure support (exactly one active procedure per
  continuation chain; a future architecture needing more than one must
  fail safely via the existing clarification path, never silently pick
  one). Live browser UI acceptance (the exact required conversations from
  this pass's own closure report) remains the final gate before this
  entry may be marked fully CLOSED.

===================================================================
DEF-0030 — RequestContract.missing_context trusted as the model's own
unmediated claim, with no deterministic reconciliation against verified
provided_context; identifier-formatting mismatch silently dropped
genuinely user-supplied targets
===================================================================

- **Status:** FIXED AND REGRESSION-TESTED; PENDING FINAL LIVE BROWSER
  ACCEPTANCE (same status class as DEF-0027/0028/0029's own first
  passes).
- **Severity:** HIGH — same operational-safety class as DEF-0024/0026/
  0027/0028/0029: a governed Knowledge EXAMPLE identifier (`RRU-9`)
  reached the user as a live command before any real unit identifier was
  ever user-confirmed; separately, a genuinely user-supplied identifier
  (`RRU-5`, spelled "RRU 5") was silently dropped by a purely-formatting
  mismatch, producing confusing, incomplete responses.
- **Detected during:** a dedicated live-conversation audit (audit-only,
  zero files changed — see the preceding "Live Target Parameterization
  Failure" audit) tracing the exact turn-by-turn RequestContract/
  execution-decision state for: "how do i handle HW Partial Fault?" →
  "it's an RRU" → "okie will do, also give me the cmd to restart the
  rru" (RRU-9 leaked) → "but my issues is in RRU 5 not 9" → "give me the
  cmd to restart rru 5" (no command rendered).
- **Root cause, confirmed by direct code trace (two independent gaps):**
  1. `validate_and_persist_request_contract` deterministically verified
     `provided_context` (via `_verify_and_filter_provided_context`) but
     passed `missing_context` straight through from the model's own raw
     claim, completely unvalidated — absent from the `model_copy(update=
     {...})` dict entirely. `derive_execution_decision`'s own target-
     specific gate is a pure `if contract.missing_context:` truthiness
     check with no independent verification of its own — a model that
     (correctly or not) declared `missing_context=[]` despite `unit_id`
     never having been genuinely confirmed let execution proceed to
     `ALLOW`, at which point DEF-0024/0027's own exact-verbatim command
     grounding (working exactly as designed) found `RRU-9` genuinely,
     verbatim present in the ACTIVE governed section and permitted it —
     grounding was never designed to ask "was this specific identifier
     itself user-confirmed," only "does this exact command exist in the
     correct section."
  2. `_verify_and_filter_provided_context`'s only verification paths were
     a literal, case-insensitive SUBSTRING check and an exact
     session-confirmed match — neither normalizes formatting. A model-
     claimed canonical `"RRU-5"` is never a literal substring of the
     user's own natural `"...RRU 5 not 9..."` phrasing (hyphen vs.
     space), so a genuinely, explicitly user-supplied identifier was
     silently dropped purely due to spelling.
- **Fix (generic, deterministic, no fuzzy matching, no command-template
  substitution, no weakening of exact grounding):**
  1. NEW `TARGET_SPECIFIC_INTENTS` (moved from `request_execution_
     policy.py`'s own former private `_TARGET_SPECIFIC_INTENTS` into
     `request_contract.py` as one public, shared definition — consolidated,
     never duplicated) and `required_target_parameter_gaps(intent,
     requested_output, provided_context)` — a small, deliberately closed,
     documented, extensible rule: when a target-specific request's own
     VERIFIED `provided_context` establishes a target TYPE (`unit_type`)
     that is itself one of the identifier-bearing classes (`RRU`/`AAS` —
     reuses the SAME small set the identifier normalizer defines, so a
     real governed `"SupportUnit"` branch, whose own content is "No
     restart" with nothing to identify, never triggers this rule), but no
     `unit_id` is present, the identifier is deterministically required
     regardless of the model's own claim.
  2. `reconcile_missing_context(intent, requested_output, verified_
     provided_context, model_declared_missing_context)` — merges the
     model's own still-genuinely-unsatisfied declared keys with `required_
     target_parameter_gaps`'s own deterministic additions; wired into
     `validate_and_persist_request_contract` so the DURABLY PERSISTED
     `missing_context` is always the reconciled value, never the model's
     raw claim.
  3. `derive_execution_decision` gained a defense-in-depth backstop
     (Section 7's own explicit requirement): independently re-derives
     `required_target_parameter_gaps` from `contract.provided_context`
     and ORs it into the missing-context check, so even a malformed/
     stale contract that somehow reached this function with an empty
     `missing_context` despite a genuinely unconfirmed target identifier
     still cannot fail open.
  4. NEW `extract_canonical_identifiers(text)` — deterministic, token/
     boundary-safe extraction of recognized unit identifiers (`RRU-5`,
     `AAS-3`) from raw text; no `re`, no fuzzy/semantic matching, no
     bare-numeric-alone inference (a number is matched ONLY when
     immediately, structurally adjacent to a recognized class prefix).
     `_verify_and_filter_provided_context` gained a THIRD verification
     path using this function (plus its own session-confirmed-aware
     variant) — "RRU 5" (user) now verifies a model-claimed "RRU-5"
     value and vice versa, storing the canonicalized form — while
     negative cases remain correctly rejected: "AAS 3" never verifies
     `RRU-3`; "RRU 15" never verifies `RRU-5` (exact whole-token digit
     extraction, never substring containment); a bare "5" never becomes
     `RRU-5`/`AAS-5` (it may still verify via the pre-existing, unmodified
     literal-substring path if literally present, but is never PROMOTED
     to a fabricated identifier).
- **Read-only Cloud SQL source validation (Section 13/14, performed for
  real, no mutation):** the REAL, complete `A5-VALIDATION-DOCUMENT1` /
  `v2-def0024-fix` "HW Partial Fault" section reads, in full: "Restart is
  allowed only on RRU. Command: accn FieldReplaceableUnit=RRU-9
  restartunit 1 1 1 • If: SupportUnit=--- → No restart • For AAS units:
  accn FieldReplaceableUnit=AAS-1 restartunit 1 1 1" — a flat, literal
  command string with NO placeholder/substitution language of any kind.
  Critically, the SAME document's own sibling sections for genuinely
  variable-unit scenarios ("HW Fault," "No Connection," "RET Failure,"
  "RET Not Calibrated") use a conspicuously DIFFERENT, explicit pattern:
  a real diagnostic lookup command (`hget near Rfportref`), an explicit
  "Example output" label, then "Restart the **identified** RRU" in prose
  — never a literal pre-filled unit number. The document's own author
  already had, and used elsewhere, the correct pattern for a genuinely
  parameterizable target; "HW Partial Fault"/"SW Error" do NOT use it.
  **PARAMETERIZATION_AUTHORITY: NOT_AUTHORIZED** for both `RRU-9` and
  `AAS-1` — both are literal, asset-specific identifiers as written, not
  established templates. Per this milestone's own explicit instruction,
  command-template substitution is NOT implemented, and command
  generation for a live-supplied `RRU-5`/`AAS-X` correctly continues to
  be withheld by DEF-0024/0027's own unmodified exact-verbatim grounding
  until the governed source itself is authored to support it (e.g. using
  the SAME dynamic-lookup + "identified unit" pattern already proven
  elsewhere in this same document).
- **Regression:** `backend/tests/test_6a14_request_parameter_consistency
  .py` (NEW — 33 tests covering required-gap detection [including the
  `SupportUnit`-never-requires-an-identifier non-regression finding made
  during this pass's own implementation], reconciliation rules A-F,
  execution-policy defense-in-depth, Knowledge-example non-satisfaction,
  identifier normalization positive/negative/boundary cases, canonical-
  value provenance preservation, same-subject continuation carry-forward
  via a real `validate_and_persist_request_contract` call, different-
  subject non-inheritance, stale-run_id rejection, and non-regression
  proof that exact command grounding is completely unchanged — RRU-9
  still grounds, RRU-5 still does not); DEF-0024 (17)/DEF-0026 (26)/
  DEF-0027 both passes (44)/6A.13 (45)/6A.14 (33+30+24)/DEF-0028 (30)/
  DEF-0029 (24) all re-run unmodified, all green. team_manager/chat_
  service/governed/knowledge/provenance/security/incident_manager-focused
  subset — see this pass's own closure report for exact counts. Full
  backend regression — see this pass's own closure report. No frontend
  file touched.
- **Fix commit SHA(s):** none yet — uncommitted; per explicit instruction,
  this corrective pass does not commit or push.
- **Related tests:** `backend/tests/test_6a14_request_parameter_
  consistency.py`.
- **Related docs:** DEF-0024/0026/0027/0028/0029 above (this pass's
  reconciliation/normalization layer is additive to all of them; none of
  their own grounding/continuity/execution-policy logic was weakened);
  `docs/AGENT_CONTRACT.md` §6a.
- **Notes:** deliberately does NOT implement command-template
  substitution (the read-only source validation above found the governed
  source does not currently authorize it), does NOT weaken exact-verbatim
  command grounding, does NOT introduce fuzzy/semantic identifier
  matching, does NOT change specialist routing, and does NOT implement
  DEF-0023. A future, SEPARATE milestone may safely revisit governed
  command-template + trusted-parameter-binding semantics ONLY once the
  governed Knowledge source itself is authored to explicitly support a
  parameterized target (following the SAME dynamic-lookup pattern this
  document's own author already uses for "HW Fault"/"No Connection"/
  etc.), never before. Live browser UI acceptance remains the final gate
  before this entry may be marked fully CLOSED.

===================================================================
DEF-0031 — Deterministic completion-boundary corrections to `final_text`
are visible in the live SSE stream but never persisted; refreshed/
reopened history reconstructs from the raw, pre-correction ADK event
===================================================================

- **Status:** FIXED (implemented + regression-tested; live browser
  acceptance still open — see 6A.14A's own closure report).
- **Severity:** HIGH — this is the trust boundary EVERY deterministic
  safety correction in this codebase ultimately depends on
  (`command_suppression_fallback_text`, `enforce_execution_decision_on_
  guidance`, `enforce_governed_knowledge_at_completion`'s remediation,
  the `KNOWLEDGE_INVENTORY` override, DEF-0024/0026/0027/0028/0029/0030's
  own corrected text) — if the correction never survives a refresh, the
  live-only guarantee these defects were fixed to provide is illusory
  for any user who reloads the page or reopens a saved chat.
- **Detected during:** documentation-milestone code audit (audit-only),
  triggered by a real, reported live reproduction: user sent `hello`;
  the LIVE response was a deterministic unresolved-target fallback
  (`chat_service: unresolved target context with no structured
  troubleshooting_guidance to enforce against -- replacing free-form
  response deterministically`); the REFRESHED response was a
  materially different `"Hello! How can I help you today?"`.
- **Affected capability:** `backend/api/chat_service.py`'s own
  completion-boundary corrections (line ~2148-2152 and every other
  `final_text = ...` override in the same method) vs. `backend/api/
  session_history_service.py`'s history projection.
- **Root cause, confirmed by direct code trace:**
  1. `chat_service.py` computes `final_text` as a LOCAL Python variable
     across the live turn, and may OVERWRITE it after the ADK `Runner`
     has already finished — via `command_suppression_fallback_text`,
     `enforce_execution_decision_on_guidance`, the governed-knowledge
     completion remediation, or the `KNOWLEDGE_INVENTORY` override.
  2. This corrected `final_text` is used to build `message_completed_
     data["content"]`, sent in the live `MESSAGE_COMPLETED` SSE event —
     proven correct for the LIVE path.
  3. **Nothing writes the corrected `final_text` back into ADK's own
     persisted event stream.** ADK's `DatabaseSessionService.append_
     event()` already durably persisted its OWN event (containing the
     model's/ADK's own ORIGINAL, uncorrected text) during the live
     `Runner.run_async` call, BEFORE chat_service.py's own post-loop
     correction logic ever runs.
  4. `backend/api/session_history_service.py` (confirmed by direct
     source read) imports and reuses `chat_service.py`'s own `_active_
     events`/`_extract_final_text`/`_non_thought_text` VERBATIM to
     reconstruct history — `_extract_final_text(event)` reads `event
     .content.parts`, i.e. the RAW, ADK-persisted event content. There
     is no code path anywhere that substitutes the corrected value.
  5. Consequence: ANY turn where a deterministic completion-boundary
     correction fired shows the CORRECTED text live, and the ORIGINAL,
     UNCORRECTED text after a refresh, backend restart, or reopening a
     saved chat — silently, with no error, no warning, no visible
     inconsistency to the user other than a genuinely different answer.
- **Trust boundary this defect undermines:** every deterministic safety
  correction in this codebase (DEF-0024 through DEF-0030) implicitly
  assumed "the corrected text is THE answer" — this defect proves that
  assumption false for the persisted/history-reconstructed path
  specifically, even though it holds for the live path.
- **Fix (6A.14A — Canonical Turn Result & Projection):**
  `backend/api/turn_source_references.py` (already the durable, ADK-
  session-state-backed, rewind-correct per-turn provenance store since
  B7) was widened, additively, to also carry the turn's own already-
  fully-corrected `final_text` in the SAME `{turn_id: {...}}` entry —
  ONE canonical object per turn, never a second, independently-written
  structure that could drift from the first (`CanonicalTurnResult`,
  `build_turn_source_references_delta(..., final_text=...)`,
  `resolve_canonical_turn_result`). `chat_service.py`'s own existing
  end-of-turn persistence call site (already writing provenance BEFORE
  `MESSAGE_COMPLETED`) now includes `final_text` in that SAME write,
  computed AFTER every existing deterministic correction (RequestContract
  execution policy, the `KNOWLEDGE_INVENTORY` override, `Troubleshooting
  Guidance` enforcement, the unresolved-target backstop) has already run
  — PERSIST BEFORE ANNOUNCE: a failure or a same-turn conflict
  (`CanonicalTurnResultConflictError`, a belt-and-suspenders idempotency/
  conflict guard) now fails the turn closed with a proper `ERROR`/
  `RUN_COMPLETED(outcome=error)` — `MESSAGE_COMPLETED` is NEVER emitted
  for a turn whose canonical result did not durably persist. A best-
  effort failure marker (`build_turn_failure_marker_delta`/`is_turn_
  marked_failed`) additionally prevents the ADK Runner's own already-
  durably-appended RAW event from being resurrected by history's legacy
  fallback in that specific failure case. `session_history_service.py`'s
  `get_session_history` now reads the canonical result first, falling
  back to the exact pre-6A.14A raw-ADK-event behavior only for a
  genuinely legacy (pre-6A.14A) turn that has neither a canonical result
  nor a failure marker. Rewind requires zero new code — ADK's own,
  already-proven full-accumulated-dict state-delta reversal (this
  module's own top docstring) already removes a discarded turn's
  canonical entry for free.
- **Real defect found and fixed during this milestone's own regression
  runs (test-fixture only, not a runtime defect, two passes):**
  `backend/tests/_api_fakes.py`'s `FakeRunner` (used by ~170 pre-existing
  tests, across both its default `respond`-only path and its explicit
  `events=[...]` path) reused ONE shared, fixed `invocation_id` default
  for every event neither path's caller explicitly overrode, regardless
  of how many turns ran against the same session — something real ADK
  never does (a fresh id every turn, per that same module's own
  pre-existing docstring). 6A.14A's new same-turn-conflict guard is the
  first thing in this codebase to actually depend on that real ADK
  guarantee, and correctly caught the fixture's inaccuracy: a first full-
  suite run found it in 7 pre-existing tests using the default path
  (`test_api_chat_service.py`/`test_api_streaming_endpoint.py`/
  `test_chat_service_saved_chat_marker.py`/`test_session_history_service
  .py`, plus one incidentally in the explicit path); after fixing the
  default path, a second full-suite run found the SAME root cause in 7
  MORE tests using the explicit `events=[...]` path exclusively
  (`test_conversation_target_regression.py` ×6, `test_p3_event_author_
  hygiene.py` ×1) — both explicitly construct two separate `FakeRunner`
  turns against the same session with different scripted final text,
  never overriding `invocation_id`. Fixed by generating one genuinely
  unique `invocation_id` per `run_async` CALL in BOTH code paths — for
  the explicit-list path, any yielded event still carrying the sentinel
  default is stamped with that call's id before being yielded, in place,
  so every event within one turn still shares one consistent id exactly
  like a real turn's own events do; an event whose author set a
  DIFFERENT, deliberate `invocation_id` (the two tests that assert a
  specific literal value, verified to construct real ADK `Event` objects
  directly rather than going through `FakeRunner` at all) is completely
  unaffected. A fixture-accuracy correction, not a weakening of the new
  safety check.
- **Tests:** `backend/tests/test_p6a14a_canonical_turn_result.py` (18 —
  real end-to-end via the REAL `team_manager` agent object/a real file-
  backed `DatabaseSessionService`/real `ChatService`: exact live/
  refreshed/restarted text equivalence for a deterministically-replaced
  response — the literal DEF-0031 reproduction via the `KNOWLEDGE_
  INVENTORY` override — an ordinary unmodified response, rewind removing
  the canonical result, a forced persistence failure proving no
  `MESSAGE_COMPLETED`/no contradictory resurrected history, and Teams+KM
  provenance staying consistent across live/history; plus unit tests for
  build/resolve round-tripping, legacy-turn fallback, conflict/
  idempotency, session isolation, and the failure marker).
- **Regression:** full backend suite (4175 collected) — first full run
  after implementation: 4131 passed, 36 skipped, 8 failed (7 fixture-
  related as described above, 1 pre-existing real-Vertex-AI-output-
  variance flake in `troubleshooting_manager/test_real_model_validation
  .py::test_real_model_empty_experience_still_works`, confirmed by direct
  standalone re-run to pass — same documented flakiness class as this
  codebase's own 6A.9/6A.10 closure history, unrelated to this pass, zero
  files it depends on touched). Final full run, after BOTH fixture fixes:
  **4138 passed, 36 skipped, 1 failed, in 7m27s** — the one failure
  (`test_real_model_never_authorizes_action_governed_evidence_prohibits`,
  a DIFFERENT specific real-model test, exactly this flakiness class's
  own established "a different test fails each run" signature) confirmed
  by direct standalone re-run to pass (a real, successful network call).
  Zero fixture-related or 6A.14A-related failures remain across the
  entire suite. No frontend file touched (`git diff --stat -- src`
  empty).
- **Related docs:** `docs/MASTER_ROADMAP.md` §7a (6A.14A), `docs/
  INTELLIGENCE_ARCHITECTURE.md` §20a/§20b (Canonical Turn Result
  Fan-out), `docs/AGENT_CONTRACT.md` §6b.
- **Notes:** live browser acceptance (real backend restart, real hard
  refresh, real rewind through the real UI) was NOT performed in this
  pass — no interactive browser tool was available in this session; see
  6A.14A's own closure report for the exact PowerShell validation
  procedure. DEF-0031 is CODE-FIXED and regression-tested; do not mark it
  CLOSED until that live acceptance pass is genuinely performed.

**6A.14A HARDENING PASS (two residual weaknesses closed, both found by
the user's own review of the first-pass closure report, before live
acceptance):**

1. **Fail-open history gap.** The first pass's own per-turn best-effort
   failure marker (`build_turn_failure_marker_delta`) could not, by
   itself, distinguish "canonical persistence failed AND the failure-
   marker write also failed" (nothing persisted under this turn's own
   key at all) from "this turn predates 6A.14A entirely" (also nothing
   persisted) — both looked identical to the old "absence means legacy"
   rule. Closed with a NEW, POSITIVE, durable, SESSION-level marker
   (`CANONICAL_RESULT_ENFORCEMENT_STATE_KEY`), established by `chat_
   service.py` BEFORE any Runner call for a turn even begins (never
   during -- writing state while the Runner's own generator is still
   active is the ALREADY-DOCUMENTED-UNSAFE B4B pattern this codebase's
   own `_finalize_user_turn_activity` docstring records) and consulted
   by a NEW single classification function, `resolve_canonical_turn_
   state`, using real, durable event ORDER (never a timestamp, never
   process-local memory) to positively distinguish a genuinely legacy
   turn from a canonical-required one. If the marker establishment write
   ITSELF fails, the turn now fails closed BEFORE any specialist/model
   execution of any kind -- structurally, no raw assistant final-
   response event can ever become eligible for that attempt, closing the
   gap at its root rather than relying on a second best-effort write
   after the fact.
2. **Incomplete conflict detection.** The first pass's own `CanonicalTurn
   ResultConflictError` compared only `final_text`. `build_turn_source_
   references_delta` now compares the COMPLETE normalized canonical
   payload (`schema_version`, `final_text`, `source`, `knowledge_
   sources`, `visual_evidence_internal`) -- a changed/added/removed Teams
   source, a changed/added/removed Knowledge source (including a changed
   `knowledge_id`/`version_label`/`section_id`), a schema-version change,
   or a changed visual-evidence binding now all correctly conflict; only
   an EXACT repeated payload is idempotent.

Also added: a mixed success/failure contradictory-state check
(`CanonicalTurnStatus.CONFLICTING`) and a malformed-entry check
(`CanonicalTurnStatus.MALFORMED`), both failing closed and logged safely
(session/turn identity only, never response text or provenance content).

**Tests:** `backend/tests/test_p6a14a_hardening_canonical_immutability
.py` (28 new — full-payload conflict/idempotency for text, Teams source,
and Knowledge provenance including version/section identity; explicit
source-order-is-authoritative proof; malformed/conflicting-state fail-
closed proofs; `_project_turns`' own event-order classification unit
tests; and 8 real end-to-end integration tests: double-persistence-
failure fail-closed with no raw-text resurrection, initial-marker-
failure blocking all specialist/model execution, a genuine legacy turn
still rendering, a mixed legacy+canonical-required session rendering
each turn correctly, restart-durability, and rewind correctly clearing a
discarded turn's own canonical result while leaving an earlier turn and
the session-level marker intact). The original 18 tests in `test_
p6a14a_canonical_turn_result.py` were left completely untouched and
continue to pass unmodified.

**Regression (hardening pass, full backend suite, 4203 collected):**
4165 passed, 36 skipped, 2 failed, in 15m55s. Both failures confirmed,
by direct standalone re-run, to be PRE-EXISTING and unrelated (empty
scoped `git diff` against both files): `test_r1_r3_correctness_
regression.py::test_full_ambiguous_to_resolved_flow_call_graph` (the
same order-dependent flakiness class documented across D1/D2/6A.4-6A.10)
and `troubleshooting_manager/test_6a11_pass2_stress_matrix.py::test_
knowledge_injection_treated_as_data_never_an_instruction` (a real Vertex
AI call, wording-sensitive assertion -- failed once and passed once on
re-run, purely on the model's own exact refusal phrasing; already a
recorded pre-existing failure in the earlier DEF-0027 final corrective
pass's own regression baseline). Zero hardening-pass-related failures
anywhere in the full suite.

**Status after hardening pass:** DEF-0031 remains CODE-FIXED and
regression-tested, now with the fail-open gap and conflict-comparison
weaknesses closed; still NOT CLOSED -- live browser acceptance remains
the outstanding requirement, unchanged by this pass.

===================================================================
DEF-0032 — Governed-Knowledge version identity is discarded by
6A.4 narrowing's own output, letting hybrid retrieval (6A.5) constrain
candidates by `knowledge_id` alone, never `(knowledge_id, version_label)`
===================================================================

- **Status:** OPEN.
- **Severity:** HIGH — a governance/provenance boundary defect: content
  from an INELIGIBLE (superseded, unapproved, or otherwise excluded)
  version of a governed document can structurally re-enter the
  retrieval candidate set merely because a DIFFERENT version of the
  SAME `knowledge_id` is currently permitted.
- **Detected during:** documentation-milestone code audit (audit-only).
- **Affected capability:** `backend/knowledge/narrowing/contracts.py`'s
  `KnowledgeNarrowingResult.permitted_knowledge_ids: list[str]` and
  every 6A.5 hybrid-retrieval SQL query that consumes it (`backend/
  knowledge/hybrid_retrieval/repository.py`'s `exact_match`/`lexical_
  search`/`semantic_search`).
- **Root cause, confirmed by direct code trace:** `backend/knowledge/
  narrowing/service.py`'s own internal eligibility computation DOES
  correctly key by the full `(knowledge_object.knowledge_id,
  knowledge_object.version.label)` identity — but the narrowing
  service's own OUTPUT contract, `KnowledgeNarrowingResult.permitted_
  knowledge_ids`, is typed `list[str]` (bare `knowledge_id` only,
  version identity discarded at the output boundary). Every hybrid-
  retrieval SQL query that enforces the 6A.4 candidate boundary
  (confirmed by direct read of `repository.py`) filters with `WHERE
  knowledge_id = ANY(:ids)` — `version_label` is stored per row in the
  evidence index (`backend/knowledge/hybrid_retrieval/repository.py`
  line 65) but NEVER checked by the candidate-boundary query itself.
- **Reproduction shape (not yet exercised as a live/test scenario by
  this audit-only pass):** a `knowledge_id` with two versions indexed
  (e.g. an old, superseded v1 and a new, approved v2) would let v1's own
  indexed rows remain retrievable via `permitted_knowledge_ids`
  correctly including that `knowledge_id` (because v2 is eligible) —
  the SQL boundary cannot distinguish which version's rows it is
  actually returning.
- **Required remediation (NOT implemented by this documentation-only
  pass):** `KnowledgeNarrowingResult` must carry the full `(knowledge_id,
  version_label)` identity through to the retrieval boundary — never
  bare `knowledge_id` — and every hybrid-retrieval query must filter on
  BOTH. Tracked in the widened **6A.16 — Version-Safe Hybrid Evidence
  Production & Reconciliation** (see `docs/MASTER_ROADMAP.md` §7a).
- **Required tests (not yet written):** a real multi-version fixture
  (same `knowledge_id`, two distinct `version_label`s indexed, only one
  currently eligible) proving the ineligible version's own rows are
  never returned by `exact_match`/`lexical_search`/`semantic_search`
  even though the `knowledge_id` itself is permitted.
- **Related docs:** `docs/MASTER_ROADMAP.md` §7a (6A.16).
- **Notes:** deliberately NOT fixed by this documentation-only pass.

===================================================================
DEF-0033 — Hybrid-retrieval embedding reconciliation is gated solely
on content-hash equality, never on whether a valid CURRENT-model
embedding actually exists — stale/missing vectors can persist silently
===================================================================

- **Status:** OPEN.
- **Severity:** MEDIUM/HIGH — a silent semantic-search correctness
  defect (never a crash, never a visible error) that can cause the
  dense-vector channel to search against a genuinely stale or
  permanently-absent embedding while nothing in the system's own output
  discloses this degradation to a caller relying on "hybrid" retrieval.
- **Detected during:** documentation-milestone code audit (audit-only),
  direct read of `backend/knowledge/hybrid_retrieval/indexing.py` and
  `repository.py`'s `upsert`.
- **Affected capability:** `index_knowledge_object`'s `to_embed`
  selection (`indexing.py`) and `EvidenceIndexRepository.upsert`'s
  UPDATE-path SQL (`repository.py`).
- **Root cause, confirmed by direct code trace (three compounding
  gaps, one shared root: freshness is judged by content-hash equality
  ALONE, never by whether the row's own `embedding` column is actually
  populated with a CURRENT-model vector):**
  1. **Failed-embedding retry never happens for unchanged content.**
     `to_embed = [r for r in records if existing_hashes.get(r.evidence_
     id) != r.content_hash]` — `repository.upsert` unconditionally
     writes the record's `content_hash` regardless of whether embedding
     succeeded (confirmed: `params["content_hash"] = record.content_
     hash` is set outside any `if embedding is not None` guard). A row
     whose embedding failed once, with unchanged content thereafter, is
     excluded from `to_embed` on EVERY subsequent run forever — no
     retry path exists once the transient failure that caused it has
     resolved.
  2. **Changed content + embedding failure leaves a stale vector.**
     `repository.upsert`'s UPDATE-path `set_clauses` (confirmed by
     direct read) only appends `embedding = ...` (and the model/
     version/dimensions/generated_at columns) INSIDE `if embedding is
     not None:` — when embedding is `None` on this run (failure), the
     UPDATE statement updates `indexable_text`/`content_hash` to the
     NEW content while leaving the OLD `embedding` column completely
     untouched, silently pairing NEW text with an OLD, semantically
     stale vector.
  3. **No embedding-model/version reconciliation trigger exists.** The
     `to_embed` selection is purely content-hash-based; nothing compares
     an existing row's `embedding_model`/`embedding_model_version`
     against the CURRENT `embedding_provider`'s own identity. A future
     embedding-model upgrade would silently leave every
     unchanged-content row on the OLD model's vectors indefinitely.
- **NOT a defect (confirmed correct, by design):** a failed embedding
  leaving a row lexical-only-searchable (never deleting the exact/
  lexical-capable row) is the module's own explicitly documented,
  correct degradation — see `indexing.py`'s own "EMBEDDING FAILURE
  HANDLING (§42)" docstring.
- **Required remediation (NOT implemented by this documentation-only
  pass):** track embedding freshness independently of content-hash
  (e.g. a boolean/timestamp "embedding attempted and failed" marker
  distinct from "never yet embedded"), always clear (never silently
  retain) a stale `embedding` on the SAME row whose `content_hash`
  changed even when the new embedding attempt fails, and add an
  explicit embedding-model/version reconciliation pass. Tracked in the
  widened **6A.16 — Version-Safe Hybrid Evidence Production &
  Reconciliation**.
- **Required tests (not yet written):** a failed-then-succeeding
  embedding retry on unchanged content; a changed-content-plus-failure
  case proving the stale vector is cleared, never retained; an
  embedding-model-version-bump reconciliation pass.
- **Related docs:** `docs/MASTER_ROADMAP.md` §7a (6A.16).
- **Notes:** deliberately NOT fixed by this documentation-only pass.

===================================================================
DEF-0034 — RequestContract provided_context verification is a pure
literal substring match with no negation/contrast awareness — a value
the user explicitly EXCLUDED can be trusted as CONFIRMED
===================================================================

- **Status:** OPEN.
- **Severity:** HIGH — same operational-safety class as DEF-0024/0027/
  0030: an explicitly-negated identifier/value can be trusted as live,
  USER-confirmed context, potentially authorizing an operational
  command/action against a target the user explicitly ruled out.
- **Detected during:** documentation-milestone code audit (audit-only),
  directly reproduced: `RequestParameter(name="vendor", value="Nokia",
  provenance=USER)` against raw current-turn text `"This is Ericsson
  equipment, not Nokia."` — `_verify_and_filter_provided_context`
  returned `vendor=Nokia` as VERIFIED, exactly backwards from the
  user's own stated meaning.
- **Affected capability:** `backend/agents/team_manager/request_
  contract.py`'s `_verify_and_filter_provided_context`.
- **Root cause, confirmed by direct code trace:** the substring-match
  verification path (`value_lower in current_lower`) is, by design
  (per this codebase's own repeated, deliberate "no NLP/keyword
  routing, no fuzzy matching" principle), a pure literal containment
  check with zero structural awareness of negation ("not X"), exclusion
  ("except X"), or contrastive structure ("X, not Y") — "Nokia" is a
  genuine literal substring of the negating sentence, so it passes.
- **Required remediation (NOT implemented by this documentation-only
  pass):** this is architecturally hard to close with a purely
  deterministic, non-NLP mechanism (the existing codebase convention);
  requires deliberate design — e.g. a small, explicit deny-adjacency
  check for a closed set of negation markers immediately preceding the
  matched value (bounded, auditable, never a general NLP negation
  detector) — attached to the Request Contract validation boundary.
  Does NOT change 6A.13's own historical COMPLETE status (per explicit
  instruction) — this is a corrective defect against that boundary, the
  same relationship DEF-0024/0026/0027 have to 6A.12.
- **Required tests (not yet written):** positive (plain statement),
  negative (explicit negation), ambiguous (genuinely unclear), and
  corrected (user restates immediately after) cases, all deterministic.
- **Related docs:** `docs/MASTER_ROADMAP.md` §7a (6A.13's own
  corrective-work note).
- **Notes:** deliberately NOT fixed by this documentation-only pass.

===================================================================
DEF-0035 — Teams write-tool execution ignores the gateway's own
application-level result payload; an HTTP-200 `{"success": false}`
response is reported to the user as a successfully executed write
===================================================================

- **Status:** OPEN.
- **Severity:** HIGH — directly in the approval/write trust boundary:
  a genuine Power Automate/Teams-side failure can be presented to the
  user as "message sent" when it was not.
- **Detected during:** documentation-milestone code audit (audit-only).
- **Affected capability:** `backend/tools/teams/execute_write.py`'s
  `teams_send_message` (and, by the same pattern, `teams_create_chat`'s
  own `_parse_create_chat_result` best-effort path).
- **Root cause, confirmed by direct code trace:** `backend/gateway/
  power_automate_client.py`'s `_call` (the shared entry point for BOTH
  read and write operations) only raises `SafeErrorException` for
  TRANSPORT-level failures (timeout, network error, HTTP status >= 400,
  unparseable JSON) — for any HTTP 200 with valid JSON, it logs
  `"ok"` and returns the RAW payload UNCONDITIONALLY, regardless of the
  payload's own application-level content. The existing `"success"`-
  field check (`_normalize_list_payload`, confirmed correct) is a
  SEPARATE, DOWNSTREAM helper invoked ONLY by list-shaped READ
  operations (`teams_list_chats`/`teams_get_messages`) — it is never
  applied to `send_message`'s own return value. `execute_write.py`'s
  `teams_send_message` (confirmed by direct read, line ~150) calls
  `client.send_message(...)` and DISCARDS its return value entirely —
  it is not even assigned to a variable — then unconditionally calls
  `consume_proposal(...)` and returns `{"status": "executed", ...}`.
  An HTTP 200 response body of `{"success": false, "error": "..."}`
  (the exact shape `_normalize_list_payload` already anticipates for
  reads) would therefore be reported to the user as a successful send.
- **Required remediation (NOT implemented by this documentation-only
  pass):** `send_message`/`create_chat` must inspect their own response
  payload for an application-level failure signal (reusing the SAME
  `"success"` field convention `_normalize_list_payload` already
  established, never a second, different convention) before
  `consume_proposal`/`"status": "executed"` is ever returned; an
  ambiguous/unrecognized response shape must resolve to an EXPLICIT
  unknown-outcome result, never silent success. Tracked in the widened
  **6A.22 — Approval, Idempotency & Durable Write-Outcome Safety** (see
  `docs/MASTER_ROADMAP.md` §7a).
- **Required tests (not yet written):** a mocked `{"success": false}`
  200-response proving `teams_send_message` does NOT call `consume_
  proposal` and does NOT return `"status": "executed"`; an ambiguous
  response shape resolving to an explicit unknown-outcome result.
- **Related docs:** `docs/MASTER_ROADMAP.md` §7a (6A.22).
- **Notes:** deliberately NOT fixed by this documentation-only pass.

===================================================================
DEF-0036 — Teams write-tool retries have no durable idempotency
identity — a timeout/retry of the SAME approved proposal generates a
brand-new `requestId` every attempt, creating duplicate-write risk
===================================================================

- **Status:** OPEN.
- **Severity:** HIGH — same trust boundary as DEF-0035; a legitimate
  retry after a CLIENT-side timeout (where the write may have genuinely
  succeeded server-side) can result in the same Teams message being
  sent twice, with nothing to deduplicate the two attempts.
- **Detected during:** documentation-milestone code audit (audit-only).
- **Affected capability:** `backend/gateway/power_automate_client.py`'s
  `_call`; `backend/tools/teams/execute_write.py`'s own "proposal stays
  approved/unconsumed on failure, enabling retry" design (confirmed
  correct in isolation, but never paired with a stable identity).
- **Root cause, confirmed by direct code trace:** `_call` builds
  `body["requestId"] = str(uuid.uuid4())` FRESHLY, inline, on every
  single invocation — there is no parameter, no caller-supplied value,
  and no linkage to the underlying `ActionProposal.proposal_id` (the
  one genuinely stable, durable identity a retry of "the same approved
  action" already has available). Combined with `execute_write.py`'s
  own correct-in-isolation design (an unconsumed proposal remains
  retriable after a transport failure), a second attempt of the exact
  same approved proposal — whether user-initiated or a future automatic
  retry — presents Power Automate/Teams with a COMPLETELY UNRELATED
  `requestId`, giving no downstream system any signal that this is a
  retry of a possibly-already-delivered message rather than a new one.
- **Required remediation (NOT implemented by this documentation-only
  pass):** derive `requestId` deterministically from the approved
  `ActionProposal.proposal_id` (or an equivalent durable, immutable
  per-approval identity) rather than a fresh random UUID per HTTP call,
  so a genuine retry of the same approved action is identifiable as
  such by any downstream idempotency layer. Tracked in the widened
  **6A.22 — Approval, Idempotency & Durable Write-Outcome Safety**.
- **Required tests (not yet written):** two `_call` invocations for the
  same `proposal_id` produce the same `requestId`; two invocations for
  genuinely different proposals produce different `requestId`s.
- **Related docs:** `docs/MASTER_ROADMAP.md` §7a (6A.22).
- **Notes:** deliberately NOT fixed by this documentation-only pass;
  closely related to but distinct from DEF-0035 (that one concerns
  TRUSTING the result, this one concerns SAFELY RETRYING the attempt).

===================================================================
CONSIDERED AND EXPLICITLY NOT REGISTERED
===================================================================

These were investigated during the milestones above and found NOT to
meet this register's bar for a confirmed defect. Recorded here
deliberately so nobody re-opens them later on the same original
suspicion.

- **B6 "TEAM-ORION message not retrievable" (Test C, first attempt).**
  An earlier B6 live-validation attempt could not find the expected
  Teams message. `CLAUDE.md` explicitly records: "the system correctly
  reported it could not find that fact rather than fabricating one;
  test setup was corrected and the clean rerun above passed. Not a B6
  defect." **Category: TEST SETUP ERROR**, not a defect — the system's
  own honest-failure behavior was actually correct.
- **B6/B7 "svg artifact" in source-reference chip presentation.**
  Observed during B6 live validation as a visible rendering artifact in
  source chips. Audited in B7: `SourceChip.tsx` renders a normal
  `lucide-react` icon (`aria-hidden`) followed by clean "Source ·
  {label}" text; its own test suite already asserts the clean
  accessible name. `CLAUDE.md`: "No code change was made for this item
  — treated as descriptive shorthand in the B6 live-validation
  narration, not a confirmed defect." **Category: NON-REPRODUCIBLE
  FINDING.**
- **A5 Test D wording-sensitivity (bare statement doesn't populate
  troubleshooting guidance).** A bare, non-question first turn did not
  populate `TroubleshootingGuidance` at all. `CLAUDE.md`: "a live
  wording-sensitivity worth recording honestly, but the underlying
  safety property held: nothing executable leaked." No fix was made;
  the safety invariant (never leak an unconfirmed command) held
  regardless. **Category: MISSING CAPABILITY / accepted limitation**,
  not a defect — no unsafe behavior occurred.
- **A5 Test F "differing timing values" (5 vs 10 minutes) across two
  applicable documents.** Both a 4G-only and a combined 4G/5G MOP
  legitimately applied to a 4G node under the deterministic
  applicability contract, and the live answer transparently reported
  both documents' differing values rather than fabricating a blended
  number. `CLAUDE.md`: "correct per the contract, though a future
  refinement could bias toward the more narrowly-applicable single-
  technology document." **Category: OUT-OF-SCOPE
  LIMITATION / potential future refinement**, not a defect — the
  observed behavior was contractually correct (never blending
  conflicting values).
- **5.X selection-continuation resume cannot reach rich-content tools.**
  Audited during the DEF-0009 fix and found to be a real, structural
  limitation of the frozen `read_continuation_execution.py` (its own
  synthesis-only agents cannot make a second tool call after resume),
  identical in kind to the direct-fast-path case DEF-0009 fixed — but
  explicitly out of scope for that fix ("do NOT extend the text fast
  path itself to orchestrate media retrieval in this milestone").
  **Category: OUT-OF-SCOPE LIMITATION**, deliberately deferred, not
  newly introduced and not fixed by any 5.X commit — a future milestone
  that gives the continuation-execution agents real tool-calling
  ability would need to address it.

===================================================================
DEF-0037 — Ordinary conversational INFORMATION requests with no named
subject are forced through the "no resolved subject/procedure" gate,
producing an operational-sounding clarification for plain conversation
(e.g. "hello")
===================================================================

- **Status:** FIXED (LIVE-CORR-2, code + regression-tested; live browser
  acceptance not separately re-performed — see the corrective-pass note
  below).
- **Severity:** HIGH — a core trust/UX invariant violation: the system
  demands the user name "which specific alarm or governed procedure"
  they mean in response to a plain greeting, actively degrading the
  ordinary conversational experience and undermining confidence in every
  OTHER deterministic clarification this codebase produces (a user who
  sees this fire on "hello" will not trust it when it fires for a
  genuinely unresolved operational request).
- **Live reproduction:** Session `32c5a4a5-4243-4139-904e-c6b49c54d66a`
  (LIVE-CORR-1 Scenario 1), real Cloud SQL DEV, inspected read-only via
  the Cloud SQL Auth Proxy. User: `hello`. The real model's own raw
  final response (event `355bda14-...`, `team_manager`, `partial=False`)
  was `"Hello! How can I help you today?"` — a correct, safe answer.
  The turn's `validated_request_contract` (event `77166268-...`):
  `{"intent": "information", "subject": null, "requested_output": "fact",
  "missing_context": [], "ambiguity": false, "requires_governed_
  knowledge": false}`. The FINAL, persisted canonical result (event
  `e527332c-...`, `turn_source_references`) is
  `"I need to know which specific alarm or governed procedure you mean
  before I can give you a command. Please name it explicitly."` — a
  DIFFERENT, operationally-framed message that discarded the model's own
  correct answer.
- **Deterministic reproduction:** confirmed directly against the current
  uncommitted implementation —
  `derive_execution_decision(RequestContract(intent="information",
  requested_output="fact", subject=None, run_id="r", ...), "r")`
  returns `status=AMBIGUOUS, reason="no resolved subject/procedure for a
  command-shaped request"` — see
  `backend/tests/test_livecorr1_diagnostics.py::
  test_def_0037_information_intent_with_no_subject_is_forced_ambiguous`.
- **Confirmed root cause:** `backend/agents/team_manager/request_
  contract.py`'s `TARGET_SPECIFIC_INTENTS` (line ~300) includes
  `RequestIntent.INFORMATION` alongside `COMMAND`/`TROUBLESHOOTING`/
  `PROCEDURE`. `backend/agents/team_manager/request_execution_policy
  .py`'s `derive_execution_decision` (line ~223) unconditionally treats
  ANY `TARGET_SPECIFIC_INTENTS` member with `subject=None` as `AMBIGUOUS`
  with the fixed reason "no resolved subject/procedure for a command-
  shaped request" — a reason that is simply FALSE for an ordinary
  conversational INFORMATION request that never needed a procedure/
  subject at all. Because no `troubleshooting_guidance` exists for a
  plain conversational answer, `requires_unstructured_response_backstop`
  (same file, `_UNSTRUCTURED_RESPONSE_BLOCKING_STATUSES` includes
  `AMBIGUOUS`) then unconditionally discards the model's own correct
  free-form answer and substitutes `command_suppression_fallback_text`'s
  `_NO_SUBJECT_FALLBACK_TEXT`.
- **Affected trust boundary:** Request Contract / Deterministic
  Execution Policy (6A.13/6A.14) — the SAME boundary DEF-0028/0029/0030
  hardened for command safety now actively harms ordinary, safe
  conversation.
- **Code locations:** `backend/agents/team_manager/request_contract.py`
  (`TARGET_SPECIFIC_INTENTS`), `backend/agents/team_manager/request_
  execution_policy.py` (`derive_execution_decision`'s `not contract
  .subject` branch, `requires_unstructured_response_backstop`).
- **Relationship to existing DEF entries:** extends the DEF-0028/0029/
  0030 lineage (6A.14/6A.14 corrective passes) — those passes widened
  `TARGET_SPECIFIC_INTENTS`/the backstop specifically to close command-
  leak defects; this is the FIRST live evidence that the widening itself
  introduced a new, distinct over-blocking defect for the INFORMATION
  intent's own ordinary-conversation case. Not a duplicate of any open
  DEF-003x entry.
- **Required correction (NOT implemented by this diagnostic pass):**
  distinguish "an INFORMATION request that could plausibly resolve to an
  operational command/procedure fact" from "ordinary conversation that
  never needed a subject at all" — e.g. a positive signal (from
  `requested_output`, or a genuinely separate CONVERSATIONAL intent
  value) rather than blanket-including INFORMATION in `TARGET_SPECIFIC_
  INTENTS`. Must preserve the existing, correct invariant that a
  command-bearing INFORMATION-shaped answer still cannot bypass
  operational safety.
- **Required tests (written, xfail, by this pass):** `backend/tests/
  test_livecorr1_diagnostics.py::test_def_0037_information_intent_with_
  no_subject_is_forced_ambiguous` — asserts the CORRECT invariant
  (`status == ALLOW` for a subject-less, non-operational INFORMATION
  request) and is `xfail(strict=True)` against current behavior.
- **Live acceptance requirements:** a corrective pass must show "hello"
  (and other ordinary conversational openers) answered directly, with no
  clarification, while a genuinely operational INFORMATION-shaped
  request (e.g. "what is the VSWR threshold?") continues to be
  evaluated for target/condition safety exactly as today.
- **Explicit statement:** this diagnostic pass did NOT implement any
  fix — `request_contract.py`/`request_execution_policy.py` are
  unmodified by this pass (confirmed via `git diff`).

**LIVE-CORR-2 CORRECTIVE PASS — FIXED.** `RequestIntent.INFORMATION` was
removed from `TARGET_SPECIFIC_INTENTS` (`request_contract.py`). A new,
shared, two-factor function, `is_operationally_shaped_request(intent,
requested_output)`, replaces every direct use of `TARGET_SPECIFIC_
INTENTS` in both the "no resolved subject/procedure" gate and the
missing-context/target-gap gate (`derive_execution_decision`,
`required_target_parameter_gaps`) — TRUE whenever EITHER `intent` is
genuinely target-specific (COMMAND/TROUBLESHOOTING/PROCEDURE) OR
`requested_output` is itself one of the operational answer shapes
(EXACT_COMMAND/PROCEDURE_STEPS/TROUBLESHOOTING_NEXT_STEP) — a deliberate
OR, not an AND, so `intent=information, requested_output=exact_command`
and `intent=command, requested_output=fact` both remain restrictively
gated (instruction section 5's own required examples), closing the
over-blocking case without reopening a bypass. **A genuine regression
was found and fixed during this pass' own regression run** (not by
LIVE-CORR-1): the missing-context/target-gap gate initially used `is_
operationally_shaped_request` alone, which would have silently ignored
an ALREADY-DECLARED, non-empty `contract.missing_context` for a request
the model itself classified `intent=information, requested_output=fact`
but which nonetheless carried a real, command-bearing
`TroubleshootingGuidance` (the exact ROOT CAUSE B shape DEF-0028's own
corrective pass exists to prevent) — caught by the pre-existing test
`test_hw_partial_fault_safety_holds_for_information_intent_with_
embedded_command_in_guidance`. Fixed by widening that ONE gate's own
condition to `is_operationally_shaped_request(...) or contract.missing_
context` (a strict OR) — the subject-required gate is intentionally NOT
widened the same way, since an empty `missing_context` with no subject
for a non-operationally-shaped request is exactly the safe "hello" case.
Tests: `backend/tests/test_livecorr1_diagnostics.py::test_def_0037_
information_intent_with_no_subject_is_forced_ambiguous` (`xfail` marker
REMOVED, now PASSES); `backend/tests/test_livecorr2_request_context_
policy_correction.py` (12 new tests: 5 ordinary-conversation-openers,
3 missing-subject-operational-requests, 4 intent-label-cannot-bypass,
plus KNOWLEDGE_INVENTORY/ACTION non-regression and the widened-set
consistency proof). Full regression: 433 passed, 2 xfailed (DEF-0040/
DEF-0044, untouched) across every 6A.12/13/14/14A-focused suite; full
backend suite re-run clean (see this pass' own closure report for exact
counts). Live acceptance: NOT separately re-performed in this pass
(no browser access) — automated-test-verified only.

===================================================================
DEF-0038 — `required_target_parameter_gaps` is non-monotonic: knowing
LESS about a command's target can require FEWER confirmations than
knowing PART of it
===================================================================

- **Status:** PARTIALLY FIXED (LIVE-CORR-2 — a bounded, fail-closed
  interim foundation only; full step-aware precision remains a later
  milestone's scope — see the corrective-pass note below. Deliberately
  NOT marked FIXED, per this pass' own explicit "do not fake closure"
  instruction).
- **Severity:** HIGH — a safety-policy soundness defect: the deterministic
  gate meant to prevent an under-specified operational command from
  reaching the user can be satisfied more easily by providing NO
  information than by providing PARTIAL information, inverting the
  intended safety direction.
- **Live reproduction:** Session `38dbd231-a704-4734-9488-80db882a5b7e`
  (LIVE-CORR-1 Scenario 4, follow-up turn). User: "Give me the first
  approved command for that procedure." (no equipment identifier, no
  unit type, no condition supplied at all). `validated_request_contract`
  (event `b5cc12e5-...`): `{"intent": "procedure", "subject": "HW
  Partial Fault", "requested_output": "exact_command", "missing_
  context": [], "provided_context": []}` → resolved `ALLOW`, a command
  was emitted. This is CONSISTENT WITH, not identical to, the deeper
  mechanical defect below (the live turn never supplied a `unit_type`
  at all, so it took the "Case A" path directly — the code-level
  contrast against "Case B" was proven by direct reproduction, not
  observed as two live turns of the same conversation).
- **Deterministic reproduction (definitive):** run directly against the
  current uncommitted implementation —
  ```python
  required_target_parameter_gaps("procedure", "exact_command", [])
  # -> [] (no context at all -> ALLOWED)
  required_target_parameter_gaps("procedure", "exact_command",
      [RequestParameter(name="unit_type", value="RRU", provenance=ParameterProvenance.USER)])
  # -> ["unit_id"] (partial context -> BLOCKED)
  ```
  See `backend/tests/test_livecorr1_diagnostics.py::
  test_def_0038_less_context_never_grants_more_permission`.
- **Confirmed root cause:** `backend/agents/team_manager/request_
  contract.py`'s `required_target_parameter_gaps` (line ~315) only
  requires `unit_id` when `unit_type` is EXPLICITLY known and identifier-
  bearing (`RRU`/`AAS`) — when `unit_type` is entirely absent from
  `provided_context`, the function returns `[]` unconditionally,
  treating "we know nothing about the target" as equivalent to "no
  target-specific safety concern applies," the opposite of the intended
  fail-closed direction.
- **Affected trust boundary:** Deterministic Execution Policy (6A.14),
  the SAME function DEF-0030's own corrective pass introduced to close
  the RRU-9-template-leak defect.
- **Code locations:** `backend/agents/team_manager/request_contract.py`
  `required_target_parameter_gaps` (line ~315-352).
- **Relationship to existing DEF entries:** a distinct, newly-discovered
  gap in the SAME function DEF-0030 introduced; not a duplicate of
  DEF-0030 (which addressed the opposite direction — a KNOWN identifier-
  bearing type with no ID) or DEF-0038's sibling DEF-0037.
- **Required correction (NOT implemented by this diagnostic pass):**
  make the gate monotonic — the correct invariant is "supplying less
  target/condition context must never grant more operational permission
  than supplying partially complete context." This likely requires a
  positive, evidence-derived signal for "does the ACTIVE governed
  procedure/branch have target-dependent commands at all" (available via
  the selected evidence/active-procedure mechanism DEF-0024/0027 already
  maintain) rather than gating solely on whether the MODEL happened to
  declare a `unit_type` parameter.
- **Required tests (written, xfail, by this pass):**
  `test_def_0038_less_context_never_grants_more_permission` — asserts
  `set(gaps_with_no_context) >= set(gaps_with_partial_context)`.
- **Live acceptance requirements:** a corrective pass must show that a
  target-dependent procedure's command is equally withheld regardless of
  whether the user supplies zero information or partial information
  about the target, until the SAME level of confirmation is reached
  either way.
- **Explicit statement:** this diagnostic pass did NOT implement any
  fix — `request_contract.py` is unmodified by this pass.

**LIVE-CORR-2 CORRECTIVE PASS — PARTIALLY FIXED (bounded interim
foundation, remains PARTIALLY FIXED / OPEN at the step-aware precision
layer).** Audited first, per this pass' own explicit instruction: this
policy has NO deterministic signal, within its own strict scope, for
whether the SPECIFIC governed operation selected this turn genuinely
requires a target at all (that would require coupling this policy to
Knowledge-evidence selection state — explicitly DEF-0040/6A.20 scope,
out of bounds here). Rather than guess, `required_target_parameter_gaps`
now treats "`unit_type` never even stated" AT LEAST as restrictively as
"`unit_type` stated but `unit_id` missing" — but ONLY for `RequestedOutput
.EXACT_COMMAND` (the confirmed live defect shape, and the one output
shape that can carry a raw, ready-to-run command string): when `unit_
type` is entirely absent AND `requested_output == EXACT_COMMAND`, the
function now returns `["unit_id", "unit_type"]` (both, for an honest,
complete clarification) instead of `[]`. `PROCEDURE_STEPS`/
`TROUBLESHOOTING_NEXT_STEP` are deliberately UNCHANGED (still `[]` when
`unit_type` is absent) — they may legitimately describe a procedure's
branches conceptually without committing to one target yet (non-
regression: `test_procedure_can_still_describe_branches_when_fully_
resolved`, unmodified). A confirmed `unit_type` OUTSIDE the identifier-
bearing class (e.g. the real governed "SupportUnit" branch) remains
correctly gap-free — a real, verified fact, not an unresolved gap.
Proven: `set(gaps_with_no_context) >= set(gaps_with_partial_context) >=
set(gaps_with_complete_context) == set()`. Tests: `backend/tests/test_
livecorr1_diagnostics.py::test_def_0038_less_context_never_grants_more_
permission` (`xfail` marker REMOVED, now PASSES); `backend/tests/test_
livecorr2_request_context_policy_correction.py` (9 new tests: no/
partial/complete-context gap proofs, confirmed-non-identifier-type
proof, the explicit three-level monotonic-ordering proof, the bounded-
scope non-overblocking proof for PROCEDURE_STEPS/TROUBLESHOOTING_NEXT_
STEP, the non-operational-request-never-gains-a-gap proof, and an
explicit contract test pinning `TARGET_SPECIFIC_INTENTS`'s own corrected,
INFORMATION-excluded membership). **Remaining limitation, honestly
recorded, not closed by this pass:** a future milestone with access to
the selected governed operation's own target-cardinality classification
(whether it is genuinely target-independent, e.g. a read-only discovery
lookup) is required before this can be marked FIXED rather than
PARTIALLY FIXED — until then, an `EXACT_COMMAND` request for a
genuinely target-independent operation with no target info at all will
be (safely, but not perfectly precisely) over-blocked rather than
allowed. Live acceptance: NOT separately re-performed in this pass.

**LIVE-CORR-3B — Operational Authority Boundary CORRECTIVE PASS — STILL
PARTIALLY FIXED (a second, independent gap closed; step-aware precision
remains the same, still-open limitation).** A SEPARATE gap in the SAME
function, found during a fresh audit (section 4's own explicit "do not
limit target safety to RRU/AAS; an unknown or other unit type must not
become permissive" requirement): `required_target_parameter_gaps`
treated EVERY `unit_type` value OUTSIDE the small `RRU`/`AAS`
identifier-bearing class as equally verified-safe — a model that declared
ANY other string at all (a typo, a hallucinated label, or a genuinely
unrecognized equipment family), not only the real, DEF-0030-verified
`"SupportUnit"` case, silently satisfied the gate with zero governed
proof. Fixed by introducing a small, closed, documented
`_TARGET_INDEPENDENT_UNIT_TYPES` allowlist (currently exactly
`{"SUPPORTUNIT"}`, the same real, Cloud SQL-verified fact DEF-0030
already established) — only a `unit_type` in this allowlist is gap-free;
every other value, known or unknown, now conservatively requires
`unit_id`. Separately, this pass also removed the LIVE-CORR-3A
`TroubleshootingOperationalEffect.DIAGNOSTIC_READ` target-independent
permission bypass (`request_execution_policy.py`'s
`enforce_execution_decision_on_guidance`) outright — a model-mislabeled
REFERENCE_DESCRIPTION/OBSERVATION/DIAGNOSTIC_READ command no longer
escapes target confirmation. Remaining limitation, unchanged from the
LIVE-CORR-2 pass above: this policy still has no deterministic signal for
whether the SPECIFIC governed operation selected this turn is genuinely
target-independent (would require coupling to Knowledge-evidence
selection state) — DEF-0038 therefore remains PARTIALLY FIXED, not FIXED,
at that precision layer. Tests: `backend/tests/
test_livecorr3b_operational_authority_boundary.py` (unknown-unit-type
gap proof, SupportUnit-allowlist non-regression, DIAGNOSTIC_READ-no-
longer-bypasses proofs).

===================================================================
DEF-0039 — Stale, pre-override Knowledge source chips are attached to a
final answer that no longer depends on them (KNOWLEDGE_INVENTORY
unsupported-capability override)
===================================================================

- **Status:** FIXED (LIVE-CORR-2, code + regression-tested; live browser
  acceptance not separately re-performed — see the corrective-pass note
  below).
- **Severity:** MEDIUM — a provenance-fidelity defect (never a command-
  safety defect): displayed citations misrepresent what evidence
  actually supports the final canonical answer, undermining the "Source"
  UI's own credibility, though it does not itself expose ungoverned
  content or an unsafe command.
- **Live reproduction:** Session `abe35bdc-e974-4fc7-a287-6733fd736faf`
  (LIVE-CORR-1 Scenario 2). User: "What MOPs or governed Knowledge
  documents do you currently have available?" `validated_request_
  contract`: `{"intent": "knowledge_inventory", "requires_governed_
  knowledge": true}` → `derive_execution_decision` correctly returns
  `UNSUPPORTED_CAPABILITY`, and `chat_service.py` correctly overrides
  `final_text` to `KNOWLEDGE_INVENTORY_UNSUPPORTED_TEXT` ("I don't yet
  have a way to enumerate..."). BUT the SAME turn's persisted canonical
  result (event `3625ed66-...`) carries a non-empty `knowledge_sources`
  list — 5 real, distinct selected evidence items across 4 documents
  (Aurora Relay, Document1, Rogers 4G, Rogers 4G5G ×2) — the model's own
  REAL `knowledge_search`/`knowledge_select_evidence` calls, made BEFORE
  the deterministic override fired, per its own (also overridden) raw
  answer that listed exactly those 4 documents by name. The live UI
  displayed all 4 as source chips beside a message that explicitly
  disclaims the ability to enumerate documents — the displayed sources
  do not support the displayed text at all.
- **Confirmed root cause:** `backend/api/chat_service.py` (line ~2130):
  `if execution_decision.status == UNSUPPORTED_CAPABILITY: final_text =
  KNOWLEDGE_INVENTORY_UNSUPPORTED_TEXT` reassigns ONLY `final_text`.
  `selected_knowledge_evidence`/`knowledge_sources` are built later
  (line ~2359, `dedupe_knowledge_source_references(build_knowledge_
  source_references(selected_knowledge_evidence))`) from the SAME
  run-scoped store regardless of the override, and are unconditionally
  attached to `message_completed_data`/the canonical result.
- **Affected trust boundary:** Deterministic Execution Policy (6A.14) ×
  Knowledge provenance (5.1H/B7) intersection — neither boundary alone
  owns "does displayed provenance still support the FINAL text after a
  later override."
- **Code locations:** `backend/api/chat_service.py` lines ~2120-2131
  (the override) and ~2355-2365 (`knowledge_sources` construction) —
  the same method, ordering issue only.
- **Relationship to existing DEF entries:** distinct from DEF-0031
  (canonical text/history divergence, CLOSED at the code level by
  6A.14A) — this is a text/provenance CONSISTENCY defect WITHIN one
  already-canonicalized result, not a live-vs-persisted divergence.
- **Required correction (NOT implemented by this diagnostic pass):**
  clear `selected_knowledge_evidence`/omit `knowledge_sources` from
  `message_completed_data` whenever `execution_decision.status ==
  UNSUPPORTED_CAPABILITY` (mirroring the SAME override's own text
  replacement) — or, more generally, ensure every deterministic
  completion-boundary text override also reconciles which provenance it
  is still honest to display.
- **Required tests:** requires a real KM search/select fixture to
  reproduce deterministically; an integration-level xfail test is
  included in this pass (`test_def_0039_knowledge_inventory_override_
  clears_stale_sources`) using an isolated local KM repository, no real
  Gemini/network call.
- **Live acceptance requirements:** a corrective pass must show the
  KNOWLEDGE_INVENTORY fallback message with NO source chips attached.
- **Explicit statement:** this diagnostic pass did NOT implement any
  fix — `chat_service.py` is unmodified by this pass.

**LIVE-CORR-2 CORRECTIVE PASS — FIXED.** `chat_service.py`'s
`UNSUPPORTED_CAPABILITY` override now ALSO clears `selected_knowledge_
evidence = []` in the same place `final_text` is overridden. Because
every later consumer of this turn — the live `knowledge_sources` SSE
field, the persisted canonical result (built from the SAME `knowledge_
sources`), AND the end-of-turn `LAST_SELECTED_GOVERNED_EVIDENCE_STATE_
KEY`/`ACTIVE_GOVERNED_PROCEDURE_STATE_KEY` continuity-anchor writes —
all read from this SAME variable further down the method, this single,
minimal fix achieves all four requirements at once: no misleading
`knowledge_sources` in the live SSE event; none in the persisted
canonical result (so refreshed history matches); no new, stale
continuity anchor is created from the discarded inventory-turn evidence
(`build_last_selected_governed_evidence_state_update([])`/`compute_
fresh_active_procedure_anchor([], ...)` both already treat an empty list
as "no change," their own pre-existing, unmodified contract); AND a
genuinely PRIOR turn's own real continuity anchor is correctly left
completely untouched (proven by a dedicated two-turn integration test).
Internal retrieval/selection activity already emitted via this turn's
own activity-channel events during the actual tool calls is unaffected
— only this end-of-turn, user-facing/persisted variable is cleared.
Tests: `backend/tests/test_livecorr1_diagnostics.py::test_def_0039_
knowledge_inventory_override_clears_stale_sources` (`xfail` marker
REMOVED, now PASSES); `backend/tests/test_livecorr2_request_context_
policy_correction.py` (2 new integration tests: `test_inventory_turn_
does_not_create_a_continuity_anchor_from_discarded_evidence` and
`test_inventory_turn_never_overwrites_a_genuinely_prior_continuity_
anchor` — the latter drives two REAL, sequential turns through the real
`ChatService`/`ApiSessionService` pipeline, proving a real prior
"HW Partial Fault" anchor survives an intervening inventory turn byte-
for-byte). Live acceptance: NOT separately re-performed in this pass.

===================================================================
DEF-0040 — A governed command or an operational recommendation embedded
directly in `TroubleshootingGuidance` free-text fields (`step.action`,
`next_action`) is structurally invisible to command-grounding
enforcement, which examines only the separate `command`/`step.command`
field
===================================================================

- **Status:** FIXED (LIVE-CORR-3, code + regression-tested; live browser
  acceptance not separately re-performed — see the corrective-pass note
  below). Fixed in TWO parts: (1) embedded-command-in-prose detection;
  (2) response-mode compatibility enforcement (the SECOND part also
  closes the "conditional branches presented as though all simultaneously
  executable" half of this defect's own confirmed shape).
- **Severity:** HIGH — this is a real, confirmed bypass of the entire
  DEF-0024/0026/0027/0028 verbatim-grounding/active-procedure lineage:
  a real, governed, template-bearing command reached the user with ZERO
  grounding validation, simply because the model wrote it inside the
  free-text `action`/`next_action` field instead of the dedicated
  `command` field the enforcement machinery actually inspects.
- **Live reproduction:** Session `38dbd231-a704-4734-9488-80db882a5b7e`
  (Scenario 4, turn 1). The real, persisted `incident_manager` function
  response's `troubleshooting_guidance.full_procedure_steps[8]` (step 9
  of 15) is: `{"action": "Restart the identified unit. If the identified
  unit is an RRU, use the command: accn FieldReplaceableUnit=RRU-X
  restartunit 1 1 1 (replace X with the identified RRU number). If the
  identified unit is an AAS, use the command: accn
  FieldReplaceableUnit=AAS-Y restartunit 1 1 1 (replace Y with the
  identified AAS number)."}` — note: NO separate `"command"` key at all;
  BOTH conditional commands (with unresolved template placeholders X/Y)
  are embedded literally in `action`. Session `38dbd231-...` (Scenario
  5, turn 1) similarly shows `troubleshooting_guidance.next_action =
  "Restart the affected radio (RRU)."` with `"command"` absent entirely
  — an unambiguous, state-changing operational recommendation carried
  entirely as free text.
- **Confirmed root cause:** `backend/agents/incident_manager/evidence
  .py`'s `enforce_procedure_scoped_command_grounding_with_reason` (line
  ~570) examines ONLY `guidance.command` (NEXT_STEP mode) or `step
  .command` for each `full_procedure_steps` entry (FULL_PROCEDURE mode)
  — `step.action`/`guidance.next_action`/`guidance.interpretation` are
  never inspected for an embedded command/instruction string, by
  explicit design (per this codebase's own repeated, deliberate "never
  build a text/regex command parser" instruction). This is a genuine,
  confirmed gap, not a misconfiguration: the ARCHITECTURE currently has
  no mechanism, deterministic or otherwise, preventing a model from
  writing real operational content into a field that is never validated.
- **Affected trust boundary:** the entire DEF-0024/0026/0027/0028
  command-safety lineage, and the "COMMAND TRUST AND PRESERVATION"
  prompt-level instruction it exists to back up deterministically.
- **Code locations:** `backend/agents/incident_manager/evidence.py`
  (`enforce_procedure_scoped_command_grounding_with_reason`,
  `_evaluate_command`); `backend/agents/incident_manager/schemas.py`
  (`TroubleshootingStep`/`TroubleshootingGuidance` field definitions —
  `action`/`next_action`/`interpretation` are unconstrained free text by
  contract).
- **Relationship to existing DEF entries:** the direct, previously-
  unforeseen successor to DEF-0024/0026/0027/0028 — those passes
  progressively narrowed and strengthened `command`/`step.command`
  validation but never addressed the free-text fields as an alternative
  channel for the SAME content.
- **Required correction (NOT implemented by this diagnostic pass):**
  per this codebase's own explicit, repeated "no text/regex command
  parser" constraint, the correct direction is most likely a PROMPT-
  level, then schema-level, requirement that any executable syntax MUST
  be expressed only via the dedicated `command`/`step.command` field
  (never embedded in prose), combined with a deterministic, non-regex
  structural check (e.g., rejecting/flagging a `TroubleshootingGuidance`
  whose `action`/`next_action` text is suspiciously long/contains known
  command-syntax markers is explicitly NOT the direction — a schema/
  contract-level fix, not a text-parsing one, must be designed in the
  corrective milestone).
- **Required tests (written, xfail, by this pass):**
  `test_def_0040_embedded_command_bypasses_grounding` — constructs a
  `TroubleshootingStep` with a real, ungrounded command embedded in
  `action` and no `command` field, asserts (xfail) that `enforce_
  procedure_scoped_command_grounding_with_reason` detects and neutralizes
  it.
- **Live acceptance requirements:** a corrective pass must show that NO
  governed command, in ANY form (structured or embedded in prose),
  reaches the user without the SAME grounding validation `command`
  already receives.
- **Explicit statement:** this diagnostic pass did NOT implement any
  fix — `evidence.py`/`schemas.py` are unmodified by this pass.

**LIVE-CORR-3 CORRECTIVE PASS — FIXED.** Two independent, deterministic
mechanisms, both extending the existing `TroubleshootingGuidance`/
grounding architecture rather than building a parallel one:

  (1) **Embedded-operational-content detection** — `evidence.py`'s new
  `_detect_embedded_operational_content`, wired into `enforce_procedure_
  scoped_command_grounding_with_reason` immediately after the existing
  `_guidance_scope_established` (DEF-0027 FINAL) check, as a WHOLE-
  GUIDANCE suppression (never a single-field strip) with a new typed
  reason, `CommandGroundingReason.EMBEDDED_OPERATIONAL_CONTENT`.
  DELIBERATELY NOT a text/regex command parser: the ONLY signal is a
  plain, deterministic, per-line VERBATIM SUBSTRING check — does any
  free-text field (`interpretation`/`next_action`/`evidence_requested`/
  a `TroubleshootingStep.action`) contain, as a literal substring, a real
  content line (above a 12-character structural length floor, filtering
  trivial short headings/labels) from ANY of this turn's own currently-
  selected governed sections — the SAME "verbatim occurrence in real,
  already-retrieved content" philosophy `_evaluate_command`'s own
  `command in active_content` check already uses for the `command`
  field, applied here to the fields that check cannot see. Checks EVERY
  selected section, not only the active one. A real, accepted false-
  positive risk exists (safe over-suppression, the same fail-closed
  direction this module already applies everywhere else) — never silent
  under-suppression.

  (2) **Response-mode compatibility enforcement** — `request_execution_
  policy.py`'s new `enforce_response_mode_compatibility`, wired into
  `chat_service.py`'s completion boundary immediately before the existing
  `enforce_execution_decision_on_guidance` call. The validated
  `RequestContract.requested_output` (via `execution_decision.requested_
  output`, the SAME already-freshness-checked value `derive_execution_
  decision` itself populated — never a second read of the raw contract,
  never phrase/keyword matching) is the sole authority for whether a
  `FULL_PROCEDURE`-shaped `TroubleshootingGuidance` may render as such:
  permitted ONLY when `requested_output` is POSITIVELY `PROCEDURE_STEPS`
  — a request validated as `TROUBLESHOOTING_NEXT_STEP`/`EXACT_COMMAND`
  (or a missing/stale/unresolved contract) discards the guidance entirely
  (never narrows/repackages "step 1" as "the next step", which could be
  semantically wrong), rendering a new, fixed, deterministic clarification
  (`FULL_PROCEDURE_NOT_PERMITTED_FALLBACK_TEXT`) instead. The OPPOSITE
  direction (guidance is `NEXT_STEP`-shaped but the contract asked for
  `PROCEDURE_STEPS`) is deliberately NOT narrowed — showing less than
  requested is a completeness question, never a safety one.

  **Scope deliberately narrowed from the full section-7/12 taxonomy this
  defect's own corrective milestone was invited to consider** (a 5-value
  response-mode enum, a 4-value operational-effect enum, model-declared
  per-step target-required/operational-effect fields): audited first and
  found that (a) most of the "response mode" taxonomy already maps onto
  the EXISTING, unmodified `RequestContract.requested_output` values, so
  no new enum was needed to satisfy section 9's own compatibility-matrix
  requirement; (b) a model-declared, per-step `target_required`/
  `operational_effect` field would require TRUSTING the model's own
  safety-relevant self-report (the exact "prompt instructions alone are
  not a sufficient control" pattern this whole codebase explicitly
  rejects) or DETERMINISTICALLY DERIVING it from the selected governed
  operation's own structure, which is out of this pass' own scope
  (DEF-0038's own "step-aware precision" gap, explicitly deferred there
  to "a later milestone with access to the selected governed operation's
  own target-cardinality classification"); section 12's own requirement
  #3 ("a target-independent diagnostic read may be emitted without a unit
  ID") was found, on audit, to ALREADY be satisfied by the existing,
  unmodified DEF-0038 bounded rule (its stricter unit-type/unit-id-
  required-when-absent rule applies ONLY to `EXACT_COMMAND`, never
  `TROUBLESHOOTING_NEXT_STEP`, so a genuine diagnostic-read NEXT_STEP
  response was never blocked by it in the first place) — closing that
  requirement without new code. Section 13's own "audit whether token-
  overlap grounding can accept paraphrased/composite/substituted
  commands" was also audited and found ALREADY CORRECT: token overlap
  (`_classify_absence_or_rejection`) is used ONLY to select explanatory
  WORDING, never to ACCEPT a command — actual acceptance remains the
  existing exact-substring `command in active_content` check (DEF-0027's
  own tests already prove paraphrases/composites are rejected).

Tests: `backend/tests/test_livecorr1_diagnostics.py::test_def_0040_
embedded_command_bypasses_grounding` (`xfail` marker REMOVED, now
PASSES, rewritten with a fixture faithfully matching the live shape — a
REAL, grounded command duplicated into `action`, not an ungrounded one,
since the detection mechanism specifically catches real-content
duplication); `backend/tests/test_livecorr3_operational_guidance_and_
streaming_safety.py` (NEW, 9 tests: pure `enforce_response_mode_
compatibility` unit tests covering every requested_output value
including the unresolved-contract case, plus 3 full `chat_service.py`
integration tests — the exact live TROUBLESHOOTING_NEXT_STEP defect
shape closed, the EXACT_COMMAND variant also closed, and the PROCEDURE_
STEPS non-regression case still rendering the full procedure normally).
Live acceptance: NOT separately re-performed in this pass.

**UNDOCUMENTED INTERIM PASS, "LIVE-CORR-3A" (found during LIVE-CORR-3B's
own audit, recorded here for the first time since no prior entry existed
in this register):** the substring-scanning `_detect_embedded_
operational_content` mechanism described immediately above was, at some
point before LIVE-CORR-3B began, REPLACED outright by a typed
`TroubleshootingOperationalEffect` classification
(`REFERENCE_DESCRIPTION`/`OBSERVATION`/`DIAGNOSTIC_READ`/`STATE_CHANGE_
RECOMMENDATION`, on both `TroubleshootingStep` and `TroubleshootingGuidance`)
plus a deterministic structural rule (`evidence.py`'s
`enforce_structural_operational_integrity`: any step/guidance classified
— or defaulting, when unset, to — `STATE_CHANGE_RECOMMENDATION` must
carry a real `command`/`step.command`, or the whole guidance is
suppressed). This is a STRICTLY STRONGER mechanism than the substring
scanner it replaced (typed structure instead of text inspection,
consistent with this codebase's own repeated "no regex/keyword command
detection" instruction) and was already live in the codebase at LIVE-
CORR-3B's own starting commit (`ba172d9`) — current source is
authoritative; the LIVE-CORR-3 text above describing the substring
scanner is HISTORICAL only.

**LIVE-CORR-3B — Operational Authority Boundary CORRECTIVE PASS — FIXED
(closes the one remaining model-controlled exemption LIVE-CORR-3A's own
mechanism still carried).** LIVE-CORR-3A's own `DIAGNOSTIC_READ`
target-independent permission exemption (`request_execution_policy.py`'s
`enforce_execution_decision_on_guidance`) is REMOVED outright, per
section 3's own explicit "remove the DIAGNOSTIC_READ target-independent
permission bypass" requirement — that exemption's own "RESIDUAL RISK"
note (`TroubleshootingOperationalEffect`'s docstring) already named the
exact gap: `operational_effect` is model-populated, never governed step
metadata, so a model that mislabels a real state-changing recommendation
as `DIAGNOSTIC_READ` could bypass target confirmation for it. Until
governed step metadata positively proves an operation target-independent
(not yet available anywhere in the Knowledge model), every command is now
treated as target-dependent by this gate, unconditionally. Separately,
`derive_execution_decision`'s ALLOW branch no longer implicitly sets
`may_emit_command=True` for every fully-resolved request (only a
validated `intent=COMMAND, requested_output=EXACT_COMMAND` request grants
it), and `requires_unstructured_response_backstop` now also fires for an
operationally-shaped ALLOW decision with no `TroubleshootingGuidance` at
all — closing the remaining fail-open paths named in this pass's own
milestone objective. `INVALID_CONTRACT` was audited for the same
backstop widening and deliberately NOT extended — see that function's
own docstring for the two candidate designs measured directly against
this repository's real test suite and rejected on concrete evidence
(unconditional firing broke ~66 unrelated pre-existing tests spanning
streaming/governed-completion-gate/Teams-read-resume suites; gating on
the turn's own `record_source_requirements` declaration was unsafe in the
opposite direction, since a `requires_governed_knowledge=True` turn is
already fully covered by the pre-existing governed-knowledge completion
gate). Tests: `backend/tests/
test_livecorr3b_operational_authority_boundary.py` (21 tests: execution-
permission unit tests, DIAGNOSTIC_READ-removal proofs, source-section-
reference verification, and the milestone's own 8 required end-to-end
`ChatService`+`FakeRunner` pipeline scenarios). Live acceptance: NOT
separately re-performed in this pass (no browser access).

===================================================================
DEF-0041 — A structured `step.command` value that direct, deterministic
testing proves should fail grounding (`TRUE_ABSENCE`) nonetheless
reached the user unstripped
===================================================================

- **Status:** CLOSED — NOT REPRODUCIBLE AFTER CURRENT CORRECTIVE STACK
  (confirmed by a real live re-test; see "DEF-0041 FINAL LIVE ISOLATION"
  below, after the deterministic-isolation-pass note).
- **Severity (historical):** HIGH — same class of trust-boundary violation as
  DEF-0040, but for the STRUCTURED field the enforcement machinery is
  specifically designed to validate — if confirmed to be a live gap
  (rather than a test-environment artifact), this means even the
  "correctly used" `command` field is not reliably protected.
- **Live reproduction:** Session `38dbd231-a704-4734-9488-80db882a5b7e`
  (Scenario 4, turn 1, follow-up). `troubleshooting_guidance.full_
  procedure_steps[5]` (step 6 of 15): `{"action": "For Antenna Group /
  Unit alarms, execute the command to fetch the associated RRU.",
  "command": "hget near Rfportref"}` — a REAL, structured `command`
  value. This command's own governed source is section `A5-VALIDATION-
  DOCUMENT1:v2-def0024-fix:section-0002` ("HW Fault" — a merely
  SUPPORTING sibling section per DEF-0024's own established precedent),
  while the turn's own resolved active procedure (persisted
  `active_governed_procedure` at end of turn) is section `...section-
  0001` ("HW Partial Fault"). Section-0001's own real content (fetched
  read-only from `slopanoc_knowledge_objects`) does NOT contain "hget
  near Rfportref" anywhere.
- **Deterministic reproduction (definitive):** calling
  `resolve_active_section_id`/`_evaluate_command` DIRECTLY with the
  REAL question text ("How do I troubleshoot HW Partial Fault using the
  approved procedure?"), the REAL section headings ("HW Fault"/"HW
  Partial Fault"), and the REAL section-0001 content correctly resolves
  `active_section = section-0001` and correctly returns `grounded=False,
  reason=TRUE_ABSENCE` for `"hget near Rfportref"` — see `backend/
  tests/test_livecorr1_diagnostics.py::
  test_def_0041_hget_command_should_fail_grounding_against_real_content`
  (this test PASSES today, proving the pure grounding LOGIC is correct
  in isolation).
- **Confirmed root cause:** UNRESOLVED at the precision this diagnostic
  pass can achieve without live-instrumented tracing. The pure grounding
  function (`_evaluate_command`/`resolve_active_section_id`), exercised
  directly with the real inputs, correctly rejects this command — yet
  the live, persisted result contains it, unstripped, exactly as
  originally emitted. This proves a DISCREPANCY between designed
  behavior and observed behavior, but this pass could not isolate
  WHERE in the real runtime that discrepancy originates (candidates,
  none confirmed: a `run_id`/`current_run_id()` correlation timing gap
  causing `snapshot_selected_knowledge_evidence(run_id)` to return
  different data than the turn's own real selection at the exact point
  `_capture_and_render_troubleshooting_guidance` runs; the function not
  being invoked at all for this response; or another mechanism not yet
  identified) — determining this precisely requires either live
  instrumentation/logging added in a corrective pass, or a full,
  real-Gemini-driven integration reproduction, neither of which this
  read-only diagnostic pass may perform.
- **Affected trust boundary:** DEF-0024/0027's own core "a command is
  trusted only if grounded in THIS run's own genuinely selected
  evidence" guarantee.
- **Code locations:** `backend/agents/incident_manager/evidence.py`
  (`enforce_procedure_scoped_command_grounding_with_reason`,
  `_capture_and_render_troubleshooting_guidance`, `current_run_id`
  correlation) — exact failure point NOT yet isolated.
- **Relationship to existing DEF entries:** related to but distinct
  from DEF-0040 (which concerns commands that NEVER enter the `command`
  field at all) — this concerns a command that DID use the correct
  field and STILL was not correctly validated.
- **Required correction (NOT implemented by this diagnostic pass):**
  the corrective milestone must first ADD safe, non-sensitive
  instrumentation (run_id, section count, active-section resolution
  outcome) around `enforce_procedure_scoped_command_grounding_with_
  reason`'s own real invocation to determine definitively whether/how it
  is being bypassed, before attempting a fix.
- **Required tests:** `test_def_0041_hget_command_should_fail_grounding_
  against_real_content` (written, PASSING — proves the pure function
  logic itself is sound) plus a documented, NOT-yet-automatable live
  reproduction requirement for the corrective milestone (the actual
  runtime discrepancy cannot be deterministically reproduced without
  either live Gemini or an integration harness driving the REAL
  `incident_manager` agent through this exact multi-section selection,
  which this pass did not construct given the scope/time boundary of a
  diagnostic-only milestone).
- **Live acceptance requirements:** a corrective pass must show, via
  real live re-test of the exact Scenario 4 conversation, that "hget
  near Rfportref" (or any other cross-section command) is either
  correctly stripped or correctly re-classified as legitimately
  supporting the active procedure.
- **Explicit statement:** this diagnostic pass did NOT implement any
  fix and did NOT add instrumentation — `evidence.py` is unmodified by
  this pass.

**LIVE-CORR-3 CORRECTIVE PASS — INSTRUMENTATION ADDED, STATUS REMAINS
OPEN (mechanism NOT isolated, per this pass' own explicit "instrumentation
alone must not close DEF-0041" constraint).** `evidence.py`'s new
`_log_grounding_decision` (a safe, `try`/`except`-wrapped, non-behavior-
changing logger call — instrumentation, never a control-flow branch) is
now called at EVERY return point of `enforce_procedure_scoped_command_
grounding_with_reason` (all 6: the two DEF-0040 whole-guidance-
suppression paths, both FULL_PROCEDURE outcomes, and both NEXT_STEP
outcomes), logging exactly the section-16 field list this defect's own
register entry already named as needed to isolate it: `run_id`,
`interaction_mode`, `selected_count`, `selected_identities` (a list of
`(knowledge_id, version_label, section_id)` triples — safe identifiers
only, never section content), `active_section_id` (computed via a SECOND,
pure, side-effect-free call to the SAME `resolve_active_section_id` this
function's own `_evaluate_command` call uses internally — never a second
source of truth for any actual grounding decision, purely for log
visibility), `command_present`, `stripped`, and `reason`. Confirmed via
the full DEF-0024/0026/0027 regression suite (98 tests, all passing
unchanged) that this instrumentation is genuinely non-behavior-changing.
**This closes NEITHER the "root cause: UNRESOLVED" note above NOR this
defect's own OPEN status** — no live Gemini/Cloud SQL re-test was
performed in this pass (same standing limitation as every prior
milestone), so the instrumentation's own real-world output has not yet
been observed against the exact live Scenario 4 conversation. The next
live re-test of that conversation will, for the first time, produce a
real `troubleshooting_command_grounding` log line the corrective
milestone can directly compare against the live, observed (defective)
outcome to determine definitively whether the function was invoked with
the expected `run_id`/selected evidence, or whether the discrepancy
originates elsewhere entirely.

**DEF-0041 ISOLATION PASS (this milestone) — STILL OPEN; no code change
made; new evidence recorded.** Per this milestone's own explicit scope
("trace the runtime path... identify the first point where live behavior
diverges... apply the smallest deterministic correction if and only if
the evidence supports one — no speculative fix"), this pass built the
deepest reproduction available without live Gemini/Cloud SQL access: a
direct drive of the REAL `enforce_incident_manager_response_integrity`
`after_agent_callback` (not the already-known-correct isolated pure
functions) with run-scoped selected Knowledge evidence populated the same
way the real `knowledge_search`/`knowledge_select_evidence` tools
populate it, and — the one thing genuinely new here — a real
`callback_context.user_content` carrying the incoming question text, the
exact field `_extract_incoming_question_text` reads in production. Every
PRE-EXISTING evidence.py test (`test_evidence_troubleshooting_guidance
.py`) omits `user_content` entirely, so none of them had ever exercised
DEF-0027's own active-section heading-resolution path at all — only its
`question=None` fallback. See `backend/tests/test_def_0041_full_pipeline_
grounding.py` (new, 4 tests, all passing) for the reconstruction and its
own full rationale.

**Result: no discrepancy found.** Reconstructing DEF-0041's own
documented shape exactly (two selected sections of the SAME governed
document — an active "HW Partial Fault" section and a merely supporting
"HW Fault" sibling whose real content alone contains the disputed
command — with a question containing the active heading verbatim) through
the REAL callback correctly strips the command, correctly rewrites the
defense-in-depth `summary` field, and correctly registers the CORRECTED
(command-stripped) `TroubleshootingGuidance` into `troubleshooting_
guidance_context` for `chat_service.py`'s own later `pop_troubleshooting_
guidance` read — the exact object `chat_service.py`'s hard completion-
boundary override (section 14/17's own canonical-result invariant) would
then render. Every one of the register's own named unconfirmed
candidates was independently exercised and found to fail CLOSED, never
open: (a) `user_content` absent entirely (`question=None`) — falls back
to DEF-0024's original "grounded in every selected section" rule, still
withheld; (b) `user_content` present but its question text does not
contain either candidate heading verbatim — active-section resolution
unresolved, same fail-closed fallback, still withheld; (c) a simulated
total `run_id` correlation gap (the bound run_id under which the callback
runs never had ANY evidence selected against it at all, standing in for
candidate root cause (1) from the original register entry) — `_evaluate_
command`'s own empty-evidence branch (`TRUE_ABSENCE`) withholds it. No
combination this pass could construct reproduces the live, observed
"reached the user unstripped" outcome.

**What this does and does not prove.** It does NOT prove DEF-0041 is
fixed, and this pass does not claim that — `enforce_procedure_scoped_
command_grounding_with_reason`'s own core logic (DEF-0027) and its
wiring into `after_agent_callback` (A5) both predate DEF-0041's own
discovery and are UNCHANGED by this pass, so this is confirmation of
already-existing behavior under a newly-realistic test shape, not a new
fix landing. What it DOES establish: the specific "was grounding even
invoked with the right run_id/evidence/question" question the register's
own root-cause note left open is now answered, deterministically, for
every input shape this offline environment can construct — invoked, with
correct data, it behaves correctly. The remaining, unclosed possibility
is a genuinely LIVE-only condition this deterministic harness cannot
express (a live ADK/runtime behavior around `after_agent_callback`
timing or `user_content` population this pass did not find evidence of
but also cannot positively rule out without live access; a live-runtime
race/threading condition; or the original live discovery itself capturing
the wrong layer of the pipeline) — none of which meets this milestone's
own bar for a deterministic correction. Per the milestone's own STOP
CONDITIONS ("the defect cannot be reproduced," "root cause cannot be
deterministically identified"), no production code was changed.
**Status remains OPEN, now downgraded from "root cause unknown, mechanism
unverified" to "mechanism verified correct under every deterministic
reconstruction attempted; live re-test with the existing `troubleshooting_
command_grounding` instrumentation (LIVE-CORR-3) is the only remaining
path to further isolation."**

**DEF-0041 FINAL LIVE ISOLATION — CLOSED, NOT REPRODUCIBLE AFTER CURRENT
CORRECTIVE STACK.** A genuine live re-test was performed against the real
application: real FastAPI backend (`uvicorn backend.api.app:app`), real
Cloud SQL Auth Proxy v2 + IAM DB auth against the actual DEV instance
(`pr-msn-dev-gl-slopai-01:europe-west4:sloc-anoc-sandbox01`), real Gemini
2.5 Flash via Vertex AI, driven through the real `/api/sessions` /
`/api/sessions/{id}/messages` HTTP endpoints (the same endpoints the
React UI calls). The prior isolation pass's own missing piece — the
LIVE-CORR-3 `troubleshooting_command_grounding` instrumentation
(`_log_grounding_decision`, `_logger.info(...)`) had never actually been
OBSERVABLE in any real run, historical or otherwise: this codebase never
raises the root/module logging level above Python's own default
(`WARNING`) anywhere (verified: no `logging.basicConfig`/`dictConfig`
call exists anywhere under `backend/`; uvicorn's own default
`LOGGING_CONFIG` only sets levels for its own `uvicorn`/`uvicorn.error`/
`uvicorn.access` loggers, never the root logger; Python's "handler of
last resort" only emits `WARNING`+), so every `_logger.info(...)`/
`_perf_logger.info(...)` call in this codebase — including the exact
instrumentation LIVE-CORR-3 added specifically to isolate this defect —
has been silently dropped in every real run to date, including the
original live sessions that first exposed DEF-0041. This pass worked
around that gap for its own diagnostic run only, via a
`logging.basicConfig(level=logging.INFO)` call in a throwaway launcher
script kept entirely OUTSIDE the repository (never a repository file
change) — **this observability gap itself is a real, confirmed, still-
unfixed finding, recorded here for a future milestone since a repository-
level logging-configuration fix was out of this pass' own explicit
"do not refactor" scope.**

The original DEF-0041 session (`38dbd231-a704-4734-9488-80db882a5b7e`)
still existed in the live DEV database; its persisted history was read
directly and shows the PRECISE reproduction shape was a two-turn
follow-up, not a single FULL_PROCEDURE response: turn 1 ("How do I
troubleshoot HW Partial Fault using the approved procedure?") correctly
rendered its 15-step FULL_PROCEDURE response with NO command shown for
the step whose governed source was the supporting "HW Fault" sibling
section — grounding worked correctly for that turn, historically, too.
Turn 2, a plain follow-up ("Give me the first approved command for that
procedure.") is where the live defect actually manifested: the persisted
assistant reply is the bare, unqualified sentence `"The first approved
command for that procedure is: `hget near Rfportref`"` — the exact
non-active-section command, with no fallback wording, no withholding
language, nothing indicating any safety mechanism intervened at all. This
is a materially more precise reconstruction of the original defect than
the register's own original entry (which pointed at `full_procedure_
steps[5]`) — the true live shape was a NEXT_STEP-mode follow-up turn.

Reproducing this exact two-turn conversation fresh, twice, in new
sessions against the real live stack, both times produced SAFE behavior
throughout — the model's own real `knowledge_search`/`knowledge_select_
evidence` tool calls this time selected only ONE section (the active
"HW Partial Fault" section) for both turns, so the cross-section shape
never even arose; genuine model-output variance, not a code change,
explains the difference from the original run — and even so, both
attempts' `TroubleshootingGuidance` carried no `command` at all, and the
`exact_command`-requesting follow-up turn was independently blocked by
`derive_execution_decision` (`missing_context_keys=['unit_id',
'unit_type']` → `may_emit_command=False`), a second, independent gate
that would have withheld any command regardless.

A THIRD live scenario, explicitly requesting "the complete approved
procedure... all steps, do not wait for confirmation" (chosen specifically
to force the same multi-section evidence selection the original defect
needed), reproduced the exact cross-section shape TWICE, independently,
in two separate fresh sessions: real `knowledge_select_evidence` calls
selected THREE sections this time (including both "HW Partial Fault" and
the supporting "HW Fault" sibling), the model DID propose a structured
`command` for the disputed step, and the real, live
`troubleshooting_command_grounding` log line — now actually observable —
confirmed grounding correctly intervened both times:
`interaction_mode=full_procedure selected_count=3 ... command_present=True
stripped=True reason=unverified_section_reference`. The rendered,
persisted, canonical user-visible text for that step contained no
structured command at all — only the fixed, deterministic fallback
sentence — and canonical live/history equality held byte-for-byte in every
case checked. The positive exact-command control (a fully-specified,
correctly-grounded `RRU-9` restart request) was independently confirmed
ALLOWED through, unaffected: `may_emit_command=True`,
`command_present=True stripped=False reason=None`, live text ==
refreshed history text, byte-for-byte — proving the corrective stack has
not simply disabled commands globally.

**Conclusion: DEF-0041's own precise mechanism — a `command`/`step.
command` structured field value that deterministic grounding rejects
nonetheless reaching the user unstripped — does not reproduce against the
real live stack as it exists today.** The historical observation was
real (confirmed via the original session's own persisted history, read
directly from the live database); its precise original root cause was
never isolated at the code level (the missing observability, above,
made that structurally impossible at the time); but the cumulative
corrective stack already in place (DEF-0024/0026/0027's original
grounding, LIVE-CORR-3A's structural-integrity rule, and — decisively —
LIVE-CORR-3B's `_verify_source_section_reference` `source_section_id`
check) closes it, live-proven, twice, independently. No new production-
code correction was required or made by this pass.

**IMPORTANT — a DIFFERENT, NEW, live-confirmed gap was found and recorded
as DEF-0045 (not DEF-0041, not implemented, per this milestone's own
explicit scope): in BOTH of the successful cross-section reproductions
above, the verbatim disputed command string ("hget near Rfportref")
still reached the user — not via the structured `command` field (correctly
stripped both times) but embedded directly in the free-text `action`
prose of an unrelated step** (e.g. `"Execute \`hget near Rfportref\` to
fetch the associated RRU. Then, restart the identified RRU."`), which no
existing mechanism inspects — `_evaluate_command`/`_verify_source_
section_reference` only ever validate the `command`/`step.command` field,
never `action`/`interpretation`/`next_action` free text; `enforce_
structural_operational_integrity` only fires when `operational_effect ==
STATE_CHANGE_RECOMMENDATION` AND `command` is unset, which this step did
not trigger. See DEF-0045 below for the full record — this is explicitly
OUT OF SCOPE for this pass and was not investigated further or acted on.

===================================================================
DEF-0042 — ESS "first approved troubleshooting action" response skipped
documented prerequisite steps and later made a factually incorrect
"no command specified" claim against the same governed section
===================================================================

- **Status:** OPEN.
- **Severity:** MEDIUM-HIGH — a factual-fidelity defect: the system
  presented an incomplete procedural recommendation as though it were
  the definitive first action, and separately gave the user a
  factually false statement about what the governed source contains.
- **Live reproduction:** Session `38dbd231-a704-4734-9488-80db882a5b7e`
  (Scenarios 5). Selected evidence: `A5-VALIDATION-ROGERS-4G5G:v1:
  section-0000` (fetched in full, read-only, from `slopanoc_knowledge_
  objects`). The section's own real content ends with an explicit,
  numbered `"Activity workflow:"` block: `"1. SSH to ENM then AMOS to
  node. 2. Check alarm status. 3. If alarm present on node, then
  performed reset on node once in 24 hours. 4. If alarm still present on
  node after reset, then raise ticket. 5. Report will be there in excel
  ..."` — this is the ONLY place in the section that presents an
  explicitly ORDERED sequence of actions; the live response ("Restart
  the affected radio (RRU)... confirm the alarm status after 5 minutes")
  corresponds to workflow step 3 alone, skipping steps 1-2 (login and
  status check) entirely. Separately, the SAME section also contains
  real, concrete commands (`"DUS Radio Reset: amos xxxxx / lt all / acc
  auxpluginUnit=xxx manualrestart / y / 2 / 0 / Plan"` and `"Baseband
  Radio Reset: amos xxxxx / lt all / acc FieldReplaceableUnit=xxxx
  restartunit / y / 2 / 0 / plan"`), yet the turn's own follow-up
  response stated: `"The procedure does not specify a command for this
  action. It only states to 'Restart the affected radio (RRU).'"` — a
  factually incorrect claim given the section's own real content.
- **Confirmed root cause:** model-reasoning/retrieval-presentation
  fidelity, not a deterministic-code defect — this codebase has NO
  mechanism (deterministic or otherwise) that verifies a "next
  action"/"first action" claim against the governed source's own
  explicit ordering, or that verifies a "no command specified" claim
  against the full selected evidence text. This is a genuine, confirmed
  gap in CAPABILITY (no verification exists), not a bug in an existing
  mechanism.
- **Affected trust boundary:** `docs/TROUBLESHOOTING_STRATEGY.md`'s own
  "operational commands should be grounded in approved knowledge... not
  recalled from general model knowledge and presented as authoritative"
  principle, and the implicit "the system does not state something
  false about its own governed source" expectation.
- **Code locations:** none identified as a deterministic-code defect —
  this is a genuine capability gap; the closest existing related
  mechanism is `backend/agents/incident_manager/prompts.py`'s own
  "COMMAND TRUST AND PRESERVATION"/"ITERATIVE TROUBLESHOOTING" guidance,
  which does not currently address source-order fidelity or the
  distinction between "no command in the ACTIVE STEP" vs. "no command
  ANYWHERE in the selected evidence."
- **Relationship to existing DEF entries:** distinct from DEF-0040/0041
  (which concern commands reaching the user WITHOUT validation) — this
  concerns a command-EXISTENCE claim being factually wrong, and a
  procedural-ORDER claim being unsupported by the source's own explicit
  structure.
- **Required correction (NOT implemented by this diagnostic pass):** out
  of this pass's scope to design — likely requires either (a) prompt-
  level guidance to prefer an explicitly-ordered "workflow"/"steps"
  block over a summary table when both exist in one section, and to
  verify a "no command" claim against the FULL section text before
  stating it, or (b) a deterministic structural signal (e.g. surfacing
  to the model which parts of a section are an explicit ordered list vs.
  a summary table) — a design decision for the corrective milestone, not
  this diagnostic pass.
- **Required tests:** NOT added by this pass — requires exact governed
  Knowledge content and model-reasoning-level verification that cannot
  be deterministically reproduced without either the live database (used
  here only for read-only diagnosis, never fixture content extraction
  into the repository) or a real Gemini call; per this pass's own
  instruction, the passing regression implementation is deferred to the
  corrective milestone.
- **Live acceptance requirements:** a corrective pass must show the ESS
  scenario's first-action response reflects the document's own explicit
  activity-workflow order, and that any "no command specified" claim is
  verified against the complete selected section text.
- **Explicit statement:** this diagnostic pass did NOT implement any
  fix and did NOT add any fixture containing real governed document
  content to the repository.

===================================================================
DEF-0043 — `command_suppression_fallback_text` discards already-known,
specific `missing_context` for `AMBIGUOUS`-status decisions, collapsing
to a generic message
===================================================================

- **Status:** FIXED (LIVE-CORR-2, code + regression-tested; live browser
  acceptance not separately re-performed — see the corrective-pass note
  below).
- **Severity:** MEDIUM — a UX/clarity defect, not a safety defect (the
  underlying decision to withhold the command is CORRECT in both cases)
  — the system had a more helpful, already-computed answer available and
  discarded it.
- **Live reproduction:** Session `a6bbf7cf-258a-4ba1-a0b7-75c80fd94644`
  (LIVE-CORR-1 Scenario 6). User: "For the approved HW Partial Fault
  procedure, give me the first command. Do not assume any equipment
  identifier or missing condition." `validated_request_contract`:
  `{"intent": "procedure", "subject": "HW Partial Fault procedure",
  "ambiguity": true, "missing_context": ["equipment identifier",
  "missing condition"]}` — the model ITSELF already correctly,
  specifically identified what is missing. The final response was the
  GENERIC `"An exact command cannot yet be safely provided for this
  step. Please confirm the missing details."` — never mentioning
  "equipment identifier" or "missing condition" at all, even though
  both were already known.
- **Deterministic reproduction:** confirmed directly —
  `command_suppression_fallback_text(RequestExecutionDecision(status=
  "ambiguous", subject="HW Partial Fault procedure", missing_context=
  ["equipment identifier", "missing condition"]))` returns
  `_GENERIC_WITHHELD_COMMAND_TEXT` (the generic fallback), never the
  specific `_MISSING_CONTEXT_FALLBACK_TEXT_TEMPLATE` the SAME function
  already uses for `NEEDS_INFORMATION` status. See `backend/tests/
  test_livecorr1_diagnostics.py::
  test_def_0043_ambiguous_status_discards_known_missing_context`.
- **Confirmed root cause:** `backend/agents/team_manager/request_
  execution_policy.py`'s `command_suppression_fallback_text` (line
  ~328) branches on `status == NEEDS_INFORMATION` for the specific
  template, but for `status == AMBIGUOUS` only special-cases `not
  decision.subject` (→ `_NO_SUBJECT_FALLBACK_TEXT`) — any OTHER
  `AMBIGUOUS` case, even one carrying a fully populated `missing_
  context` list, falls through to the generic message.
- **Affected trust boundary:** none (UX-only) — the underlying
  execution-policy DECISION (withhold the command) is unaffected and
  correct.
- **Code locations:** `backend/agents/team_manager/request_execution_
  policy.py` `command_suppression_fallback_text` (line ~328-338).
- **Relationship to existing DEF entries:** a refinement gap in the SAME
  function DEF-0028's final corrective pass introduced
  (`command_suppression_fallback_text`); not a duplicate.
- **Required correction (NOT implemented by this diagnostic pass):**
  extend the `AMBIGUOUS` branch to also use `_MISSING_CONTEXT_FALLBACK_
  TEXT_TEMPLATE` whenever `missing_context` is non-empty, regardless of
  whether `subject` is set — reserving the generic text for the
  genuinely uninformative case only.
- **Required tests (written, xfail, by this pass):**
  `test_def_0043_ambiguous_status_discards_known_missing_context`.
- **Live acceptance requirements:** a corrective pass must show
  Scenario 6's exact conversation producing a clarification that
  explicitly names "equipment identifier"/"missing condition."
- **Explicit statement:** this diagnostic pass did NOT implement any
  fix — `request_execution_policy.py` is unmodified by this pass.

**LIVE-CORR-2 CORRECTIVE PASS — FIXED.** `command_suppression_fallback_
text`'s specific-template check now reads `if decision.missing_context:`
alone, independent of `status` — `NEEDS_INFORMATION` always carries a
non-empty `missing_context` by construction (unaffected); `AMBIGUOUS`
now uses the SAME specific template whenever IT ALSO carries a non-empty
`missing_context` (Scenario 6's own shape), falling back to the no-
subject/generic text only when it genuinely has nothing specific to say
(DEF-0029's own resolved-subject-but-still-ambiguous-procedure case,
non-regression-tested). Each rendered key now also passes through a new,
small, closed `_safe_missing_context_label` mapping — a raw internal key
name (`"unit_id"`) is NEVER shown verbatim to the user, rendered instead
as a safe, generic phrase ("the affected unit identifier (for example
the exact RRU or AAS identifier)"); a model-declared, already-safe
phrase (e.g. "equipment identifier") passes through completely
unchanged, per the instruction's own explicit "only include an example
if it is generic... not copied from an unrelated Knowledge example"
requirement. Three pre-existing tests that asserted the raw `"unit_id"`
key appeared verbatim in the fallback text were updated (not weakened —
strengthened) to assert it does NOT appear and a safe label does instead
(`test_6a14_deterministic_request_execution.py::test_rru_unit_type_
without_unit_id_asks_for_unit_id`/`test_aas_unit_type_without_unit_id_
asks_for_unit_id`, `test_6a14_final_corrective_pass.py::test_hw_partial_
fault_first_turn_asks_for_unit_context`) — the only existing test
content this pass changed for a reason other than the two-factor gate
migration, and only because each was pinned to the OLD, less-safe
wording. Tests: `backend/tests/test_livecorr1_diagnostics.py::test_def_
0043_ambiguous_status_discards_known_missing_context` (`xfail` marker
REMOVED, now PASSES); `backend/tests/test_livecorr2_request_context_
policy_correction.py` (6 new tests: `NEEDS_INFORMATION` non-regression
with safe-label proof, no-subject/no-missing-context non-regression,
resolved-subject/empty-missing-context generic-text non-regression,
`INVALID_CONTRACT` non-regression, model-declared-phrase-passthrough
proof, and Scenario 6's own exact request shape end to end through
`derive_execution_decision` + `command_suppression_fallback_text`
together). Live acceptance: NOT separately re-performed in this pass.

===================================================================
DEF-0044 — Raw, uncorrected model text can be streamed live to the user
via `MESSAGE_DELTA` before the completion-boundary deterministic
correction has run, independent of and prior to every existing
safety/consistency mechanism (DEF-0024 through DEF-0043, 6A.14, 6A.14A)
===================================================================

- **Status:** FIXED (LIVE-CORR-3, code + regression-tested; live browser
  acceptance not separately re-performed — see the corrective-pass note
  below).
- **Severity:** HIGH — this is a SYSTEMIC, transport-layer gap that sits
  UPSTREAM of every deterministic correction mechanism in this codebase.
  Even a hypothetically perfect completion-boundary enforcement (fixing
  every other defect in this register) would NOT close this gap, since
  by construction it only ever corrects the FINAL, persisted text —
  never what was already streamed to the screen in real time.
- **Live reproduction:** not independently confirmable from the
  read-only Cloud SQL session data alone (streaming events are not
  persisted) — CONFIRMED by direct source-code audit instead (see
  below), consistent with and explaining the pattern seen in every
  scenario where the raw model text (captured in the durable ADK event,
  e.g. Scenario 1's "Hello! How can I help you today?") differs from the
  final persisted/displayed text.
- **Confirmed root cause:** `backend/api/chat_service.py` line ~1122:
  `run_config = RunConfig(streaming_mode=StreamingMode.SSE)` — true,
  live, incremental streaming is unconditionally active for the main
  team_manager Runner call. Line ~1621: `yield sequencer.build(
  StreamEventType.MESSAGE_DELTA, {"text": delta_text})` — emits each
  raw text chunk AS THE MODEL PRODUCES IT, entirely INSIDE the Runner's
  own event loop. Every deterministic correction mechanism this codebase
  has (the DEF-0024/0026/0027/0028 grounding lineage, the 6A.14
  execution-policy backstop, the KNOWLEDGE_INVENTORY override, DEF-0037/
  0038/0039/0040/0041/0043 above) runs AFTER this loop completes, on the
  fully-assembled `final_text` only — none of it has any way to affect
  text already emitted via `MESSAGE_DELTA`. On the frontend,
  `src/state/AppState.tsx`'s `BACKEND_MESSAGE_DELTA` reducer (line
  ~1065) APPENDS each delta directly to the visible message text with
  `status: "streaming"` — genuinely rendering it to the screen in real
  time; `BACKEND_MESSAGE_COMPLETED` (line ~1083) later performs an
  "authoritative overwrite" with the corrected text, but only once the
  full turn (including every post-loop correction) has finished.
- **Affected trust boundary:** ALL of them — this is the one mechanism
  every other deterministic safety/consistency control in this codebase
  implicitly assumes does not exist.
- **Code locations:** `backend/api/chat_service.py` (`RunConfig(
  streaming_mode=StreamingMode.SSE)` at line ~1122; `_extract_delta_
  text`/`MESSAGE_DELTA` yield at line ~1588-1621); `src/state/AppState
  .tsx` (`BACKEND_MESSAGE_DELTA` reducer, line ~1065-1081).
- **Relationship to existing DEF entries:** upstream of and orthogonal
  to DEF-0024 through DEF-0043 — fixing any or all of those does not
  close this gap, and closing this gap does not remove the need for
  those (a corrected FINAL text must still be correct).
- **Required correction (NOT implemented by this diagnostic pass):** a
  genuine architectural decision for the corrective milestone, not a
  small patch — options to evaluate include: withholding `MESSAGE_
  DELTA` emission for any turn whose `RequestContract`/execution
  decision cannot yet be known to be safe (loses true low-latency
  streaming for those turns); buffering deltas and only flushing them
  once the corresponding portion of `final_text` is confirmed
  unmodified (complex); or scoping true streaming only to requests that
  structurally cannot carry an operational command (e.g. confirmed
  `ALLOW` + non-operational `requested_output`). Do not implement in
  this pass.
- **Required tests (written, xfail, by this pass):**
  `test_def_0044_message_delta_exposes_pre_correction_text` — drives a
  real `execute_turn_events` turn (scripted model, no Gemini) whose
  final text will be deterministically replaced, and asserts (xfail)
  that the accumulated `MESSAGE_DELTA` text never contains the raw,
  pre-correction content.
- **Live acceptance requirements:** a corrective pass must show, via a
  real browser trace/network inspection, that no scenario subject to a
  completion-boundary correction ever displays the pre-correction text
  on screen, even transiently.
- **Explicit statement:** this diagnostic pass did NOT implement any
  fix — `chat_service.py` and `src/state/AppState.tsx` are unmodified by
  this pass (confirmed via `git diff`, including `src/`).

**LIVE-CORR-3 CORRECTIVE PASS — FIXED.** Adopted the mandatory, fixed
buffering policy the milestone's own instruction specified (never a
per-turn conditional choice): status/progress events may stream
immediately; assistant/specialist text is NEVER emitted raw via
`message.delta`, at all, for any turn; the validated canonical response
reaches the client exactly once, via `message.completed`, only after
every deterministic correction and durable canonical persistence
succeed. `chat_service.py`'s three raw-text `yield sequencer.build(
StreamEventType.MESSAGE_DELTA, {"text": ...})` call sites (the main
per-event loop, the pre-existing governed-knowledge buffer-release
branch, and the trusted-presentation-retry path) were all removed, not
merely bypassed. The pre-existing, NARROWER governed-knowledge-specific
buffer/release/discard mechanism (added for a DIFFERENT, earlier
purpose — 5.1J source-requirements gating, buffering only while
classification was UNKNOWN, releasing live once resolved to non-
governed) became entirely REDUNDANT under the new universal policy (no
classification outcome ever "releases" text live anymore) and was
deleted outright — a net simplification (~90 lines removed), not a
parallel mechanism layered on top. `_extract_delta_text`'s own return
value is still consulted, ONLY to detect that a chunk has arrived (to
clear the "thinking" `STATUS_CLEAR` status indicator once, a status/
progress signal explicitly permitted to stream) — its TEXT is discarded
immediately, never buffered, never emitted. `final_text` was never
derived from these per-chunk deltas in the first place (it comes from
`_extract_final_text`'s own separate, complete/non-partial event), so
nothing is lost by never accumulating them. Deliberately NOT optimized
into conditional/partial streaming in this pass, per the milestone's own
explicit "no conditional conversational token streaming" instruction —
an intentional, documented, temporary loss of progressive assistant-
token streaming UX, left for a later milestone's own separate, approved
design (see this entry's own "Required correction" note above for the
option space considered and explicitly deferred).

Tests: `backend/tests/test_livecorr1_diagnostics.py::test_def_0044_
message_delta_exposes_pre_correction_text` (`xfail` marker REMOVED, now
PASSES). `backend/tests/test_chat_service_streaming.py` — 6 pre-existing
tests that asserted the OLD, now-incorrect raw-streaming behavior were
rewritten to assert the corrected invariant (`test_incremental_partials_
never_become_message_delta_text`, `test_status_and_completed_event_
order_is_preserved`, `test_no_delta_event_ever_carries_any_text`,
`test_status_cleared_before_message_completed`, `test_no_deltas_carry_
the_answer_only_completed_does`, `test_thought_parts_never_reach_any_
emitted_event`) — the only existing test content this pass changed for
a reason other than DEF-0040/0037-adjacent fixture updates, and only
because they were pinned to the now-intentionally-removed behavior.
`backend/tests/test_p5_1j_governed_completion_gate.py`'s own Part 21
family (8 tests) — 6 of 8 already asserted `delta_events == []` for
their own governed/still-classifying scenarios and needed no change
(byte-for-byte correct, unmodified proof of the now-broader invariant);
2 (`test_part21b_...`/`test_part21d_...`) specifically asserted the
OPPOSITE, now-incorrect behavior (live streaming for an explicitly non-
governed turn) and were renamed + corrected. A superseding note was
added to that section's own header comment, explaining the relationship
without deleting the original, historically-accurate narrative.

Live acceptance: NOT separately re-performed in this pass (no
interactive browser tool available in this session) — every fix is
automated-test-verified only.

===================================================================
DEF-0045 — A governed command string reproduced verbatim in a
`TroubleshootingStep.action`/free-text field is never validated by any
existing mechanism, even when the SAME step's structured `command` field
is correctly grounded/stripped
===================================================================

- **Status:** OPEN. Found and recorded during the DEF-0041 final live
  isolation pass; NOT investigated further and NOT implemented, per that
  pass' own explicit scope boundary ("If DEF-0041 investigation uncovers
  evidence relevant to DEF-0042 [or an adjacent concern]: record it; do
  not implement it").
- **Severity:** MEDIUM-HIGH — the same class of "a governed operational
  string reaches the user without going through the trusted grounding
  boundary" concern DEF-0040/DEF-0041 both address for the `command`
  field specifically, but for a field (`TroubleshootingStep.action`, and
  by the same reasoning `interpretation`/`next_action`/`evidence_
  requested`) no existing mechanism inspects at all — the STRUCTURED
  safety net can be working perfectly (as confirmed live, twice, in the
  DEF-0041 closure evidence below) while the exact same disputed command
  string still reaches the user through the step directly next to it.
- **Live reproduction:** confirmed directly, twice, independently, in
  two fresh sessions against the real application (real FastAPI backend,
  real Cloud SQL DEV instance, real Gemini 2.5 Flash via Vertex AI) —
  see the DEF-0041 entry's own "DEF-0041 FINAL LIVE ISOLATION" note for
  the full session/turn detail. Prompt: "Give me the complete approved
  procedure for HW Partial Fault, all steps, do not wait for
  confirmation." Both times, `knowledge_select_evidence` selected three
  sections including the "HW Fault" supporting sibling (whose only real
  command is `hget near Rfportref`); both times the structured `command`
  field for the affected step was correctly grounded/stripped
  (`troubleshooting_command_grounding ... command_present=True
  stripped=True reason=unverified_section_reference`); both times the
  PERSISTED, CANONICAL, user-visible text nonetheless contained the
  verbatim string `hget near Rfportref` embedded in that step's own
  `action` prose, e.g.: `"Execute \`hget near Rfportref\` to fetch the
  associated RRU. Then, restart the identified RRU."` Canonical live/
  history equality held (the leak is not a streaming/correction-timing
  artifact — it is genuinely part of the persisted canonical text).
- **Confirmed root cause:** deterministic, by direct code inspection,
  not merely inferred from the live symptom. `_evaluate_command` and
  `_verify_source_section_reference` (`backend/agents/incident_manager/
  evidence.py`) are invoked ONLY against `TroubleshootingStep.command`/
  `TroubleshootingGuidance.command` — neither is ever called against
  `action`/`interpretation`/`next_action`/`evidence_requested`, which
  are free-form prose fields the model populates independently and which
  this codebase has consistently, deliberately never parsed for embedded
  operational content (per the LIVE-CORR-3A "operational safety must come
  from typed structure... do not replace it with regex/keyword/free-text
  command detection" instruction this codebase already follows).
  `enforce_structural_operational_integrity` (LIVE-CORR-3A/DEF-0040) is
  the one existing mechanism that DOES inspect a step holistically, but
  it fires ONLY when `operational_effect == STATE_CHANGE_RECOMMENDATION`
  AND `command` is unset — the reproduced step's own `operational_effect`
  classification (whatever the model assigned it) did not satisfy that
  condition, so it never fired. This is therefore a genuine, confirmed
  CAPABILITY GAP (no mechanism exists for this field), not a bug in an
  existing mechanism — the same category DEF-0042's own register entry
  already uses this exact phrase for.
- **Affected trust boundary:** the same DEF-0024/0027/DEF-0041 "a command
  is trusted only if grounded in THIS turn's own genuinely selected,
  ACTIVE-procedure evidence" guarantee — but for a field the existing
  enforcement was never designed to reach.
- **Code locations:** `backend/agents/incident_manager/evidence.py`
  (`_evaluate_command`, `_verify_source_section_reference`, `enforce_
  structural_operational_integrity` — none of the three inspect
  `action`/free-text fields); `backend/agents/incident_manager/schemas.py`
  (`TroubleshootingStep.action` — a plain, unvalidated `str`).
- **Relationship to existing DEF entries:** distinct from DEF-0040
  (embedded operational content with NO structured `command` at all —
  closed by the typed `operational_effect` structural rule) and DEF-0041
  (a `command` field value bypassing grounding — closed, see that entry's
  own live isolation). Overlaps in SPIRIT with DEF-0042 (free-text
  fidelity/source-accuracy gap) but is a narrower, more precise,
  independently live-reproducible mechanism: a VERBATIM governed string
  (not a paraphrase, not a factual claim) appearing in prose no
  mechanism ever checks.
- **Required correction (NOT implemented — explicitly out of scope for
  the pass that found this):** a genuine design decision for a future,
  properly-scoped milestone, not a small patch — options to evaluate
  include extending typed classification/verification to `action` text
  (risk: drifts toward free-text parsing, which this codebase has
  consistently and deliberately avoided) or a stricter structural rule
  requiring any step whose `action` text contains a real command-shaped
  token to also carry a grounded `command` field (needs careful,
  deterministic definition of "command-shaped" that does not become a
  regex/keyword detector by another name). Do not implement without a
  dedicated milestone.
- **Required tests:** none written yet — this is a recorded finding
  only, per the pass' own explicit scope boundary.
- **Live acceptance requirements:** a corrective pass must show, via
  real live re-test, that a verbatim non-active-section command string
  can no longer reach the user through ANY field of `TroubleshootingGuidance`/
  `TroubleshootingStep`, not merely `command`.
- **Explicit statement:** the pass that found this did NOT implement any
  fix — no production code was changed as a result of this finding.

===================================================================
DEF-0046 — A configured environment variable could authorize a
governance approval and sign it with a human governor's name
===================================================================

- **Status:** FIXED (POST-6A repair campaign, prompt 6).
- **Severity:** HIGH — it is an authorization bypass whose artifact is a
  trusted audit record.
- **Component:** `backend/tools/admin/descriptor_admin.py`,
  `backend/config/settings.py` (`admin_actor_id`).
- **Symptom:** the descriptor CLI accepted `--actor alice` and performed
  a governance approval whenever `SLOPANOC_ADMIN_ACTOR_ID` matched that
  string and was on the governor allowlist — in ANY deployment, not only
  a development one.
- **Root cause:** `SLOPANOC_ADMIN_ACTOR_ID` is CONFIGURATION, and it was
  being read as IDENTITY. Setting an environment variable proves only
  that something could set an environment variable on the host: a
  deployment account, a CI runner, or any compromised process on the box
  qualifies equally. The resulting `OperationApprovalRecord` then named a
  real human as the reviewer of a state-changing Teams operation they had
  never seen — an audit record that is worse than no record, because it
  is trusted.
- **Fix:** the CLI's normal path is now the AUTHENTICATED HTTP API. It
  sends a bearer token from `SLOPANOC_API_TOKEN` and takes no `--actor`
  argument at all, in any subcommand; the server derives the actor from
  the verified token and writes the record against that identity. The
  direct-to-database path is `--local` and refuses unless
  `SLOPANOC_GOVERNANCE_DEV_MODE` is explicitly set, the same flag that
  makes the server itself accept unverified identity.
- **Tests:** `backend/tests/test_post6a_identity_and_concurrency.py::
  test_the_cli_takes_no_actor_argument_at_all`,
  `::test_the_cli_local_path_refuses_outside_development_mode`,
  `::test_the_cli_remote_path_requires_a_token`.
- **Related, fixed in the same pass:** drafting a descriptor was gated
  only on governance being AVAILABLE, not on governor PERMISSION. Since
  `author_operation` calls `invalidate_on_edit`, any authenticated user
  could overwrite a reviewed descriptor and demote an APPROVED operation
  to CANDIDATE — destroying operational authority for everyone. Removing
  authority is a governance act; it now takes the same permission check
  as granting it (`test_drafting_is_permission_gated`).

===================================================================
DEF-0047 — A long-running active turn could be recorded as INTERRUPTED
by an unrelated worker
===================================================================

- **Status:** FIXED (POST-6A repair campaign, prompt 6).
- **Severity:** MEDIUM — it fabricates a terminal outcome for work that
  is still running.
- **Component:** `backend/api/turn_lifecycle.py`
  (`reconcile_interrupted_turns`), `backend/api/chat_service.py`.
- **Symptom:** an ACCEPTED turn older than `stale_after_seconds` (900)
  was reconciled to INTERRUPTED by whichever worker next started a turn
  on that session — including a turn belonging to a different worker that
  was running perfectly well at that instant.
- **Root cause:** an AGE THRESHOLD was used as a proxy for "no longer
  running". It cannot distinguish "still working" from "gone", and the
  case it gets wrong is the one that matters most: a large multimodal
  investigation, a slow specialist chain, or a gateway simply taking its
  time is ACTIVE, and 15 minutes is not evidence of anything.
- **Fix:** reconciliation is now OWNERSHIP-based. A turn holds a
  PostgreSQL session advisory lock for its whole duration (`chat_service`
  `_drive`, released in its `finally`, including on cancellation). Another
  worker reconciles that turn only after establishing ownership is gone —
  by successfully TAKING that lock, which PostgreSQL releases the moment
  the owner's connection dies. A turn whose lock cannot be taken is left
  ACCEPTED however long it has been running. The `stale_after_seconds`
  parameter is removed outright rather than defaulted higher; a larger
  threshold is the same mistake, slower.
- **Tests:** `backend/tests/test_post6a_durable_outcomes.py::
  test_an_interrupted_turn_is_reconciled_not_lost`,
  `backend/tests/test_post6a_prompt6.py::
  test_a_long_running_active_turn_is_not_reconciled_as_interrupted`.

===================================================================
MAINTENANCE
===================================================================

When a new defect is confirmed:

1. Assign the next sequential `DEF-xxxx` (or `SEC-xxxx`/`OPS-xxxx`/
   `UX-xxxx`/`DOC-xxxx`, as appropriate) ID — never reuse or renumber.
2. Fill in every field of the schema above, with real evidence (root
   cause verified against actual source, not assumed) — do not
   speculate on a root cause you have not actually traced.
3. Link the corresponding milestone entry in
   `docs/MASTER_ROADMAP.md`'s chronological ledger back to this ID —
   the roadmap should link here, not duplicate this content.
4. If something is investigated and found NOT to be a defect, add it to
   "Considered and explicitly NOT registered" above with its category —
   do not create a DEF ID for it.
5. Update "Next available ID" at the top of this file.
