CLAUDE.md

Project Purpose

SLOPANOC is an enterprise AI assistant for Microsoft Teams-based incident and
operational collaboration. It began as a UI/UX-only prototype; that phase is
over. A real React frontend and a real FastAPI backend now exist, orchestrating
Gemini (via Google ADK) to read and summarize Microsoft Teams conversations
and to propose — never silently execute — Teams write actions.

Read this file together with README.md, docs/AGENT_CONTRACT.md, and
docs/TEAMS_TOOL_CONTRACT.md before making an architectural change. Those three
documents are current architectural truth; this file summarizes what matters
for day-to-day work in this repository and must never contradict them. If this
file and the implementation ever disagree, the implementation wins — treat
that as a signal this file needs a small correction, not that the code is
wrong.

docs/PRODUCT.md and docs/UX_SPEC.md remain useful for original product/UX
intent (Projects, global Knowledge, Connectors beyond Teams, Skills) — most of
that surface is still local-state mock UI, not backend-wired. Do not treat
those two documents as a description of current backend capability.

===================================================================
CURRENT STATE (verified against the implementation — do not assume more)
===================================================================

- Real React + TypeScript frontend (Vite, Tailwind) and real FastAPI backend.
- Gemini, via Google ADK, performs reasoning/orchestration.
- Team Manager is the only user-facing agent.
- Incident Manager is currently the principal specialist agent, invoked by
  Team Manager through an ADK AgentTool call (in-process, not a hand-off).
- Power Automate is the Microsoft Teams / M365 gateway. Microsoft Graph is
  not integrated directly anywhere in this stack.
- Persistent chat sessions are implemented (ADK DatabaseSessionService,
  SQLite locally).
- Persistent Case/fault context is implemented (backend/cases/), distinct
  from ordinary session/chat state.
- True SSE streaming is implemented, with real mid-run cancellation.
- Edit/rewind is implemented — editing an earlier message excludes later,
  now-stale turns from model context going forward.
- Deterministic conversation targeting is implemented (current_thread /
  selected_external_conversation / explicit_external_conversation).
- Teams read flows are implemented: chat discovery, deterministic name
  resolution, ambiguity handling, message retrieval with pagination and
  time-range scoping, decision/action/proposal/open-question/risk
  extraction.
- Teams write proposal/approval flows are implemented: propose → approve/
  reject (trusted API endpoint, never the model) → deterministic
  re-authorized execution.
- SelectionCard (ambiguous chat choice) and ApprovalCard (write-action
  approval) both exist and are different concepts — see "Selection vs.
  approval" below.
- TrustedSpecialistResult / trusted-continuation architecture exists: a
  validated specialist result can be handed to a presentation-only Team
  Manager variant (tools=[]) in the same turn, with a fail-closed path if
  validation fails.
- SourceReference / Supporting Evidence provenance exists: backend-built,
  message-owned, validated against actually-retrieved evidence.
- Markdown rendering and lightweight response typography are implemented
  for real backend assistant messages.
- Model warm-up and per-model-call performance/timing instrumentation
  exist.

Do not describe this project as UI-only, prototype-only, pre-agent,
pre-Teams-integration, pre-persistence, pre-streaming, pre-provenance, or
pre-approval. None of that is true anymore.

The application shell, sidebar, Projects, Settings (Usage/Connectors/
Skills), and Scheduled Tasks inherited from the original UI/UX prototype
phase still run on local, mock state — they are not backend-wired. The real,
backend-driven experience is the chat conversation itself (any chat with a
`backendSessionId`). Do not assume Projects/Connectors/Skills/Usage have
backend support just because the chat pipeline does.

===================================================================
FROZEN ARCHITECTURE — do not casually rework
===================================================================

    SLOPANOC React UI
            v
    FastAPI backend
            v
    Gemini ADK Team Manager
            v
    Incident Manager / deterministic application services
            v
    controlled tools
            v
    Power Automate gateway
            v
    Microsoft Teams / M365

- Team Manager remains the only user-facing agent.
- Incident Manager remains a specialist, invoked via AgentTool, never native
  sub_agents transfer.
- Power Automate remains the current M365 gateway. Do not introduce a direct
  Microsoft Graph integration unless explicitly requested.
- No model-controlled approval, ever.
- No model-controlled destination binding — a chat id used for retrieval or
  a write always traces back to a real tool result, never a model assertion.
- No fabricated provenance — evidence is validated against actually
  retrieved messages before it reaches the user.
- No unrestricted generic HTTP / SQL / shell tools given to any agent.
- No keyword/regex-based natural-language routing, unless a deterministic
  protocol explicitly requires it (conversation targeting and chat
  resolution are model semantic judgment plus deterministic validation, not
  string matching on the user's wording).
- Do not reopen frozen performance, provenance, or Markdown-rendering work
  unless a real, currently-observed defect requires it.

===================================================================
TARGET FUTURE ARCHITECTURE (not implemented — planning guardrail only)
===================================================================

The frozen architecture above is what runs today. The diagram below is
where the architecture is headed — it exists to keep Phase 5.1A and later
phases pointed at a consistent destination, not to describe anything
currently running. Nothing in this section may be treated as implemented,
and nothing in it changes the frozen architecture above.

    Head of Automated Operations        [FUTURE]
    Supervision / Efficiency / Governance
                |
                v
           Team Manager                 [CURRENT — user-facing orchestrator]
                |
       +--------+--------+
       v                 v
    Incident Manager   Troubleshooting Manager
     [CURRENT]            [FUTURE]
       |                   |
       +--------+----------+
                v
      Context Engineering Layer         [FUTURE]
                |
       +--------+--------+--------+
       v                 v                 v
    Operational       Knowledge          Case
     Context            Context          Context
       |                 |                 |
     Teams          Generic KM Layer     Cases
    [CURRENT]           [NEXT]          [CURRENT]
                         |
                +--------+--------+--------+
                v        v        v        v
               MOP      SOP      RCA      KB
                    (all behind Generic KM — [NEXT])

Key architectural statement (governs Phase 5.1 design):

"The Generic Knowledge Management Layer is one provider of Knowledge
Context within the broader Context Engineering architecture. It must
remain independent of individual specialist agents and expose governed,
validated knowledge through generic contracts."

Read this section as CURRENT / NEXT / FUTURE labels, never as "the runtime
includes" or "the system does" — those phrasings are reserved for what
`git`/tests actually prove exists. In particular:

- MOP/SOP/RCA/KB are never peer raw sources alongside Teams and Cases —
  they sit behind the Generic KM Layer, which itself sits behind Knowledge
  Context, which is one of three context domains the future Context
  Engineering Layer would assemble (Operational, Knowledge, Case).
- The Context Engineering Layer is a future conceptual abstraction, not a
  concrete implemented runtime service — do not build one, or a stub of
  one, during Phase 5.1A.
- Troubleshooting Manager and Head of Automated Operations are FUTURE only.
  Do not add either during Phase 5.1A, do not change the current agent
  topology to anticipate them, and do not make either a mandatory hop in
  the current runtime. The current user-facing path remains Team Manager,
  unchanged.
- Operational Context's future sources (ITSM, Alarms, Topology, KPIs,
  Change, Handover) are Phase 5.2–5.7 roadmap items, listed here only to
  show where Operational Context is headed — do not build any of them
  early.

===================================================================
NON-NEGOTIABLE TROUBLESHOOTING PRODUCT STRATEGY
===================================================================

The full strategy lives in docs/TROUBLESHOOTING_STRATEGY.md — a
NON-NEGOTIABLE product principle, not one option among several. Read it
before implementing Phase 5.1, any troubleshooting functionality, Context
Engineering, any future agent, an operational integration, state-changing
automation, or a security control that touches troubleshooting UX.

Mandatory principles from that document, restated here only as a governing
summary (do not treat this bullet list as a substitute for reading it):

- Troubleshooting is iterative, not checklist-driven.
- The central UX question is: "What should I check next, and why?"
- Every new evidence item must be interpreted before the next step is
  selected — never queue up another step without interpreting the last
  result first.
- Context must be continuously re-evaluated — new evidence can change what
  knowledge/operational/case context is relevant; it is never fetched once
  and reused for the rest of a session.
- Troubleshooting continuity must be preserved — a session is a persistent
  investigation, not a sequence of isolated questions.
- Only relevant, valid, authoritative context should reach the reasoning
  layer — never dump all available context/documents into the model.
- Operational commands should be grounded in approved knowledge wherever
  possible, never recalled from general model knowledge and presented as
  authoritative.
- Diagnostic reads/checks and production-changing actions are different
  trust classes.
- State-changing actions remain behind deterministic approval — this
  extends the existing ActionProposal/ApprovalCard boundary (§"TRUST /
  CONTROL PRINCIPLES" above), never a new, separately-reasoned mechanism.
- Generic KM (Phase 5.1) must support this future flow without becoming the
  troubleshooting loop itself — it provides governed Knowledge Context; it
  is not a Troubleshooting Manager, a Troubleshooting State runtime, or a
  next-best-action reasoner.

Governing rule: if an implementation decision conflicts with
docs/TROUBLESHOOTING_STRATEGY.md, stop and reconsider the design rather
than silently weakening the product principle.

This governs the same invariant already stated above: "Can the Knowledge
layer still work without knowing Incident Manager exists?" — if the answer
becomes no, the architecture is too coupled, independent of whether it
also satisfies the troubleshooting strategy.

===================================================================
TRUST / CONTROL PRINCIPLES
===================================================================

MODEL MAY:
- interpret, summarize, and classify Teams content
- select among already-validated evidence
- propose a Teams write action

MODEL MAY NOT:
- authorize a write
- approve its own action
- override a trusted, already-resolved chat destination
- fabricate source identity or evidence
- expose secrets (gateway URLs, credentials) in any form
- grant itself permissions
- bypass trusted application state (approval records, selection state,
  trusted-result envelopes)

Every sensitive decision — write authorization, destination binding,
evidence provenance — is enforced by deterministic application code, never
by an agent's prompt-following alone. When adding a new capability, ask
where the sensitive decision actually gets enforced; if the answer is "the
prompt asks the model nicely," that is not sufficient.

===================================================================
CURRENT TEAMS BEHAVIOR
===================================================================

- Conversation target resolution decides, per request, whether the user
  means current_thread (this SLOPANOC conversation), selected_external_
  conversation (the already-selected Teams chat), or explicit_external_
  conversation (a Teams chat named in the current message) — model semantic
  judgment, deterministically validated, never regex/keyword routing.
- Ambiguity is resolved via SelectionCard and session state, never via the
  approval mechanism — selection and approval are separate state machines.
- Once a destination is resolved, it is authoritative for the rest of the
  turn — the model cannot substitute a different chat.
- Read continuation (after a selection, or via the direct-unique fast path
  for a first-time exact match) uses trusted, backend-held state to drive
  retrieval deterministically, converging on the same trusted-result/
  presentation pipeline either way.
- Writes always go through ActionProposal + trusted approval before
  anything reaches Power Automate.
- Teams provenance is backend-validated: evidence not traceable to an
  actual retrieval is stripped before the user sees it.
- SourceReference is message-owned, not a global/session-level artifact.
- `teams_get_members` is used deterministically for contributor enrichment
  (building SourceReference), not as a model-callable tool — do not assume
  the model can look up chat membership on demand.

See docs/TEAMS_TOOL_CONTRACT.md for the full contract.

===================================================================
CURRENT PERSISTENCE / RUNTIME
===================================================================

- Session persistence uses ADK's DatabaseSessionService; local development
  defaults to SQLite unless `SLOPANOC_DATABASE_URL`/
  `SLOPANOC_KNOWLEDGE_DATABASE_URL` explicitly select PostgreSQL — SQLite
  remains the zero-setup default, never silently overridden.
- POST-5.1 A — Cloud SQL PostgreSQL (A1–A4) is COMPLETE. A Cloud SQL
  PostgreSQL 18 instance (`sloc-anoc-sandbox01`, project
  `pr-msn-dev-gl-slopai-01`, region `europe-west4`) and its `slopanoc`
  database exist, with IAM database authentication (Cloud SQL Auth Proxy
  v2, no DB password) as the local Cloud SQL development path.
  `resolve_knowledge_database_url()` supports the same `*_SECRET_RESOURCE`
  Secret Manager fallback `resolve_database_url()` already had. Alembic
  (`alembic/`) manages ONLY the SLOPANOC-owned Case/Fault + Governed
  Knowledge schema (`slopanoc_cases`, `slopanoc_case_memberships`,
  `slopanoc_case_session_links`, `slopanoc_case_context_items`,
  `slopanoc_knowledge_objects`) and has been applied to Cloud SQL. ADK
  owns its own session schema (`sessions`, `events`, `app_states`,
  `user_states`, `adk_internal_metadata`) entirely independently — Alembic
  must never manage it. A dedicated least-privilege PostgreSQL role
  architecture exists (`slopanoc_migrator` for schema/migration authority,
  `slopanoc_runtime` for application DML only, no `CREATE`); the developer
  IAM identity currently holds both roles for local development only, not
  as a statement about the future production runtime identity. The real
  SLOPANOC FastAPI runtime has been validated end-to-end against Cloud
  SQL: ADK session persistence, Case/Fault Context, the approval/session-
  state persistence mechanism, and the Governed Knowledge repository all
  proven to persist correctly, including surviving a backend restart, with
  local SQLite confirmed untouched during that validation. SQLite remains
  the zero-setup LOCAL DEFAULT when `SLOPANOC_DATABASE_URL`/
  `SLOPANOC_KNOWLEDGE_DATABASE_URL` are not explicitly set to PostgreSQL —
  that default behavior is unrelated to whether Cloud SQL support itself
  is implemented and proven, which it now is.
- ===============================================================
  MANDATORY CLOUD SQL RUNTIME POLICY (adopted at B6 closure, applies to
  B7 and every milestone after it)
  ===============================================================
  All live UI validation, all manual validation, all end-to-end
  validation, all integration validation, all restart/persistence
  validation, and all other production-like validation — for every
  persistent SLOPANOC runtime domain, governed Knowledge included — MUST
  use Cloud SQL PostgreSQL, never local SQLite/in-memory persistence.
  SQLite/in-memory is permitted ONLY for isolated automated tests, where
  storage isolation is a deliberate, correct choice (unit/integration
  tests must keep using disposable SQLite exactly as they already do —
  this policy governs live/manual/E2E/integration VALIDATION environments,
  not the automated test suite).

  Governed Knowledge MUST have its own explicit configuration set —
  `SLOPANOC_KNOWLEDGE_DATABASE_URL` or
  `SLOPANOC_KNOWLEDGE_DATABASE_SECRET_RESOURCE` — during any such
  validation. `SLOPANOC_DATABASE_URL` and `SLOPANOC_KNOWLEDGE_DATABASE_URL`
  remain LOGICALLY SEPARATE configuration concerns even when they
  currently point at the same Cloud SQL database — do not introduce an
  implicit fallback from the Knowledge setting to the general database
  setting; the goal is explicit configuration per domain, never
  configuration coupling.

  If a future live/manual/E2E/integration environment has NEITHER
  `SLOPANOC_KNOWLEDGE_DATABASE_URL` nor
  `SLOPANOC_KNOWLEDGE_DATABASE_SECRET_RESOURCE` configured, that
  environment is NOT a valid production-like KM validation environment —
  no live KM validation result from it may be accepted as closing a
  milestone. (Historical exception, preserved as an accurate record, not
  reopened: POST-5.1 B6's own live Tests B/C correctly and knowingly used
  the local SQLite governed-KM fallback — `KnowledgeDatabaseUrlSet=False`/
  `KnowledgeSecretSet=False` at the time — because this policy did not
  yet exist; this does not invalidate B6, and those tests must never be
  rewritten as Cloud SQL tests. This policy governs B7 onward.)

  B7 PRECONDITION (verify before B7 live validation begins, not performed
  in this documentation pass): the normal SLOPANOC database runtime
  resolves to Cloud SQL; the governed Knowledge runtime EXPLICITLY
  resolves to Cloud SQL (its own env var/secret set, never inherited);
  the Cloud SQL `slopanoc_knowledge_objects` table is reachable; any
  controlled validation knowledge B7 needs already exists in Cloud SQL;
  no live B7 result is accepted if governed KM silently resolved to local
  `slopanoc_knowledge.db`. The actual Cloud SQL KM runtime setup/
  environment migration happens after this B6 checkpoint, before B7 live
  work — not part of this documentation pass.
- Case/fault context (backend/cases/) is a separate, optional persistence
  layer from ordinary session/chat state — do not conflate the two.
- Streaming is real SSE, with a background-task turn model — a turn runs as
  a background task and is cancellable mid-run.
- Edit/rewind changes what is included in model context going forward; it
  does not delete history.
- Model warm-up and per-model-call timing instrumentation exist for
  diagnosis, not as a production observability/alerting stack.

Do not imply distributed, multi-instance, or otherwise fully "production"
persistence/runtime characteristics — none of that exists yet.

===================================================================
CURRENT LIMITATIONS
===================================================================

SLOPANOC is not production-ready. In particular, none of the following
exist yet:

- Phase 4H security hardening (trust-boundary/threat modeling,
  prompt-injection isolation, tool output validation, secret-handling
  hardening, security audit events, adversarial regression suite)
- enterprise authentication
- per-user Microsoft identity / delegated Graph (Power Automate currently
  runs as its own configured connection identity, not per-user)
- production deployment identity/configuration — local Cloud SQL
  validation (POST-5.1 A) used the developer's own IAM identity, a member
  of both `slopanoc_migrator` and `slopanoc_runtime`; a future deployment
  needs a dedicated runtime service account mapped only to
  `slopanoc_runtime`, and Secret Manager-based DB URL configuration is not
  yet exercised for Cloud SQL (Secret Manager fallback code exists, but
  local Cloud SQL development so far has used shell environment variables
  only)
- distributed runtime coordination
- production observability/alerting
- load/concurrency validation
- final secret-management hardening
- broader operational integrations: generic Knowledge Management, ITSM,
  alarm/topology/KPI/change integrations
- autonomous remediation of any kind

See README.md's "Current limitations / production readiness" for the full,
current list.

===================================================================
LOCKED ROADMAP — do not reorder
===================================================================

CURRENT: Teams integration, core platform, and Markdown rendering are
complete.

CURRENT (out-of-band milestone, inserted between the LOCAL GIT CHECKPOINT
below and Phase 4H — does not reorder anything in this locked list):
POST-5.1 A — CLOUD SQL POSTGRESQL (A1–A4) is COMPLETE. POST-5.1 B —
MULTIMODAL ATTACHMENTS: B0-B6 done. B7 (Lifecycle + Real UI + Full
Regression) is next.
A5 (real TELCO/RAN MOP ingestion) follows POST-5.1 B, before Phase
4H security hardening.

POST-5.1 B execution sequence (locked, do not reorder):
  B0 [DONE] Durable chat attachment architecture + ADK persistence audit
  B1 [DONE] Persistent Attachment Foundation -- Cloud SQL metadata model
      (`slopanoc_chat_attachments`, Alembic-managed), private GCS bucket
      foundation (`slopanoc-chat-attachments-sandbox01`, europe-west4),
      `backend/attachments/{models,repository,service,storage}.py`. No
      HTTP endpoints, no frontend, no Gemini/ADK wiring yet.
  B2 [DONE] Attachment Upload/Retrieve API -- multipart image upload
      (`POST /api/sessions/{session_id}/attachments`), Pillow-validated
      (actual-format decode, declared-vs-actual MIME match, PNG/JPEG/WebP
      only), private GCS write + Cloud SQL `READY` row, authenticated
      metadata/content retrieval (`GET /api/attachments/{id}[/content]`),
      GCS calls off the event loop (`run_in_threadpool`), best-effort GCS
      cleanup on DB-insert failure. No frontend, no `attachment_ids` on
      message-send, no `Part.from_uri` construction in the live chat path
      yet.
  B3 [DONE] Complete Existing Frontend Attachment UX -- real File-backed
      draft state (`DraftImageAttachment`), local `URL.createObjectURL`
      preview, real `POST /api/sessions/{id}/attachments` upload wired
      into the picker (`ComposerPlusMenu`) and clipboard paste
      (`PromptComposer`) through one central ingestion path
      (`queueImageFiles` in `src/state/AppState.tsx`), per-chat backend
      session single-flight (`ensureBackendSession`) so concurrent
      images/pastes before a session exists cause exactly one
      `POST /api/sessions`, PENDING/UPLOADING/READY/FAILED draft states
      (frontend-only, never persisted as such), remove/retry with
      per-upload `AbortController` (a stale completion after removal
      never resurrects the removed attachment), a 4-image / 8 MiB
      frontend preflight (`src/lib/constants.ts`) with a visible,
      auto-dismissing "Up to 4 images can be attached." notice
      (`draft.attachmentLimitNotice`) whenever a selection/paste is
      rejected purely for exceeding that capacity -- never silent, never
      an alert()/modal -- and safe backend-errorCode -> user-message
      mapping (`src/lib/attachmentError.ts`). Validated end-to-end against
      real Cloud SQL PostgreSQL (`slopanoc`) + the real private GCS bucket
      via a live picker upload and a live clipboard (Ctrl+V) paste, both
      through the real backend -- not just component/unit tests. Real
      image Send remained INTENTIONALLY BLOCKED in B3 (B5 didn't exist
      yet) -- `PromptComposer`'s `canSend` and `AppState`'s `sendMessage`
      both hard-blocked whenever any image-kind attachment was present, in
      any state; no fallback to a text-only or fabricated send. SUPERSEDED
      BY B5 (see below): Send is now enabled for a `ready` image on the
      real backend branch.
      `NoAnswerNotice.tsx`'s old, separate metadata-only file-attach
      affordance was removed (not routed to the real pipeline) -- there
      is exactly one attachment entry point now. Removing an
      already-UPLOADED attachment from the draft does NOT delete its GCS
      object/Cloud SQL row -- it is left as a READY, unlinked
      (`message_id IS NULL`) orphan candidate for future retention
      cleanup, per B1/B2's existing architecture; B3 intentionally adds
      no DELETE endpoint or synchronous cleanup. No backend file was
      touched by B3. No `attachment_ids` on message-send, no
      Gemini/ADK/`Part.from_uri` wiring, no rehydration -- all still B4/B5.
  B4  Saved Conversation / Attachment Rehydration -- broken into
      B4A [DONE] Architecture + ADK event audit (backend/api/chat_service.py's
      `_active_events`/`is_final_response`/rewind mechanics traced against
      the installed ADK 1.33.0 source; locked the turn_id/message_id
      identity split, the tri-state `has_visible_message` marker design,
      and the "no schema migration" conclusion -- see
      docs/ or this file's own B4A/B4B implementation history for the
      full audit).
      B4B [DONE] Backend safe session-list/history API:
      `backend/api/session_state_keys.py` (NEW) -- plain, unprefixed,
      session-scoped state keys (`has_visible_message`, `chat_title`,
      `chat_activity_at`; never `app:`/`user:`/`temp:` -- those are
      reserved ADK scopes with cross-session/non-durable semantics,
      verified against the installed source), `derive_chat_title` (ports
      the frontend's own `deriveChatTitle` rule exactly), and
      `record_user_turn_activity` -- fires on EVERY genuine user turn.
      The first event `chat_service.py`'s `_run_turn_events` observes
      from `Runner.run_async` is a source-proven happens-before point
      (the real user-content event is always already durably appended by
      then), but SLOPANOC defers its OWN call to
      `record_user_turn_activity` until the active `Runner.run_async`
      invocation has fully terminated (from `_run_turn_events`'s own
      `finally` block), never while it is still yielding further events
      for the same invocation -- a live production incident (documented
      below, under the B4B runtime defect fix) proved that an external
      `get_session()`/`append_event()` call made mid-run races ADK's own
      session-revision staleness tracking and breaks the Runner's own
      next internal append (e.g. a tool's function-response). A failed
      or cancelled assistant turn still leaves the session correctly
      marked/advanced, because the deferred write still runs from
      `finally` after a failure -- it is simply never issued while the
      Runner is still active. Writes `has_visible_message`/
      `chat_title` ONLY on the session's first-ever genuine turn; writes
      `chat_activity_at` -- the REAL persisted user `Event.timestamp` for
      that turn, never wall-clock time -- on every turn.
      `ApiSessionService.create_session` now initializes
      `has_visible_message=False` on every new session (tri-state: `True`
      known-real, `False` known-empty, ABSENT legacy/pre-B4B -- bounded
      per-request backfill, never permanent N+1).
      CORRECTION PASS (locked): sidebar ordering/`updated_at` use
      `chat_activity_at`, NEVER ADK's own generic `Session
      .last_update_time` -- that field also moves on unrelated state-only
      writes this codebase already performs (approval, selection, Teams,
      Case, a manual rename, or this module's own legacy-marker repair),
      which would otherwise incorrectly bump an untouched old conversation
      to the top of the list. `rename_session` writes ONLY `chat_title`,
      never `chat_activity_at`. The saved-chat list algorithm classifies
      EVERY session from its already-loaded state first (known-TRUE,
      known-FALSE, or needs-repair) BEFORE any limit is applied, so
      `SLOPANOC_SAVED_CHAT_LIST_LIMIT` can never let enough empty/unknown
      sessions hide an older real saved conversation; the same limit
      bounds only the per-request REPAIR budget (event reads), which is
      self-terminating across requests, never the visibility
      classification itself. A known-visible session found missing only
      `chat_activity_at` (a rare edge case) is still always listed
      (`Session.last_update_time` as a documented, temporary sort
      fallback) and repaired from its latest still-ACTIVE user turn.
      `backend/api/session_history_service.py` (NEW) -- `GET /api/sessions`
      (owner-scoped saved-chat list, sorted by `chat_activity_at`),
      `GET /api/sessions/{id}/history` (safe transcript -- reuses
      `chat_service.py`'s existing, already-tested `_active_events`/
      `_extract_final_text`/`_non_thought_text` verbatim, never a second
      rewind/text-extraction implementation; excludes function calls/
      responses/thought parts/state-only events/rewound branches; ONE
      ownership-scoped `AttachmentService.get_for_owner_session` query per
      request, never one per message), `PATCH /api/sessions/{id}` (durable
      manual rename -- the existing frontend `RENAME_CHAT` reducer is
      local-only and would otherwise be silently overwritten by a derived
      title on the next refresh; explicit validation, never silent
      truncation, never touches `chat_activity_at`). Locked identity
      model: `turn_id` = ADK `invocation_id`; `message_id` =
      `f"{turn_id}:user"`/`f"{turn_id}:assistant"` (NOT ADK's own
      `Event.id`, which is never observable by this backend without an
      extra round trip -- see the B4A audit). Locked attachment semantic:
      `slopanoc_chat_attachments.message_id` stores the owning TURN's
      `invocation_id` (documented in `backend/attachments/models.py`),
      never the frontend-visible history `message_id` -- no schema
      change. No frontend change, no Gemini/ADK multimodal wiring, no
      `attachment_ids` on send -- all still B4C/B4D/B5. Real Cloud SQL
      PostgreSQL smoke proven (both the original pass and the correction
      pass): session creation, empty-session exclusion, a real
      conversational turn becoming visible with a derived title and
      activity timestamp, a rename leaving activity/ordering untouched,
      an unrelated state-only write leaving activity/ordering untouched,
      safe history projection, durable rename, survival across a
      simulated backend restart, and a real `Runner.rewind_async` call
      correctly narrowing history AND activity to the active branch --
      all against the real `slopanoc` Cloud SQL database, all cleaned up
      afterward.
      B4B RUNTIME DEFECT FIX -- the first live Gemini/Vertex smoke (a
      function-call tool turn) failed twice with a generic "could not
      complete this request" and no model continuation. Root-caused
      (evidence-first, no code changes before the cause was confirmed) by
      direct inspection of installed `google-adk==1.33.0`'s
      `Runner.run_async` event loop and `DatabaseSessionService
      .append_event`'s session-revision staleness check, confirmed via a
      disposable local reproduction against a real
      `DatabaseSessionService`, and corroborated by read-only inspection
      of the real failed Cloud SQL session (only the durable user event
      plus a bookkeeping event per attempt were ever persisted -- no
      function-call/model event, consistent with the Runner's own next
      append failing before it could be written). Fixed by deferring the
      `record_user_turn_activity` call to post-run finalization (see
      above), reusing the same re-fetch-then-write pattern already proven
      in this codebase for the same bug class
      (`_reload_and_persist_cleanup_delta`). Covered by
      `backend/tests/test_chat_service_function_call_continuation.py`
      (function-call continuation, no-final-answer, and multi-turn
      regressions, plus two tests proving the underlying ADK mechanism
      directly), the full backend suite (2557 passed, 1 skipped -- the
      prior 2552/1 baseline plus these 5 new tests), and a real Cloud SQL
      fixture proving the post-run write persists and survives a
      simulated restart.
      B4B FINAL LIVE CLOSURE -- the retry succeeded end-to-end against
      the real stack (session `606a7ba1-da70-4131-b4d2-f038ebc84dc9`):
      real Vertex Gemini 2.5 Flash returned the requested reply with
      normal model-call -> assistant-text -> generation-complete ->
      source-requirements remediation -> run-complete behavior and no
      recurrence of the earlier generic failure; `GET /api/sessions`
      listed it with the correct derived title and `chat_activity_at`-
      based ordering above older sessions; `GET
      /api/sessions/{id}/history` returned exactly the two visible
      messages with correct `{turn_id}:user`/`{turn_id}:assistant`
      identity, real timestamps, and no tool/function/internal event
      leakage; `PATCH /api/sessions/{id}` renamed the title while leaving
      `updated_at` exactly unchanged; a full backend process restart
      preserved session visibility, the manual title, and
      `chat_activity_at` identically, including on a second history read.
      The earlier defect-evidence session (`6dd9fad5-...`) was left
      intact, read-only, not deleted. **B4B is DONE.**
  B4C [DONE] Frontend saved-chat list + lazy transcript hydration. Server
      remains authoritative -- no localStorage/sessionStorage/IndexedDB
      persistence added. `AppStateProvider` fetches `GET /api/sessions`
      once at boot and merges each summary into the sidebar
      (`Chat.id = Chat.backendSessionId = session_id`, general workspace,
      `historyHydrationStatus: "unloaded"`), deduped by
      `backendSessionId` (never `Chat.id` equality) so a StrictMode
      double-invocation can never duplicate a chat; `activeChatId` is
      never touched (no auto-open). Backend order (already newest-
      activity-first) is preserved by appending hydrated ids to
      `chatOrder`, reusing `sortChatsForDisplay`'s existing pin logic
      unchanged. Transcript history is fetched lazily -- only on open
      (`GET /api/sessions/{id}/history`), tracked via
      `Chat.historyHydrationStatus` (`"unloaded" | "loading" | "loaded" |
      "error"`, deliberately not inferred from `messageIds.length` --  a
      real failed turn can legitimately look empty otherwise); a late
      response can neither resurrect a deleted chat nor overwrite a real
      local turn started while the fetch was in flight (defensive
      messageIds.length guard); failure is retryable
      (`retryHistoryLoad`), never fabricates a placeholder assistant
      reply. `turn_id` is deliberately NOT stored on the frontend
      `Message` model -- edit/rewind already worked (regression-tested)
      by counting preceding `role === "user"` entries positionally, so
      there is no consumer for it. History-response `attachments` are
      typed but not mapped onto any `Message` -- persisted attachment
      rendering remains B4D. Resuming a hydrated chat needs no special
      code: `sendMessage`'s existing `chat.backendSessionId` reuse means
      a normal send never re-calls `createSession()`. Real-chat rename
      calls `PATCH /api/sessions/{id}` whenever `backendSessionId` is
      set (hydrated OR a chat that got a session from its own first
      send); success applies the server-echoed title, failure keeps the
      prior title and surfaces the real safe `ApiError` message (same
      fallback pattern as `editMessage`'s own rewind-failure path), and a
      per-chat request token discards a stale response so a rapid
      double-rename can't let an older reply win. Mock/project/demo
      chats (no `backendSessionId`) keep the original synchronous
      local-only rename. B4B's DELETE/pin/unread non-guarantees are
      unchanged -- no backend DELETE/pin/unread persistence exists;
      deleting a hydrated chat only removes it locally this session.
      CORRECTION PASS (locked): the boot `GET /api/sessions` failure path
      previously only logged a DEV-only console.debug, leaving the
      sidebar's Chats section indistinguishable from a genuinely empty
      account during a real network failure. Fixed with a new GLOBAL (not
      per-chat) `AppState.savedChatsHydrationStatus: "loading" | "loaded"
      | "error"` (+ `savedChatsHydrationError`), separate from any one
      chat's own `historyHydrationStatus` -- starts `"loading"`, settles
      to `"loaded"` on any successful response including a genuine
      zero-session one (never confused with loading/error), or to
      `"error"` with a safe message, which never touches
      `chats`/`chatOrder` (a failed/retried fetch never erases an
      existing local chat). Sidebar shows "Loading chats..." only while
      unknown-and-empty, "Couldn't load saved chats" + "Retry" on
      failure (no modal/alert/raw ApiError), "No chats yet" only once
      genuinely loaded-and-empty. `retrySavedChats()` resets to
      `"loading"` and reuses boot's own fetch/merge function; repeated
      retries can't duplicate a chat (unconditional `backendSessionId`
      dedupe). The boot single-flight ref is boot-scoped only -- never
      blocks an explicit retry, including right after a StrictMode
      double-invocation (regression-tested).
      Covered by `src/api/sessions.test.ts`,
      `src/state/AppState.savedChats.reducer.test.ts` (+ this pass's
      loading/loaded/loaded-empty/error/retry-dedupe cases), and
      `src/state/AppState.savedChats.integration.test.tsx` (boot
      hydration incl. StrictMode double-invocation, lazy fetch,
      resume-saved-chat, rename success/failure/race, edit/rewind
      regression against a real hydrated two-turn fixture, + this pass's
      boot-loading/boot-failure-preserves-local-chats/retry-recovery/
      failed-retry/StrictMode-then-retry cases) -- full frontend suite
      and `npm run build` both green, zero regressions.
      FINAL LIVE CLOSURE -- a real browser validation pass against the
      full real stack (React -> FastAPI -> ADK DatabaseSessionService ->
      real Cloud SQL PostgreSQL, after a genuine VS Code/backend/frontend
      restart) confirmed all of it end to end: a hard refresh restored
      the real saved-chat list from Cloud SQL ("B4B Live Persistence
      Test," the earlier B4B failed-session chat, and "Hello"); opening
      "B4B Live Persistence Test" lazily loaded and rendered exactly its
      real two-message transcript with no fabricated assistant reply and
      no raw tool/function/internal event leakage; renaming it from the
      real UI to "B4C UI Rename Test" then hard-refreshing showed the
      renamed title surviving durably via `PATCH
      /api/sessions/{backendSessionId}` into Cloud SQL -- proving
      real-chat rename is no longer local-only; reopening the renamed
      chat afterward reloaded the exact same transcript again, proving
      the rename never detached the underlying session identity.
      **B4C is DONE.**
  B4D [DONE] Attachment-reference hydration + secure persisted
      image rendering + restart/rewind/live validation. Does NOT send
      images to Gemini and does not touch model input in any way (B5,
      untouched) -- the B3 image Send gate remains fully closed. History
      DTO `attachments` (typed since B4C, deliberately unmapped) now map
      onto a NEW `Message.persistedAttachments` field -- metadata only
      (`attachmentId`/`filename`/`mimeType`/`sizeBytes`), never a
      File/Blob/objectUrl, never merged with the existing mock
      `attachments` field. Binary rendering reuses the existing,
      unmodified B2 `GET /api/attachments/{id}/content` route
      (authenticated + ownership-checked, anti-enumeration -- unknown and
      foreign-owner attachments return the identical generic SafeError)
      through one new `client.ts` helper (`getBlob`) and one new
      `api/attachments.ts` function (`getAttachmentContent`) -- no new
      backend route, no GCS/bucket/signed-URL knowledge anywhere in the
      frontend (verified: no `gs://`/`storage_object_name`/`bucket`/
      `sha256`/`owner_user_id` string anywhere in frontend source). A new
      self-contained `PersistedImageAttachment` component (no AppState
      dependency, no global binary cache) does fetch-on-mount -> Blob ->
      `URL.createObjectURL` -> `<img>` -> revoke-on-replace/unmount,
      rendered additively inside `Message.tsx`'s existing user-message
      branch. An unsupported MIME type never fetches at all -- a safe
      "Unsupported format" state. A content-fetch failure never fails the
      transcript, `historyHydrationStatus`, or any sibling image -- only
      that one image's own safe "Image unavailable" + Retry (regression-
      tested: a real image failure alongside real history success leaves
      `historyHydrationStatus: "loaded"` untouched). No content binary is
      ever fetched merely because a chat is summarized at boot or because
      history loads -- only once an actual `<img>`-bearing message is
      actually rendered. Edit/rewind needs zero code changes -- discarding
      a later turn already deletes its message (and therefore its
      `persistedAttachments`) wholesale via existing reducer logic, and
      the corresponding renderer simply unmounts, triggering its own
      abort/revoke cleanup automatically (regression-tested against a
      real two-turn hydrated fixture where the discarded turn owns an
      image). Covered by `src/api/attachments.test.ts`,
      `src/state/AppState.savedChats.reducer.test.ts` (DTO mapping),
      `src/components/conversation/PersistedImageAttachment.test.tsx`
      (loading/success/cleanup/StrictMode/failure+retry/unsupported-MIME/
      multiple-instance isolation), and
      `src/components/conversation/Message.persistedAttachments.test.tsx`
      (real AppStateProvider-backed: history/image-failure boundary,
      lazy content-fetch network boundary, successful render, edit/rewind
      ghost-attachment regression) -- full frontend suite and
      `npm run build` both clean, zero regressions.
      LIVE IMAGE HYDRATION VALIDATED -- real disposable session
      `78a5b7c8-a675-4bb1-9372-b3fa0d641cdc`, real turn
      `e-4faba7ae-82f1-4d82-9cf2-b30a12824f2c`: a real PNG (`00001.PNG`,
      image/png, 180337 bytes) was uploaded through the unmodified real
      `POST /api/sessions/{id}/attachments` route (real GCS write + Cloud
      SQL READY row), then linked via the existing
      `AttachmentService.link_to_message` in a one-off, disposable,
      never-committed local invocation (READY -> LINKED, message_id =
      the real ADK turn_id) -- no production route added, no fixture
      code committed, no backend behavior changed. `GET /history`
      returned exactly `{attachment_id, filename, mime_type, size_bytes}`
      on the owning USER message only (assistant attachments empty), no
      gs:///bucket/storage/owner metadata exposed. After a hard refresh,
      the real browser rendered `00001.PNG` inside the owning user
      message end to end: Cloud SQL reference -> GET /history ->
      PersistedAttachmentReference -> authenticated GET
      /api/attachments/{id}/content -> private GCS binary -> Blob ->
      transient object URL -> real <img> -- no direct GCS access, no
      signed URL, no public bucket, no base64, no durable browser
      storage.
      CORRECTION PASS (locked) -- image-bearing user turns are
      intentionally non-editable. A user turn that owns a durable,
      server-linked image reference owns evidence tied to its original
      ADK turn/invocation; a text-only edit/rewind of that prompt while
      silently retaining/dropping/re-associating the image would create
      ambiguous attachment semantics this product has not designed yet.
      Multimodal edit/rewrite semantics are not defined. The single
      authoritative signal (`src/lib/persistedAttachments.ts`'s
      `hasPersistedImageAttachment`) is `message.persistedAttachments`'s
      presence -- never inferred from text/filename/regex/DOM. Enforced
      twice: `Message.tsx`'s `UserMessageActions` hides the Edit action
      entirely for such a message, and -- the real boundary --
      `AppState.tsx`'s `editMessage` hard-guards on the same signal
      before any side effect (no rewindSession, no createSession, no
      text mutation, no dispatch), so the prohibition holds for any
      future/programmatic caller, not only the UI. Text-only user
      messages are completely unaffected -- existing edit/rewind behavior
      unchanged, regression-tested. B5 INVARIANT, NOW FULFILLED (see the
      B5 entry below): once a live-sent turn can carry a real image, the
      frontend Message representing it must expose this same
      `persistedAttachments` (or an equivalent structured image-ownership)
      signal immediately, in the same turn -- not only after a later
      reload -- so this prohibition holds both before and after refresh;
      B5 does exactly this, live-proven. FINAL LIVE PROOF (B4D correction
      pass) -- after the correction
      pass, a real hard refresh reopening "B4D attachment hydration
      fixture" confirmed the persisted image still renders, the original
      user text still renders, the image-bearing user prompt has no
      usable Edit action, and text-only user messages elsewhere retain
      normal Edit behavior -- no rewindSession call for the blocked
      direct-edit attempt. **B4D is DONE.**
  B5  [DONE] Gemini/ADK Multimodal Runtime. First milestone where a user can
      actually send an image to Gemini. `SendMessageRequest` gained
      `attachment_ids: list[str]` (default `[]`, backward-compatible);
      `message` now optional (default `""`) for image-only sends, gated
      by a `model_validator` ("message or attachment_ids required") ->
      the same 400 SafeError shape a malformed request already got.
      Every attachment id re-validated server-side before the Runner
      starts (`attachment_service.prepare_attachments_for_turn`):
      existence, ownership, session, READY-only (no LINKED/DELETED
      replay), MIME (SUPPORTED_MIME_TYPES), duplicate-id rejection,
      count/total-bytes limits from existing settings (no drifting
      duplicate constants) -- unknown/foreign-owner/foreign-session stay
      anti-enumeration-identical. Internal gs:// URI built ONLY on the
      backend (`ChatAttachmentStorage.uri_for`), never reaches any
      DTO/SSE/log/frontend. `Part.from_uri` exclusively -- `Part.from_
      bytes` proven never called (patched + asserted). LINKAGE TIMING,
      audited not guessed: READY->LINKED happens INLINE at the first
      Runner-yielded event (same point B4B's deferred write already
      proved the turn is durable) via `AttachmentService.link_many_to_
      message` -- a plain SQLAlchemy UPDATE on the SEPARATE
      `slopanoc_chat_attachments` table, never through `session_service
      .append_event()`. Direct inspection of installed ADK 1.33.0
      (`StorageSession.get_update_marker()`, schemas/v1.py) proved the
      session-revision marker is derived exclusively from the `sessions`
      table's own `update_time` -- this write can never touch it. A
      dedicated regression test reproduces the exact B4B production
      shape (function-call -> tool -> final text) with a real attachment
      linked at that point against a real file-backed
      DatabaseSessionService and proves no staleness error -- B5 does
      NOT resurrect the B4B defect. Link failure after a genuine turn
      fails closed (attachment stays READY, never fraudulently LINKED).
      BUG FOUND + FIXED during this pass' own audit:
      session_history_service.py's turn projection previously dropped an
      image-only turn entirely (`_user_text` returning None was treated
      as "not genuine") -- fixed; image-only turns now appear with
      text="" and count for has_visible_message/chat_activity_at/legacy
      repair; title still correctly falls back to "New chat"; gs:// URI
      still never exposed via GET /history (regression-tested).
      FRONTEND: Send/Enter enabled for a `ready` image only on the real
      backend branch (mirrors sendMessage's own isBackendBranch
      conditions -- never silently mocks/drops an image elsewhere). The
      LOCKED B4D invariant now holds IMMEDIATELY: a just-sent image
      message gets `persistedAttachments` synchronously from the
      draft's own upload metadata, so the existing edit-prohibition
      applies at once (regression-tested), never only after refresh.
      Regenerate needed no change -- already unconditionally hidden for
      every backend message (pre-existing Phase 4F decision, confirmed
      still correct on audit). B5/B6 BOUNDARY (superseded by B6, see
      below): at B5 close, only the TOP-LEVEL Team Manager Runner was
      multimodal -- a nested AgentTool call (Incident Manager) did NOT
      yet inherit the image. B6 closes exactly that gap
      (`MultimodalAgentTool`). No keyword routing added; no GCS deletion
      lifecycle, no persistent pin/unread (still B7). Covered by
      `backend/tests/test_attachments_repository.py`,
      `test_attachment_prepare_for_turn.py`,
      `test_chat_service_function_call_continuation.py` (the mandatory
      B4B non-regression + Content-construction proofs),
      `test_session_history_service.py`, `test_api_streaming_endpoint.py`,
      and frontend PromptComposer.test.tsx/AppState.attachments.test.tsx/
      streamChat.test.ts -- full backend (2587 passed, 1 skipped) and
      frontend (662 passed) suites clean, `npm run build` clean.
      LIVE MULTIMODAL VALIDATION PASSED -- real disposable session
      `469f7cad-f60b-4a67-b079-ba52cc1bed90`, real turn
      `e-52df5f58-0d5b-4c50-be0e-5333c93c4de4`: real PNG (`image.png`,
      image/png, 119629 bytes) attached through the real UI, Send enabled
      automatically once READY, pressed normally -- no manual backend
      command anywhere. Real Vertex Gemini correctly read "Fixed Access
      and SDH - DMs status" directly from the image's own pixels (never
      inferable from filename/prompt/metadata) -- conclusive proof of
      real Part.from_uri multimodal input, never OCR/base64/
      Part.from_bytes/a public or signed URL/prompt simulation. READY ->
      LINKED happened automatically as part of this same real send.
      GET /history confirmed the attachment on the user message only
      (assistant, same turn_id, attachments: []), exposing only
      {attachment_id, filename, mime_type, size_bytes} -- no gs:///
      bucket/storage_object_name/owner_user_id/sha256. Before refresh:
      persistedAttachments already present, Edit already unavailable.
      After a hard refresh: text/image/answer all restored identically,
      Edit still unavailable -- before-refresh and after-refresh
      semantics proven identical. A backend-restart persistence check
      was not separately exercised in this pass (B4B/B4D already proved
      that persistence class for text/history/rename; not re-confirmed
      live here for B5's own linkage mechanism specifically).
      **B5 is DONE.**
  B6 [DONE] Image + Teams + KM Operational Reasoning -- closes the
      B5/B6 boundary gap: a nested `incident_manager` AgentTool call now
      genuinely receives the SAME trusted current-turn image evidence
      Team Manager sees, without making image identity model-controlled.

      ADK AUDIT (installed 1.33.0 source, verified before implementing):
      `AgentTool.run_async` builds the nested `Content` ONLY from
      `input_schema.model_validate(args).model_dump_json(...)` -- the
      calling invocation's own original multimodal `Content` is never
      consulted. Separately verified: `tool_context.user_content`
      (`ReadonlyContext.user_content`, a PUBLIC, documented property) IS
      exactly team_manager's own top-level `Content` for this turn
      (`Runner.run_async`'s `new_message` becomes `invocation_context
      .user_content` -- `runners.py`, `_new_invocation_context`) --
      already-isolated per-invocation, no registry needed for this call
      site.

      PROPAGATION MECHANISM -- `backend/agents/team_manager/multimodal_
      agent_tool.py`'s `MultimodalAgentTool(AgentTool)`: a narrow
      subclass (Option A, no ADK internals monkeypatched) overriding only
      `run_async` -- identical to the base implementation except that
      trusted `file_data` `Part`s from `tool_context.user_content` are
      appended, in order, after the structured-request text part, before
      the nested Runner starts. `incident_manager_tool` in team_manager/
      agent.py now constructs `MultimodalAgentTool(agent=_fast_path_
      incident_manager)` in place of the base `AgentTool` -- same name,
      schema, nested call/return model, output validation, state-delta
      forwarding; zero behavior change for a text-only turn (proven by
      test).

      RUN-SCOPED TRUSTED IMAGE IDENTITY -- `backend/api/multimodal_turn_
      context.py`: a SEPARATE, narrower mechanism from the `Part`
      propagation above, needed only where `attachment_id` itself (never
      recoverable from a `Part.from_uri`, which carries only file_uri/
      mime_type) must be captured for a LATER turn -- `tools/teams/
      list_chats.py`'s ambiguous-with-candidates branch, when creating a
      `PendingSelection`. In-process, run_id-keyed (reuses `turn_context
      .current_run_id()`, the same already-proven correlation ContextVar),
      registered by chat_service.py immediately after B5's own attachment
      validation succeeds, discarded in the SAME `finally` block that
      already cleans up the Teams-snippet mailbox/run_id -- never ADK
      session state, never persisted, cleared on every exit path
      including CancelledError.

      DIRECT FAST-PATH STRUCTURAL BYPASS -- `direct_read_fast_path.py`'s
      `_capture_unique_match_for_fast_path` gained `_has_image_evidence`
      (mirrors the existing `_requires_governed_knowledge` gate exactly):
      when the nested invocation's own `user_content` carries a
      `file_data` part, the fast-path marker is never set, so incident_
      manager's own REAL model turn always runs for an image-bearing
      delegation -- proven end to end (a real ADK Runner, real gateway
      mock) that the shortcut does NOT engage and the resulting second
      model call genuinely still has the image in its own `llm_request
      .contents`. Text-only fast-path behavior is completely unchanged
      (regression-tested).

      SELECTION-CONTINUATION IMAGE PRESERVATION (instruction's hardest
      requirement) -- `PendingReadIntent`/`ResolvedReadContinuation`
      (selection/schemas.py) each gained `attachment_ids: list[str]`,
      server-captured only, never model/frontend-supplied:
      `list_chats.py`'s ambiguous branch populates it from `multimodal_
      turn_context.current_run_image_attachment_ids()`; `api/selection_
      service.py`'s `choose()` copies it verbatim into the
      `ResolvedReadContinuation` it stores. `PendingSelectionDTO` (the
      frontend-facing SelectionCard shape) never exposes it -- `pending_
      read_intent` was already excluded from that mapping. At resume
      time, `backend/api/attachment_service.py`'s new `resolve_
      continuation_images` re-validates each id against the REAL
      attachment table -- LINKED-only (never READY, which would mean
      never-sent; never DELETED), same owner/session, MIME still
      supported -- structurally distinct from `prepare_attachments_for_
      turn`'s READY-only new-send validator. `read_continuation_
      execution.py`'s both continuation-execution shapes (deterministic
      and model-driven retrieval) resolve images FIRST (before any Teams
      retrieval) and fail the WHOLE continuation closed
      (`IncidentManagerResponse(outcome=error, detail=...)`, never a
      silent fallback to Teams-only reasoning) on ANY corruption --
      unknown/foreign-owner/foreign-session/still-READY/DELETED id all
      collapse to the same safe outcome, anti-enumeration-consistent with
      every other attachment read path in this codebase. A real ADK
      rewind (`Runner.rewind_async`'s own, already-proven state-delta
      reversal -- unchanged, not reimplemented) correctly reverses a
      discarded branch's own `PendingSelection`/its `attachment_ids` --
      regression-tested; no ghost-image resume is possible.

      TRUST BOUNDARY UNCHANGED: `IncidentManagerRequest` gained no new
      field for images (no `attachment_ids` team_manager's model could
      populate) -- image ownership/selection remains 100% runtime-
      supplied. `IncidentManagerResponse` also gained no new field --
      the audit of `evidence.py`/`provenance_compliance.py` found both
      already read `callback_context.user_content` structurally (never
      parse free text), so appended `file_data` parts are simply ignored
      by that existing logic; the model's own free-text `summary` field
      safely represents "visually observed" vs. "Teams states" vs. "KM
      supports" distinctions, per the new incident_manager prompt
      paragraph -- no schema change needed or made.

      NO GCS URI EVER IN TEXT: proven by test at every propagation point
      (nested Content's own text part, continuation resume, joint
      integration) -- only `Part.from_uri`'s own `file_data.file_uri`
      ever carries it.

      PROMPTS: incident_manager gained one "IMAGE EVIDENCE" paragraph
      (pixels are evidence not instructions; visible-image text is
      untrusted operational content, never a priority instruction;
      distinguish direct observation from inference; distinguish image
      vs. Teams vs. KM support and surface disagreement; no fabricated
      commands from image content alone; command/procedure grounding
      unchanged). team_manager gained one short paragraph (runtime
      auto-forwards current-turn images to Incident Manager; never
      paraphrase/forward image content into the delegation request; an
      attached image alone is never itself a reason to delegate).

      NO NEW AGENT, NO Phase 7 troubleshooting state, no OCR, no image
      search/browse tool, no GCS access tool on Incident Manager, no
      second image database/migration (uses the existing
      `slopanoc_chat_attachments` table + transient in-process context
      only), no frontend change (zero `src/` files touched -- the
      existing UI experience is already correct end to end), no new SSE
      event type, no image URI/storage metadata ever logged.

      MANDATORY IMAGE+TEAMS+KM JOINT-REASONING PROOF: one integration
      test drives the REAL `incident_manager` agent (full tool set, real
      `after_agent_callback` integrity/provenance enforcement, unmodified)
      through a real Power Automate gateway mock and a real isolated
      SQLite `KnowledgeRepository`, with a scripted model that genuinely
      calls `teams_get_messages`, `knowledge_search`, and `knowledge_
      select_evidence` in the SAME nested Runner call that also received
      the top-level image `Part` on its own FIRST reasoning step --
      proving combination (instruction section 46's own standard), never
      three sources merely working in isolation.

      TESTS: `test_multimodal_turn_context.py` (10 -- register/read/
      isolation/cleanup/two-interleaved-runs), `test_multimodal_agent_
      tool.py` (5 -- real ADK Runner/Agent, text-only unaffected, image
      order preserved, no fabricated text, output-schema validation
      still applies, state-delta forwarding still works),
      `test_p5_1_b6_fast_path_image_gate.py` (3 -- unit gate + real
      end-to-end second-model-call-genuinely-sees-the-image proof),
      `test_p5_1_b6_selection_continuation_images.py` (11 -- capture,
      DTO no-leak, choose() propagation, real LINKED-image resolution,
      5 parametrized fail-closed corruption cases, real ADK rewind ghost-
      image regression), `test_p5_1_b6_image_teams_km_integration.py`
      (1 -- the mandatory joint-reasoning proof above). 30 new tests, all
      passing. Full backend suite: 2617 passed, 1 skipped (2587 B5
      baseline + 30 new; 2 pre-existing prompt-size assertions updated
      for the new prompt paragraphs' legitimate length growth, zero
      logic regressions). Standalone KM-tagged subset: 846 passed. Teams-
      tagged subset: 291 passed. Frontend: zero files touched; a focused
      selection/streaming contract subset (30 tests) re-run clean to
      confirm no API-contract drift; full frontend suite/`npm run build`
      not re-run (nothing frontend changed).

      MULTIMODALAGENTTOOL VERSION-SENSITIVITY MAINTENANCE NOTE:
      `MultimodalAgentTool` is a narrow subclass audited against
      installed `google-adk==1.33.0`'s `AgentTool.run_async`, which it
      mirrors line-for-line except for the one image-appending insertion
      point (see multimodal_agent_tool.py's own module docstring). This
      is an ADK-VERSION-SENSITIVE adapter, not a current defect: any
      future `google-adk` upgrade MUST trigger a re-audit, before
      assuming continued equivalence, of -- `AgentTool.run_async` itself,
      nested Runner construction, nested Content construction, state-
      delta forwarding, output-schema validation, event/result
      extraction, and cleanup semantics. If any of these change upstream,
      `MultimodalAgentTool` must be re-diffed against the new base
      implementation before relying on it further.

      LIVE OPERATIONAL MULTISOURCE VALIDATION -- ALL FOUR PLANNED PATHS
      PASSED:

      Test A -- IMAGE + TEAMS (PASSED). Real Teams conversation
      "SLOPANOC Gateway Group Test" carried a Teams-only validation
      marker (`NORTHSTAR-4281`); the attached image carried a pixel-only
      fact ("RADIO DOT FOR MULTI OPERATOR") absent from all text. The
      live answer correctly attributed each fact to its own source, never
      conflating them. Backend trace, in order: team_manager model call
      -> incident_manager model call -> teams_list_chats -> Power
      Automate teams.listChats = ok -> `fast_path_skipped_has_image_
      evidence` -> incident_manager continued its own real reasoning ->
      teams_get_messages -> Power Automate teams.getMessages = ok ->
      incident_manager final result -> team_manager final visible answer
      -- direct, live confirmation that the direct/exact-match Teams fast
      path is structurally skipped whenever trusted image evidence
      exists, exactly the B6 invariant (`_has_image_evidence`,
      direct_read_fast_path.py). No gs:// URI was exposed. Real run
      `a2c52be5-0912-493a-9ade-b7275a474544`, real attachment
      `315e08cb-a26b-430c-8b3b-c1ef5cff27e2`.

      Test B -- IMAGE + GOVERNED KM (PASSED). Controlled test image
      visibly showed "AURORA RELAY STATUS", observed checksum 7318,
      observed status GREEN -- the image deliberately did NOT contain
      7319. Governed KM required checksum 7319/status GREEN plus an
      approved escalation procedure (collect observed values; escalate to
      platform owner; do not restart/reconfigure). Live result correctly
      concluded verification FAILED (7318 != 7319) and stated the
      approved next action. Backend trace: incident_manager ->
      knowledge_search -> knowledge_select_evidence -> incident_manager
      grounded result -> team_manager final result -- live proof that
      SEARCH RESULT != EVIDENCE USED held (selected evidence was
      genuinely exercised, not merely available). Real run
      `bec4d024-2efd-4804-ac09-96ae62c27be9`, real session
      `e5507802-11ad-4d32-b1a3-7559ad4e71c9`, real attachment
      `9bf55604-6fed-41bc-84d2-1c61b01e7d57`.

      IMPORTANT KM STORAGE ACCURACY (Tests B/C): the live validation
      shell had `SLOPANOC_KNOWLEDGE_DATABASE_URL` unset and no Knowledge
      Secret Manager fallback configured (`DatabaseUrlSet=True` but
      `KnowledgeDatabaseUrlSet=False`/`KnowledgeSecretSet=False`), so
      `resolve_knowledge_database_url()` correctly fell back to the
      documented zero-setup local SQLite path (`./slopanoc_knowledge.db`)
      -- NOT Cloud SQL. Tests B/C therefore used: the real SLOPANOC UI,
      the real backend, real Gemini/Vertex, the real private attachment
      runtime, the real Generic KM tool/runtime path, and a real governed
      `KnowledgeObject` repository -- populated with two APPROVED,
      controlled end-to-end test fixtures, both titled "Aurora Relay
      Verification Procedure": `E2E-KM-AURORA-001` v1 and
      `aurora-relay-verification` v1 (both `technical_instruction`/
      `approved`). This does NOT invalidate B6 -- the Generic KM
      repository contract is deliberately dialect-neutral, and B6
      validates the runtime/tool/reasoning contract, not a storage
      dialect. Do not claim Cloud SQL KM was used in Tests B/C. Do not
      imply A5 (real TELCO/RAN MOP ingestion) has started -- it has not.

      Test C -- IMAGE + TEAMS + GOVERNED KM (PASSED, the strongest B6
      multisource proof). Same image (checksum 7318, status GREEN); a
      real Teams message in "SLOPANOC Gateway Group Test" stating the
      designated platform owner is TEAM-ORION and that no remediation
      action has been approved; the same governed checksum/status
      requirement plus escalation procedure. Live UI correctly combined
      all three into one answer: verification fails because 7318 !=
      7319; escalate to TEAM-ORION; provide observed checksum/status; do
      not restart/reconfigure without further approval. Backend trace, in
      the SAME incident_manager execution: teams_list_chats ->
      teams.listChats ok -> `fast_path_skipped_requires_governed_
      knowledge` -> teams_get_messages -> teams.getMessages ok ->
      knowledge_search -> knowledge_select_evidence -> incident_manager
      final synthesis -> team_manager final answer -- direct live proof
      IMAGE + TEAMS + GOVERNED KM combine in one specialist reasoning
      path, not three sources merely working in isolation (instruction
      section 46's own standard). Real run
      `db5f5cf9-2f44-4985-96d4-b90a81c23d5d`, real session
      `d3336d3c-554a-45a6-bb02-c2c84cffc40c`, real attachment
      `73c8b130-6e27-446d-a3da-2f8a4b8aefa8`. An earlier attempt hit a
      test-setup issue (the expected TEAM-ORION Teams message was not yet
      retrievable) -- the system correctly reported it could not find
      that fact rather than fabricating one; test setup was corrected and
      the clean rerun above passed. Not a B6 defect.

      Test D -- SELECTION CONTINUATION PRESERVES ORIGINAL IMAGE (PASSED).
      An intentionally non-exact Teams name ("SLOPANOC Gateway Group")
      produced a real ambiguous result and a real SelectionCard; choosing
      "SLOPANOC Gateway Group Test" (`POST /api/sessions/{id}/selections/
      {id}/choose` -> 200 OK) resumed the read on a SECOND backend
      continuation. The user did NOT reattach the original image. The
      resumed response still correctly referenced checksum 7318 -- a fact
      that existed only in the ORIGINAL image -- proving the server-
      captured `attachment_ids` reference (never frontend/model-supplied)
      survived the SelectionCard boundary and was re-validated/re-
      attached automatically. Backend trace: selection choose ->
      messages/stream -> teams_evidence_prepared -> Power Automate
      teams.getMessages = ok -> incident_synthesis_start ->
      incident_manager model call -> incident_synthesis_complete ->
      read_continuation_executed -> trusted_result_presentation_mode ->
      team_manager final presentation. The selected Teams conversation
      itself contained no comparison message for the checksum -- expected
      and irrelevant to what Test D specifically proves (image-evidence
      preservation across the continuation boundary, not Teams content
      completeness). Real continuation run
      `69848cdc-cda9-48ca-83f0-15f4608c47a1`.

      LIVE VALIDATION SUMMARY: Test A PASSED. Test B PASSED. Test C
      PASSED. Test D PASSED. This closes B6's live-validation
      requirement.

      B7 FOLLOW-UP RECORDED (not implemented in this pass): Tests B/C's
      real UI rendered source-reference chips roughly (a visible "svg"
      artifact in the chip presentation), and Test C showed two governed-
      knowledge chips that looked similar. These are TWO DISTINCT
      observations, never to be conflated: (a) source-chip rendering/
      presentation polish is a genuine UI issue: (b) the two
      similar-looking KM chips are NOT a provenance defect -- the
      validation repository legitimately contains two approved, content-
      overlapping Aurora Relay fixtures, so two distinct governed source
      references may correctly be selected/displayed for the same
      answer. B7 should improve chip presentation/disambiguation without
      ever "deduping" backend provenance merely because two chips look
      alike -- a source reference may legitimately be distinct even when
      the underlying documents overlap in content.

      **B6 is DONE.**
  B7  Lifecycle + Real UI + Full Regression

LOCKED product model (B0, refined for durable resources): normal SENT
chat attachments are real saved conversation resources, not a
current-turn-only demo -- unsent draft = browser File/Blob only; sent
normal chat attachment = private GCS binary + Cloud SQL metadata/
reference, linked to the owning chat/user message; a future temporary/
incognito chat (not built) would be ephemeral-only; future incident
evidence and future governed-KM images (neither built) get their own
separate ownership/lifecycle, per the target design below.

LOCKED Gemini/ADK multimodal construction rule (B0, proven empirically
against installed `google-adk==1.33.0`/`google-genai==1.75.0`, both via
direct source inspection and a disposable local-SQLite `DatabaseSessionService`
experiment): `google.genai.types.Part.from_uri(file_uri="gs://...", ...)`
is the ONLY sanctioned construction for a durable chat image in model
input, because ADK's `DatabaseSessionService` persists only the small
`file_data.file_uri`/`mime_type` reference for it.
`Part.from_bytes(...)` is FORBIDDEN for this path -- proven to serialize
the full image as base64 directly into the `events.event_data` column.
Not yet wired into any live message-send path (that's B5) -- B1 only
preserves the architecture (no binary column anywhere in
`backend/attachments/models.py`).

Target image-storage design (future domains, not all built yet):
  temporary user screenshot (future incognito chat) -> no Cloud Storage
  sent normal chat attachment (B1+)                 -> Cloud Storage + Cloud SQL metadata
  persistent incident evidence (future)              -> Cloud Storage + Case/Fault ownership
  governed knowledge image (future, A5+)              -> Cloud Storage + KM ownership, separate lifecycle from chat attachments

A5 — REAL TELCO/RAN MOP INGESTION follows POST-5.1 B in full (not just
B1). A5 ingests the 3 real TELCO/RAN MOPs through the existing,
unchanged Generic KM pipeline (MOP → source adapter/import boundary →
IngestedKnowledgeDocument → processing → governance → KnowledgeRepository
→ Cloud SQL PostgreSQL) — a separate milestone from Attachments, not part
of it.

NEXT:

PHASE 5.1 — GENERIC KNOWLEDGE MANAGEMENT LAYER

5.1A Knowledge architecture + contracts
5.1B Metadata + applicability
5.1C Generic ingestion boundary
5.1D Content processing / structured segmentation
5.1E Versioning + lifecycle governance
5.1F Knowledge repository abstraction
5.1G Retrieval + ranking
5.1H Knowledge provenance
5.1I Generic agent-facing Knowledge tools
5.1J First reference consumer integration

5.1 is a GENERIC KM PLATFORM CAPABILITY. It is NOT a one-off MOP reader,
Incident-Manager-specific KM logic, a JOC implementation, or autonomous
execution. Initial knowledge object types may include MOP, SOP, RCA, KB
Article, Troubleshooting Guide, Operational Procedure, Technical
Instruction. The KM layer must remain independent from Incident Manager —
Incident Manager in 5.1J is only the first reference consumer, used to
validate the generic contract.

5.1A (domain foundation), 5.1B (metadata hardening + deterministic
applicability evaluation), 5.1C (the generic, source-agnostic ingestion
boundary contract), 5.1D (deterministic structural content
processing/segmentation), 5.1E (versioning + lifecycle governance),
5.1F (the generic knowledge repository contract, with a local SQLite
implementation), 5.1G (deterministic, generic retrieval + ranking —
a context-reduction boundary composing list_all/resolve_current_version/
evaluate_applicability unchanged, plus one lexical token-overlap
reference scorer), 5.1H (knowledge provenance — a deterministic
validation boundary that exactly revalidates a retrieval result against
the repository, reusing KnowledgeEvidenceReference as its authoritative
identity, before it becomes a trusted KnowledgeEvidenceSet), 5.1I
(one generic agent-facing knowledge_search tool composing the frozen
5.1G/5.1H services behind a closed model-controlled request contract,
splitting its result into a model-safe agent_payload and a separately-
retained trusted KnowledgeEvidenceSet, with as_of/ApplicabilityContext
supplied only by a trusted execution context, never the model), and 5.1J
(Incident Manager, the first reference consumer: concrete
knowledge_search/knowledge_select_evidence ADK tools live in
backend/tools/knowledge/ -- outside Generic KM itself -- with a
server-owned, run-id-keyed trusted evidence store; Team Manager is
unchanged and receives neither tool) are implemented. The detailed
contract is documented in docs/KNOWLEDGE_CONTRACT.md — read it before
extending backend/knowledge/domain/, backend/knowledge/ingestion/,
backend/knowledge/processing/, backend/knowledge/governance/,
backend/knowledge/repository/, backend/knowledge/retrieval/,
backend/knowledge/provenance/, backend/knowledge/tools/, or
backend/tools/knowledge/. Phase 5.1 is now complete; no concrete source
adapter (SharePoint/GCS/Drive/...) exists, and none is planned as part
of 5.1 -- the next phase is the locked-roadmap LOCAL GIT CHECKPOINT
followed by Phase 4H security hardening.

Then: LOCAL GIT CHECKPOINT

Then: PHASE 4H SECURITY HARDENING

4H.1 Trust boundaries + threat model
4H.2 Prompt-injection / untrusted external content isolation
4H.3 Tool authorization + output validation
4H.4 Sensitive-data / secret handling
4H.5 Model Armor integration
4H.6 Security audit events + safe failure
4H.7 Adversarial regression suite

Then: FULL REGRESSION + LIVE VALIDATION

Then: GITHUB CHECKPOINT

Then:

5.2 ITSM
5.3 Alarm / fault
5.4 Topology / inventory
5.5 KPI / observability
5.6 Change Management
5.7 Handover / operational context

Then:

Phase 6 Agent expansion
Phase 7 JOC / advanced troubleshooting
Phase 8 Controlled autonomy

===================================================================
DEVELOPMENT RULES FOR PHASE 5.1
===================================================================

- Build contracts before storage.
- Build generic KM before agent integration.
- MOP is a document type, not an architecture — do not design the KM layer
  around any single document type.
- Keep KM independently testable without Gemini.
- Do not let Incident Manager own KM logic.
- Do not let agents access raw database/storage directly — go through a
  tool/service boundary, the same way Teams access goes through
  tools/teams/, never a direct client in agent code.
- Enforce lifecycle/version/applicability deterministically, not by model
  judgment.
- Provenance must be validated against real retrieved evidence — the same
  discipline already enforced for Teams evidence, never relaxed for
  Knowledge.
- Semantic relevance alone is not enough to select a knowledge item for use.
- Approved/current/applicable knowledge must be favored for operational use
  over merely similar content.
- Vector/index storage must not become the system of record — it is a
  retrieval aid over a real repository, not the source of truth.
- External knowledge content must later be treated as untrusted data during
  Phase 4H — do not design 5.1 in a way that assumes ingested content is
  automatically safe to reason over unguarded.
- Do not introduce autonomous execution during 5.1.
- Phase 5.1 builds Knowledge Context capability — one of three future
  context domains (see "TARGET FUTURE ARCHITECTURE" above) — not a
  standalone feature bolted onto Incident Manager.
- Generic KM sits behind Knowledge Context; Knowledge Context is one input
  the future Context Engineering Layer would assemble alongside
  Operational Context and Case Context. Do not design 5.1 as if Generic KM
  were a peer data source alongside Teams/Cases, and do not design it as
  if Context Engineering already existed as a runtime service.
- Do not create one-off MOP/SOP tooling — every knowledge object type goes
  through the same generic architecture/contracts, never type-specific
  shortcuts.
- Do not add the Troubleshooting Manager during 5.1A — it is FUTURE (see
  "TARGET FUTURE ARCHITECTURE" above), not part of this phase.
- Do not add the Head of Automated Operations during 5.1A — it is FUTURE,
  not part of this phase, and never a mandatory hop in the current runtime.
- Do not add future operational integrations (ITSM, Alarms, Topology,
  KPIs, Change, Handover) early — those are Phase 5.2–5.7, not 5.1.
- Preserve the current frozen runtime architecture throughout 5.1 — Team
  Manager remains the only user-facing agent, and Incident Manager remains
  the only specialist actually wired into a live turn, until 5.1J
  deliberately connects it to Knowledge Context as the first reference
  consumer.

Key invariant to check continually while building 5.1: "Can the Knowledge
layer still work without knowing Incident Manager exists?" If the answer
becomes no, the architecture is too coupled — back out and re-decouple
before continuing.

===================================================================
DESIGN PRINCIPLES (frontend)
===================================================================

These principles governed the original UI/UX prototype phase and remain the
standard for any frontend work, including the still-mock areas
(Projects/Settings/Connectors/Skills) and any future backend-wired UI:

The interface should be calm, minimal, premium, desktop-first, spacious,
low-noise, content-first, and conversational rather than dashboard-like —
familiar without being a pixel-for-pixel clone of any other assistant.
Prefer whitespace, subtle hierarchy, progressive disclosure, restrained
borders, contextual controls, and menus/overlays over permanent panels.
Avoid enterprise-dashboard aesthetics, excessive cards, dense toolbars,
decorative complexity, and onboarding/marketing patterns inside the
product.

The prompt composer is always immediately usable when the app opens — the
user is never blocked from typing, and never needs a "New Chat" step before
their first prompt. The first submitted prompt creates the conversation.

When uncertain between showing more information or hiding it until needed,
prefer hiding it until needed, unless doing so loses important context.
When uncertain between a new screen and a modal/popover/contextual
interaction, prefer the simpler interaction if it stays clear. When
uncertain between visually impressive and calm and obvious, prefer calm and
obvious.

Accessibility fundamentals apply throughout: keyboard-accessible controls,
visible focus states, semantic buttons, sufficient contrast, predictably
closable dialogs, keyboard-navigable menus.

Desktop is the priority; the UI should still degrade gracefully at smaller
widths (sidebar may collapse, secondary metadata may hide, composer and
modals must remain usable, conversation content should keep a readable line
length).

===================================================================
WORKING IN THIS REPOSITORY
===================================================================

Before a non-trivial architectural change, read README.md,
docs/AGENT_CONTRACT.md, and docs/TEAMS_TOOL_CONTRACT.md — they are current
truth for the backend/agent architecture. For frontend product/UX intent
beyond what's built, docs/PRODUCT.md and docs/UX_SPEC.md remain useful, with
the caveat above that most of what they describe (Projects, global
Knowledge, non-Teams Connectors, Skills) is still local-state mock UI.

Propose an implementation plan for a non-trivial change before writing code,
the same discipline this project has always used: identify what already
exists, what's reusable, and the minimum change needed — do not
casually rework the frozen architecture above. Do not install new
dependencies, change the technology stack, or restructure existing modules
without a clear reason tied to the task at hand.

docs/implementation-handoff/ contains historical pre-implementation planning
documents, superseded by README.md/docs/AGENT_CONTRACT.md/
docs/TEAMS_TOOL_CONTRACT.md for current architecture — useful for historical
context, not for current truth.
