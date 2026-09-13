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

**Next available ID: DEF-0031.**
**First ID in this register: DEF-0001. Last ID currently used: DEF-0030.**
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
