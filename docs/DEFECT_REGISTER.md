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

**Next available ID: DEF-0017.**
**First ID in this register: DEF-0001. Last ID currently used: DEF-0016.**

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
