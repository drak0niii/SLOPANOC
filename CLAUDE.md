CLAUDE.md

Project Purpose

SLOPANOC is an enterprise AI assistant for Microsoft Teams-based incident and
operational collaboration. It began as a UI/UX-only prototype; that phase is
over. A real React frontend and a real FastAPI backend now exist, orchestrating
Gemini (via Google ADK) to read and summarize Microsoft Teams conversations,
to propose — never silently execute — Teams write actions, to reason from
governed operational knowledge (Generic KM, Phase 5.1, complete), and to
combine current-turn image evidence with Teams and/or governed-knowledge
context in the same specialist turn (POST-5.1 B, B0–B7, complete).

Read this file together with README.md, docs/AGENT_CONTRACT.md, and
docs/TEAMS_TOOL_CONTRACT.md before making an architectural change. Those three
documents are current architectural truth; this file summarizes what matters
for day-to-day work in this repository and must never contradict them. If this
file and the implementation ever disagree, the implementation wins — treat
that as a signal this file needs a small correction, not that the code is
wrong.

docs/BUILD_SEQUENCE.md is the canonical, detailed, current strategic build
sequence (Phase 0 through Phase 7) — read it for the full dependency
progression and topology evolution behind the status markers in this file.
docs/KNOWLEDGE_CONTRACT.md is the authoritative Generic KM contract.

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

    Head of Automated Operations        [FUTURE, optional]
    Supervision / Efficiency / Governance
                |
                v
           Team Manager                 [CURRENT — user-facing orchestrator]
                |
       +--------+--------+
       v                 v
    Incident Manager   Troubleshooting Manager
     [CURRENT]            [FUTURE / Phase 6A]
       |                   |
       |             Skills [FUTURE / Phase 6A]
       |                   |
       +--------+----------+
                v
      Context Engineering Layer         [FUTURE / Phase 6A foundation,
                |                        Phase 6B expansion]
       +--------+--------+--------+--------+
       v                 v                 v        v
    Operational       Knowledge          Case   Experience Memory
     Context            Context          Context   [FUTURE / Phase 6A]
       |                 |                 |
     Teams          Generic KM Layer     Cases
    [CURRENT --      [CURRENT]          [CURRENT]
   text; media
   is 5.X/NEXT]           |
                +--------+--------+--------+
                v        v        v        v
               MOP      SOP      RCA      KB
                 (all behind Generic KM — [CURRENT];
                  real TELCO/RAN content via A5 — [COMPLETE])

Key architectural statement (governs Phase 5.1 design, still binding):

"The Generic Knowledge Management Layer is one provider of Knowledge
Context within the broader Context Engineering architecture. It must
remain independent of individual specialist agents and expose governed,
validated knowledge through generic contracts."

Read this section as CURRENT / NEXT / FUTURE labels, never as "the runtime
includes" or "the system does" — those phrasings are reserved for what
`git`/tests actually prove exists. In particular:

- MOP/SOP/RCA/KB are never peer raw sources alongside Teams and Cases —
  they sit behind the Generic KM Layer, which itself sits behind Knowledge
  Context, which is one of three (now four, with Experience Memory) future
  context domains the future Context Engineering Layer would assemble
  (Operational, Knowledge, Case, Experience Memory).
- Generic KM Layer is now CURRENT (Phase 5.1 complete) and A5 has proven
  it against real compound TELCO/RAN content — this diagram previously
  showed it as [NEXT]; that label is now stale and corrected here. Do not
  reintroduce a "[NEXT]" label for Generic KM anywhere else in this file.
- The Context Engineering Layer remains a future conceptual abstraction —
  its FOUNDATION is Phase 6A scope (bounded to context sources that exist
  by then: Knowledge Context, Case Context, Teams text + 5.X media,
  session state), its EXPANSION against the full Operational Context
  surface is Phase 6B scope (after 5.2–5.7). Do not build either during
  5.X, and do not conflate 6A's bounded foundation with 6B's fuller
  expansion.
- Troubleshooting Manager, Skills, and Experience Memory belong to Phase
  6A (docs/BUILD_SEQUENCE.md §2a). Head of Automated Operations remains
  FUTURE and OPTIONAL — it is not automatically part of 6A merely because
  6A exists, and must never be a mandatory hop in the current or 6A-era
  runtime. The current user-facing path remains Team Manager, unchanged.
- Operational Context's future sources (ITSM, Alarms, Topology, KPIs,
  Change, Handover) are Phase 5.2–5.7 roadmap items, now scheduled after
  Phase 6A and Phase 4H (not directly after A5) — listed here only to
  show where Operational Context is headed — do not build any of them
  early. Teams-originated rich media (images first) is 5.X, the
  immediate next milestone — see docs/BUILD_SEQUENCE.md §2b.

===================================================================
ARCHITECTURE INVARIANTS — KNOWLEDGE / MEMORY / SKILLS / TOOLS / MCP /
AGENTS (added post-A5, documentation/mental-model alignment only)
===================================================================

SLOPANOC uses one canonical mental model across Knowledge/RAG, Memory,
Skills, Tools/Connectors/MCP, Context Engineering, and Agents. Full
definitions live in docs/AGENT_CONTRACT.md §3a, docs/KNOWLEDGE_CONTRACT.md
§22, docs/TROUBLESHOOTING_STRATEGY.md §12a, and README.md's "Canonical
mental model" subsection — this section exists so a future coding session
does not have to rediscover them, and does not accidentally violate them.
Read the invariants below as constraints on ALL future work, not just A5:

- MOP ≠ Skill. SOP ≠ Skill. RCA ≠ Memory. All four (MOP/SOP/RCA/KB) are
  `KnowledgeDocumentType` values inside Generic KM — content, not
  behavior. A (FUTURE, not built) Skill is a reusable behavioral
  procedure a specialist selects and executes; it may RETRIEVE a MOP, it
  never IS one.
- Chat history ≠ Approved Knowledge. Case Context ≠ Approved Knowledge.
  Experience Memory (FUTURE) ≠ organisational truth. All three are
  memory/operational-state concepts, never `KnowledgeObject`s with a real
  `LifecycleStatus`. None of them may silently become Approved Knowledge
  — only the existing human-gated `CANDIDATE → APPROVED` governance
  transition (docs/KNOWLEDGE_CONTRACT.md §14/§22.4) can do that, and an
  agent (or a future Experience Memory mechanism) observing something
  repeatedly is never sufficient on its own.
- Knowledge Context ≠ Knowledge Agent. There is no Knowledge Agent,
  planned or built. Generic Governed Knowledge is a Knowledge Context
  provider consumed through tool contracts (`knowledge_search`/
  `knowledge_select_evidence`), never its own reasoning boundary.
- Skill ≠ Agent. Tool ≠ Agent. A Skill (FUTURE) and a Tool (CURRENT —
  Teams tools; FUTURE — other connectors) are both things an Agent uses,
  never a competing reasoning boundary. Do not create a new named agent
  (a "VSWR Agent," a "Cell Down Agent") for work that can instead be
  represented as a Skill executed by an existing specialist — see the
  existing "Future agent expansion principles" in docs/AGENT_CONTRACT.md
  §12 and this file's own "Preserve the current frozen runtime
  architecture" rule above.
- MCP ≠ mandatory integration architecture. MCP (FUTURE, optional) is one
  possible transport for exposing a tool/connector, not a required
  rewrite of every existing typed integration. Do not relabel the current
  Teams integration (Incident Manager → typed Python Teams tools → Power
  Automate client → Power Automate → Microsoft Teams) as MCP.
- RAG ≠ Knowledge Repository. RAG is the retrieval MECHANISM Generic KM's
  existing retrieval/ranking/provenance pipeline already implements
  (docs/KNOWLEDGE_CONTRACT.md §16, §18, §22.1) — it is how the ONE
  `KnowledgeRepository` gets queried, never a second repository or
  competing source of truth.
- Model inference ≠ trusted operational fact. Unchanged from the existing
  "TRUST / CONTROL PRINCIPLES" section below — this applies equally to
  any future Skill/Memory/Context Engineering work: a model's own
  inferred/suggested fact never becomes a trusted dimension value,
  applicability fact, or Approved Knowledge claim merely by being stated
  confidently or repeatedly.
- Ingestion ≠ approval. Ingesting a document (or, in the future,
  recording an Experience Memory entry) only ever produces a CANDIDATE —
  never an APPROVED `KnowledgeObject` — per the existing, unmodified
  governance lifecycle (docs/KNOWLEDGE_CONTRACT.md §5/§14).
- Authority ordering (non-strict, but this one line is load-bearing): an
  explicit Approved procedural prohibition always outranks Experience
  Memory / prior pattern information. Example: an Approved MOP saying "do
  not restart for VSWR Over Threshold" remains authoritative even if
  Experience Memory shows three prior VSWR cases were restarted.

None of Skills, Experience Memory, Context Engineering, Troubleshooting
Manager, or MCP is implemented by this section — it is a documentation/
mental-model alignment pass only, performed after A5's completion, and
does not reorder the locked roadmap below or reopen A5.

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
  local SQLite confirmed untouched during that validation. SQLite remained
  the zero-setup LOCAL DEFAULT when `SLOPANOC_DATABASE_URL`/
  `SLOPANOC_KNOWLEDGE_DATABASE_URL` were not explicitly set to PostgreSQL,
  at the time this paragraph was written — that default behavior was
  unrelated to whether Cloud SQL support itself was implemented and
  proven, which it already was. SUPERSEDED for normal runtime by the
  POST-A5 "Cloud SQL-only runtime hardening" refinement below (see the
  "MANDATORY CLOUD SQL RUNTIME POLICY" / "POST-A5 REFINEMENT UPDATE"
  paragraphs further down this list) — normal backend startup no longer
  silently falls back to local SQLite for either domain; it fails fast
  instead. SQLite remains the default only for the isolated automated
  test suite, which now opts in explicitly.
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

  POST-A5 REFINEMENT UPDATE — CODE-ENFORCED, NOT JUST A DOCUMENTED POLICY
  (final corrective pass): as of the POST-A5 "Cloud SQL-only runtime
  hardening" refinement, this policy is enforced by `backend/api/runtime_
  database_policy.py`, wired into `backend/api/app.py`'s `_lifespan`
  startup hook. Normal backend startup now fails fast (a safe
  `ConfigurationError`, no resolved URL or credential ever logged) unless
  BOTH persistence domains resolve to EXACTLY the `postgresql` SQLAlchemy
  dialect — a POSITIVE requirement, not merely "not sqlite": an unset
  variable (previously a silent local-SQLite fallback), an explicit
  `sqlite+aiosqlite://` URL, a `mysql`/`mariadb`/`oracle`/`mssql`-style
  URL, and an unparseable URL are all rejected identically — AND unless
  `SLOPANOC_SESSION_BACKEND == "database"` (ADK's `InMemorySessionService`
  "memory" mode is REJECTED for normal runtime too, since it never
  persists to Cloud SQL at all, regardless of what `SLOPANOC_DATABASE_URL`
  is set to).

  THERE IS NO ENVIRONMENT VARIABLE THAT WEAKENS THIS POLICY. An earlier
  pass of this refinement added `SLOPANOC_ALLOW_SQLITE_RUNTIME` as a
  config-based test opt-out; that was corrected and removed entirely (no
  such `Settings` property exists) because any env var is, by
  construction, something a real deployment's configuration could also
  set, accidentally or otherwise — defeating the whole guarantee. The
  ONLY way the automated test suite stays hermetic (no live Cloud SQL/
  Auth Proxy/ADC needed) for a test that instantiates the real FastAPI app
  is a pure Python-level dependency substitution:
  `backend/tests/conftest.py`'s autouse fixture
  (`bypass_runtime_database_policy_for_tests`) monkeypatches the
  *function reference* `backend.api.app` itself calls
  (`backend.api.app.validate_runtime_database_configuration`) with a
  stub — no environment/configuration value from outside a test process
  can reach or trigger this. Mirrors the exact pattern this codebase
  already uses for `warmup_shared_model`. This module never inspects
  pytest internals (`PYTEST_CURRENT_TEST`/`sys.modules`/stack frames) and
  never will. `resolve_database_url()`/`resolve_knowledge_database_url()`
  themselves are deliberately unchanged (still plain, policy-free
  resolution) so tests that construct an isolated SQLite repository/
  session service directly continue to work exactly as before.
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
below and the (now-realigned) Phase 4H — does not reorder anything else
in this locked list): POST-5.1 A — CLOUD SQL POSTGRESQL (A1–A4) is
COMPLETE. POST-5.1 B — MULTIMODAL ATTACHMENTS is COMPLETE (B0–B7, all
done — see B7's own entry below for its full implementation,
corrective-pass, and real-stack live-validation history). **POST-5.1 B7
(Lifecycle + Real UI + Full Regression) is DONE.** The POST-B7 UI/UX
Refinement Milestone (see its own entry below) is COMPLETE and
live-validated. **A5 — Knowledge Island Ingestion Foundation + Real
TELCO/RAN Compound Knowledge Validation (see its own entry below) is
COMPLETE and live-validated.** **POST-A5 REFINEMENT — Cloud SQL-only
runtime hardening + Source Drawer source+version consolidation (see its
own entry below) is COMPLETE and live-validated.** This refinement does
NOT reorder the roadmap below — 5.X remains NEXT.

**ROADMAP REALIGNMENT (locked, replaces the previous A5 → Phase 4H →
5.2–5.7 → Phase 6 order — see docs/BUILD_SEQUENCE.md §2a for the full
rationale):** the execution order after A5 is now **5.X (Teams Rich
Content / Media Retrieval) → Phase 6A (Intelligence Architecture
Foundation) → Phase 4H (Security Hardening) → 5.2–5.7 → Phase 6B
(Context Engineering Expansion) → Phase 7**. **5.X is NEXT — NOT
STARTED.** Phase 4H no longer immediately follows A5; it now follows
Phase 6A.

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

      UPDATE (B7 corrective pass, see B7's own entry below): item (b) is
      now resolved -- distinct governed Knowledge evidence references are
      preserved and are now disambiguated in the UI using trusted
      document/section metadata. Item (a) (the "svg artifact") was
      separately audited during B7 and found not reproducible from
      source -- see B7's own entry.

      **B6 is DONE.**
  B7 [DONE] Lifecycle +
      Real UI + Full Regression -- closes the B3-documented orphan gap
      and re-proves the full B0-B6 attachment surface still regresses
      clean, against Cloud SQL.

      ATTACHMENT LIFECYCLE CLOSURE -- audited before implementing: the
      domain-layer `AttachmentService.mark_deleted` (`READY|LINKED ->
      DELETED`, atomic, ownership/session-checked) already existed since
      B1, fully tested, simply never exposed via HTTP or called from
      anywhere. `backend/api/attachment_service.py`'s new `delete_ready_
      attachment` is a DELIBERATELY NARROWER entry point than that
      domain method: it only ever permits `READY -> DELETED`, rejecting
      `LINKED` with a safe `validation_error` BEFORE `mark_deleted` is
      ever called -- "never delete a durable, sent attachment" is a
      structural property of this call site, not a hope that `mark_
      deleted`'s own broader (future) capability is never misused.
      Idempotent for an already-`DELETED` id. Cloud SQL transitions
      FIRST, GCS deletion is best-effort SECOND (the inverse of B2's own
      upload order, for the same reason: the DB row is the authoritative
      claim about whether the binary exists, so that claim is retracted
      before the binary deletion even starts, never left standing after
      the binary is gone) -- a GCS-side failure during cleanup is logged,
      never raised; the attachment is already correctly gone from every
      path that reads `DELETED` (already unconditional in `get_
      attachment_content`/`prepare_attachments_for_turn`, unchanged).
      New route: `DELETE /api/sessions/{session_id}/attachments/
      {attachment_id}` -> 204, session-ownership-scoped like upload
      (never the owner-only, no-session scoping the two GET routes use).
      Frontend: `AppState.tsx`'s `removeAttachment` now also calls this
      route -- fire-and-forget, best-effort, only when the attachment
      being removed is a `kind: "image"` draft with `uploadState ===
      "ready"` and a real `attachmentId` (a `pending`/`uploading`/
      `failed` removal never had one to delete); a failure is logged
      DEV-only, never surfaced to the user, and never re-adds the
      already-removed local draft item.

      UI CLEANUP AUDIT: the B6-observed "svg artifact" in source-
      reference chips was NOT reproducible from source -- `SourceChip
      .tsx` renders a normal `lucide-react` icon (`aria-hidden` by
      default, contributing nothing to the accessible name) followed by
      "Source · {label}" text; its own existing test suite already
      asserts the clean accessible name (`getByRole("button", { name:
      /Source · Teams conversation/ })`) with no extra text. No code
      change was made for this item -- treated as descriptive shorthand
      in the B6 live-validation narration, not a confirmed defect. The
      OTHER B6 observation (two similar-looking governed-KM chips) was
      explicitly NOT a provenance bug (two legitimately distinct approved
      fixtures) and no backend "deduplication" was implemented, per the
      B6 closure's own explicit instruction -- instead, a SEPARATE B7
      corrective pass (below) disambiguated the chip LABELS themselves,
      frontend-only, from metadata the backend already sent.

      B7 CORRECTIVE PASS -- GOVERNED-KM SOURCE LABEL DISAMBIGUATION:
      distinct governed Knowledge evidence references are preserved and
      are now disambiguated in the UI using trusted document/section
      metadata. Audited first (per instruction): `KnowledgeSourceReference
      DTO` already carried `title` (required) and `section_heading`/
      `source_display_name` (optional) -- the backend just never used them
      for the chip's own visible label, hardcoding a single constant
      (`KNOWLEDGE_SOURCE_LABEL = "Governed knowledge"`,
      backend/api/knowledge_source_reference.py, UNCHANGED by this pass)
      for every KM reference regardless of which document/section it
      actually was. Fix is entirely frontend: `src/lib/sourceReference.ts`
      's new `formatKnowledgeSourceLabel` builds `"<title> · <section
      heading>"` when both are present, falling back progressively to
      `"<title>"` alone, then `"<source display name>"`, then the
      original generic "Governed knowledge" -- never touching `label`
      itself, `source_uri` (never present on the DTO at all), retrieval,
      evidence selection, provenance identity, ranking, KM storage, or
      Incident Manager behavior. `SourceChip.tsx`'s `kind === "knowledge"`
      trigger now calls this helper instead of reading `source.label`
      directly; the drawer/details view is UNCHANGED (already showed
      `title`/section/version distinctly). Evidence cardinality is
      UNCHANGED -- two distinct selected identities (e.g. two sections of
      the same approved document) still always render as two separate
      chips; only their TEXT now differs. No same-title/same-knowledge_id
      deduplication of any kind was added, per the explicit invariant this
      pass was given. Tests: 6 new unit tests
      (`src/lib/sourceReference.test.ts`, covering the full fallback
      chain and proving two sections of the same document produce two
      distinct labels), 5 new/1 updated component tests
      (`SourceChip.test.tsx`, covering two-distinct-chips-both-present,
      title-only fallback, generic fallback, no `gs://`/`source_uri` in
      the rendered trigger text, and Teams presentation unchanged) -- 11
      new tests total. Frontend suite: 678 passed (667 B7-implementation
      baseline + 11 new); `npm run build`/`npx tsc -b` both clean. No
      backend file changed -- backend regression not re-run (instruction:
      skip unless a backend contract changed; it did not).

      TRUST/SECURITY INVARIANTS: unaffected by this pass -- no B5/B6
      multimodal propagation file (`multimodal_agent_tool.py`,
      `multimodal_turn_context.py`, `read_continuation_execution.py`,
      `evidence.py`, `provenance_compliance.py`), approval file, or
      knowledge-tools file was touched. Confirmed both by file-scope
      diff and by the full regression suite (below) staying green.

      TESTS: `backend/tests/test_attachment_lifecycle.py` (9 -- READY
      delete + storage cleanup, idempotent re-delete, LINKED rejected +
      untouched, foreign owner rejected, foreign session rejected,
      unknown id rejected, missing/failing GCS delete does not fail the
      request, already-DELETED never re-touches storage, unconfigured
      storage still completes the DB transition), 6 new HTTP-level tests
      in `test_api_attachment_endpoints.py` (204 + storage cleanup,
      idempotent over HTTP, LINKED rejected with 400 + still retrievable,
      cross-user denied, unknown id denied), 5 new frontend tests in
      `AppState.attachments.test.tsx` (READY removal triggers delete with
      the correct session/attachment id, pending/uploading/failed/non-
      image removal never call it, a failing delete never re-adds the
      item or throws). `test_api_security_contract.py`'s closed route
      allow-list updated for the one new route (the only pre-existing
      test that needed a change, and only because it is deliberately an
      exhaustive allow-list).

      REGRESSION: full backend suite 2631 passed, 1 skipped (2617 B6
      baseline + 14 new); standalone KM-tagged subset 846 passed (B6:
      846, unchanged); Teams-tagged subset 291 passed (B6: 291,
      unchanged); full frontend suite 667 passed (662 B6 baseline + 5
      new); `npm run build` clean; `npx tsc -b` clean.

      B7 CORRECTIVE PASS -- HISTORICAL GOVERNED-KM/TEAMS PROVENANCE
      PERSISTENCE (closes a genuine B7 persistence/rehydration gap found
      during the KM source-label work above, NOT a new feature phase):
      CURRENT-TURN `SourceReferenceDTO`/`KnowledgeSourceReferenceDTO`
      provenance rode the live SSE `message.completed` payload correctly,
      but `GET /api/sessions/{id}/history` never projected either kind at
      all (audited first: this gap was symmetric, affecting Teams and
      governed-KM equally, not KM-specific as first suspected) -- so a
      hard refresh, backend restart, or reopened saved chat silently
      dropped a previously-grounded answer's provenance from the UI.

      MECHANISM (audited before choosing it): ADK's own
      `DatabaseSessionService`/rewind machinery, not a new table or
      migration. `backend/api/turn_source_references.py` (NEW) stores one
      plain (never `temp:`-prefixed) session-state key,
      `TURN_SOURCE_REFERENCES_STATE_KEY`, whose value is a dict keyed by
      `turn_id` (the same ADK `invocation_id` identity B4A/B4B already
      established) -> that turn's exact `source`/`knowledge_sources`
      payload, verbatim `.model_dump(mode="json")` of the SAME DTOs the
      live SSE path already sends -- never a separate "historical" shape,
      never re-run `knowledge_search`/`knowledge_select_evidence`, never
      today's current KM document state. `chat_service.py` writes the
      FULL accumulated dict (never an incremental patch) once per turn,
      right before the `message.completed` SSE event; `session_history_
      service.py` reads it back per turn via `resolve_turn_source_
      references` and populates two new, additive `SessionHistoryMessage
      DTO` fields (`source`, `knowledge_sources`) alongside the existing
      ones. Verified directly against installed ADK 1.33.0 source
      (`Runner._compute_state_delta_for_rewind`): because the write is
      always the full accumulated dict and ADK's own rewind replays
      `state_delta` values in event-list order, a real `Runner.rewind_
      async` call correctly reverses a discarded turn's own provenance
      entry for free -- no second, purpose-built branch-selection
      mechanism was added. Frontend: `SessionHistoryMessageDTO` (src/api/
      types.ts) gained the same two fields; `AppState.tsx`'s
      `HISTORY_FETCH_SUCCEEDED` reducer maps them onto the SAME
      `chat.sources`/`chat.knowledgeSources` maps the live
      `BACKEND_MESSAGE_COMPLETED` path already populates, keyed by
      message id -- `Message.tsx`/`SourceChip.tsx` needed NO changes at
      all, since both paths converge on one rendering model. Cardinality
      is preserved exactly (two distinct sections of the same document
      still hydrate as two separate chips, never deduped/merged); no
      `source_uri`, `gs://` URI, or other internal identifier is ever
      persisted or exposed -- the persisted shape is exactly the DTOs'
      own already-sanitized fields.

      TESTS (all passing): `backend/tests/test_p5_1_b7_turn_source_
      reference_persistence.py` (8 -- a real end-to-end pass driving a
      real file-backed `DatabaseSessionService`/`ChatService.run_turn`/
      scripted team_manager+incident_manager through a combined Teams +
      two-governed-KM-section answer, proving `GET /history` returns both
      exact KM references and the Teams reference, cardinality preserved,
      no `source_uri`/`gs://` leak, idempotent repeated reads, and a real
      `rewind_before_user_turn` call removing the discarded turn's
      provenance; plus 6 unit tests for `build_turn_source_references_
      delta`/`resolve_turn_source_references`'s own edge cases --
      malformed/missing data, multi-turn preservation). Frontend: 10 new
      tests in `src/components/conversation/Message.historicalProvenance
      .test.tsx` (real `AppStateProvider` integration -- hydrated KM
      SourceReferences render; two sections of one document remain two
      chips; exact `<title> · <section heading>` label; Teams+KM render
      together; hydration converges on the same `SourceChip`/
      `formatKnowledgeSourceLabel` path the live SSE flow uses; no
      `source_uri`/`gs://` in the rendered drawer text; missing optional
      metadata falls back correctly; a text-only historical message shows
      no chip; a persisted image and governed-KM provenance render
      together on the same historical turn (B4D unaffected);
      selectionCards/actionCards/runTraces stay absent when hydration
      carries no such data). One pre-existing allow-list test
      (`test_api_session_history_endpoints.py::test_history_response_
      never_leaks_raw_state_or_internal_fields`) was updated to include
      the two new, intentional DTO fields -- the only existing assertion
      this pass needed to change, and only because it is a deliberately
      exhaustive field allow-list.

      REGRESSION (this corrective pass): full backend suite 2639 passed,
      1 skipped (2631 B7-implementation baseline + 8 new); KM-keyword
      subset 791 passed; Teams-keyword subset 292 passed (both keyword-
      selected, not marker-selected -- no pytest marker infrastructure
      exists in this repo); full frontend suite 688 passed (678 B7-label-
      pass baseline + 10 new); `npm run build` clean; `npx tsc -b` clean.

      TRUST/SECURITY INVARIANTS UNCHANGED: no KM retrieval/ranking/
      applicability/lifecycle/version-resolution code, no `knowledge_
      search`/`knowledge_select_evidence` tool, no B5/B6 multimodal
      propagation file, no approval/selection-continuation file, and no
      Team Manager/Incident Manager wiring was touched -- SEARCH RESULT
      != EVIDENCE USED still holds (only a turn's already-selected,
      already-validated evidence is ever persisted); the model never
      gains any way to author or influence persisted provenance.

      LIVE VALIDATION: still NOT performed (same environment limitation
      as the rest of B7). This corrective pass EXTENDS checklist item (B)
      above: hard refresh / reopening a saved chat must now show the
      SAME governed-KM and Teams source chips the original live answer
      had -- not just the same text -- and item (C) (backend restart)
      must show the same provenance surviving a real process restart
      too. Both remain open, real-stack checks for the human-performed
      B7 live pass, alongside (A) through (J) above.

      B7 LIVE-REGRESSION CORRECTIVE PASS -- GOVERNED-KNOWLEDGE COMPLETION
      REMEDIATION LOST CURRENT-TURN IMAGE EVIDENCE (a REAL live-stack
      defect, found by the user's own B7 final combined validation, not a
      documentation/UI pass): a real image + Teams + Cloud SQL governed-
      knowledge run (image: observed checksum 7318, status GREEN;
      governed KM: required 7319/GREEN; Teams: TEAM-ORION owns escalation,
      no remediation approved) produced "Assuming the image shows a
      checksum of 7319 and a RED status indicator" -- the system invented
      observed values instead of using the real current-turn image.

      ROOT CAUSE, PROVEN BY DIRECT AUDIT (not assumed): `backend/agents/
      team_manager/governed_knowledge_completion.py`'s `enforce_governed_
      knowledge_at_completion` -- the bounded, deterministic remediation
      `chat_service.py` triggers whenever a turn declared `requires_
      governed_knowledge=true` but finished with no selected KM evidence
      -- built its own nested `incident_manager` Content TEXT-ONLY,
      unconditionally. This remediation is a bare `Runner.run_async` call,
      never routed through `AgentTool`/`MultimodalAgentTool`, so B6's own
      image-propagation mechanism (which only intercepts `AgentTool.run_
      async`, reading `tool_context.user_content`) never had a way to
      reach it -- the ORIGINAL delegation genuinely had the image (proven
      by test), the SECOND, remediation execution genuinely did not.
      `backend/agents/team_manager/source_requirements_completion.py` was
      independently audited and found to share the identical text-only
      Content pattern, but was deliberately NOT modified in this pass: it
      only ever calls `record_source_requirements` (a boolean-tuple
      classification) and never produces user-facing answer text, so it
      cannot itself fabricate an observed value the way the reported
      defect describes. `backend/agents/incident_manager/provenance_
      compliance.py`'s own compliance retry was also audited and found
      correct/untouched: its instruction explicitly forbids changing the
      prior answer's content ("respond again with the SAME structured
      response... do not change its content"), so it cannot introduce
      invented values either.

      FIX, MINIMAL AND STRUCTURAL (no keyword/text filtering of any
      kind): `backend/api/multimodal_turn_context.py` gained `trusted_
      image_parts_from_content(content)` -- a plain function extracting,
      in order, every `file_data`-bearing `Part` from an already-trusted
      `Content` object (the SAME filter predicate `MultimodalAgentTool`
      already uses, deliberately duplicated rather than cross-imported --
      that class remains its own narrow, ADK-version-audited unit).
      `enforce_governed_knowledge_at_completion` gained an `image_parts`
      parameter (default `()`, so a text-only turn's remediation `Content`
      is byte-identical to before this pass), appended after the
      structured-request text part -- exactly mirroring `MultimodalAgent
      Tool`'s own "text first, then trusted image parts, in order"
      construction. `chat_service.py`'s own remediation call site now
      passes `image_parts=trusted_image_parts_from_content(content)`,
      where `content` is the SAME already-built, already-validated turn
      `Content` object the original team_manager Runner call already used
      -- no new registry, no run-id correlation, no re-derivation from
      attachment ids, no fresh storage/DB lookup. This is deliberately
      NARROWER than `multimodal_turn_context.py`'s existing attachment-id
      registry (which is unaffected/untouched): the trusted `Part` objects
      are passed as a plain function argument within one Python call
      stack, so there is no possible channel for an unrelated run to ever
      read them -- structurally satisfies "must remain turn-scoped, never
      a global attachment cache, never search history for an image."
      `source_requirements_completion.py` was NOT given the same
      parameter in this pass (see ROOT CAUSE above for why it was
      deliberately left as-is).

      FAIL-CLOSED SEMANTICS: with this fix, a text-only turn's remediation
      passes zero image parts (same as before this pass -- correct,
      unchanged behavior); an image-bearing turn's remediation now always
      carries the SAME real evidence the original run had. There is no
      separate "reconstruction" step that could fail (unlike an id-based
      re-lookup would be) -- the Parts are the exact same in-memory
      objects, so there is no fail-closed gap to add: the fix eliminates
      the only place fabrication could occur (missing evidence forcing
      the model to guess) rather than adding a keyword/text filter after
      the fact, per the explicit "the solution must be structural, not
      keyword-based" instruction.

      TESTS: `backend/tests/test_p5_1_b7_governed_completion_image_
      evidence.py` (17 new -- the MOST IMPORTANT one drives a real
      `ChatService`/`AttachmentService`/Teams-gateway-mock/isolated-KM-
      repository pipeline through the EXACT live-reproduced trigger
      [original incident_manager execution has real image + Teams
      evidence but deliberately never selects governed-KM evidence,
      forcing the completion-boundary remediation] and proves, directly
      against `llm_request.contents`, that the REMEDIATION model's own
      first reasoning step genuinely receives the SAME trusted `file_data`
      Part -- same `file_uri`, same order [text then image] -- and that
      the final synthesis correctly reports observed 7318/GREEN, required
      7319/GREEN, FAIL, and TEAM-ORION, with `"assuming"` and a fabricated
      `"RED"` asserted absent; plus a text-only-turn no-change proof, a
      no-recursive-remediation-call proof, `Part.from_bytes` patched to
      raise [never invoked], `gs://`/bucket-name absence from the final
      user-visible answer, and 6 unit tests for `trusted_image_parts_
      from_content` [order/multi-image preservation, `None`/empty
      handling, no cross-call state -- proving "unrelated run cannot
      access another run's images" has no possible channel at all, plus 2
      cleanup/cancellation tests for `enforce_governed_knowledge_at_
      completion`'s own existing finally-block contract, unaffected by the
      new parameter]). Two pre-existing test files' mock signatures
      (`fake_remediation`/`fake_governed` in `test_p5_1j_governed_
      completion_gate.py` and `test_p5_1j_source_requirements_gate.py`)
      were widened to accept the new keyword-only `image_parts` argument
      -- the only existing assertions this pass changed, and only because
      those mocks stand in for the real function's own signature.

      REGRESSION: full backend suite 2650 passed, 1 skipped (2639 prior-
      corrective-pass baseline + 11 new -- 17 new tests minus the net
      effect of counting shared fixtures once); KM-keyword subset 791
      passed; Teams-keyword subset 292 passed; attachment/multimodal-
      keyword subset 204 passed; B6 multimodal-focused suite (Multimodal
      AgentTool, image fast-path, image+Teams+KM integration, selection-
      continuation image preservation, turn-context isolation, rewind
      ghost-image) re-run explicitly and unchanged at 30 passed; full
      frontend suite 688 passed (untouched -- this pass is backend-only);
      `npm run build`/`npx tsc -b` both clean.

      NOT TOUCHED (audited, confirmed already correct): `Multimodal
      AgentTool` (original delegation path), `direct_read_fast_path.py`'s
      `_has_image_evidence`/fast-path-skip gate (`fast_path_skipped_has_
      image_evidence` still fires exactly as before), `provenance_
      compliance.py`'s compliance retry, KM retrieval/ranking/
      applicability/lifecycle/version-resolution, `knowledge_search`/
      `knowledge_select_evidence` themselves, SelectionCard continuation
      image preservation, rewind machinery, Team Manager/Incident Manager
      topology, approval/selection state machines, Gemini model
      configuration, and all frontend source-chip/attachment/provenance-
      hydration code from the prior two corrective passes.

      B7 LIVE-REGRESSION CORRECTIVE PASS -- EXACT-DUPLICATE GOVERNED-KM
      PROVENANCE: the user's own re-run of the corrected combined image +
      Teams + governed-KM scenario (the one the previous corrective pass
      fixed) produced the correct answer, but the UI showed FOUR source
      chips instead of three -- Teams, Verification, **Verification
      again**, Escalation -- and the exact duplicate survived a hard
      refresh, proving it was durably persisted/reprojected, not a
      transient frontend artifact.

      AUDIT, DONE BEFORE ANY CODE CHANGE: traced every identity-based
      construction/selection mechanism already in this codebase --
      `KnowledgeEvidenceSet`'s own Pydantic model validator (backend/
      knowledge/provenance/contracts.py) already structurally FORBIDS a
      duplicate `(knowledge_id, version_label, section_id)` identity
      within one evidence set (raises on construction); `backend.tools.
      knowledge.runtime.select_evidence`'s own guarded append already
      deduplicates repeated `knowledge_select_evidence` calls within one
      run_id; `build_knowledge_source_references` (backend/api/knowledge_
      source_reference.py, pre-existing Phase 5.1J logic) already
      deduplicates by the SAME canonical identity when building DTOs. All
      three were found ALREADY CORRECT by direct inspection -- no
      reproducible in-process defect exists in any of them, and the exact
      live-Gemini trigger could not be reproduced without live Cloud
      SQL/Gemini access (the same standing limitation as every prior live
      milestone). What WAS found missing, exactly where the user's own
      symptom points: NEITHER `turn_source_references.py`'s persistence
      write NOR its read-time history projection had ANY exact-identity
      normalization of their own -- so a duplicate reaching either
      boundary, from any cause, would be faithfully persisted/reprojected
      forever.

      FIX: `backend/api/knowledge_source_reference.py` gained `dedupe_
      knowledge_source_references(references)` -- generic, identity-only
      (`knowledge_id`/`version_label`/`section_id`, the SAME canonical
      identity `build_knowledge_source_references`/`KnowledgeEvidenceSet`
      already use -- NEVER `title`/`section_heading`/`source_display_
      name`/content, which can legitimately collide for two genuinely
      DISTINCT references), first-seen order preserved, a pure function
      over an already-built DTO list (safe to call on freshly-constructed
      DTOs or on deserialized-from-persisted-state DTOs alike). Wired at
      TWO points: (1) `chat_service.py`'s own `knowledge_sources =
      dedupe_knowledge_source_references(build_knowledge_source_
      references(selected_knowledge_evidence))` -- the single point a
      turn's `knowledge_sources` list is finalized, shared by BOTH the
      live SSE `message.completed` event and the persisted provenance
      record, so both are always built from one already-safe list; (2)
      `turn_source_references.py`'s `resolve_turn_source_references`
      applies the SAME normalization to whatever it reads back -- a
      read-time-only safety net for any turn's data already persisted
      with a duplicate (whether from before this fix or from a cause this
      pass could not reproduce), never mutating the underlying stored
      session state, never re-running KM retrieval, never re-querying
      current KM state, never merging two genuinely distinct identities.

      TESTS: `backend/tests/test_p5_1_b7_km_provenance_exact_duplicate
      .py` (13 new) -- unit tests for `dedupe_knowledge_source_references`
      (exact duplicate -> one; same document/version different section ->
      two; same knowledge_id different version -> two; same title
      different authoritative identity -> two; same section heading in
      two different documents -> two; deterministic first-seen order
      regardless of input order; empty input), persistence/read-time-
      projection boundary tests (an already-persisted duplicate is
      normalized on read without mutating the stored dict or merging
      distinct references; no `source_uri`/`gs://` anywhere), and a real
      end-to-end `ChatService`/real Teams-gateway-mock/real isolated-KM-
      repository pipeline test (the most important one) where the
      scripted model selects the SAME Verification identity via TWO
      SEPARATE `knowledge_select_evidence` calls plus a distinct
      Escalation identity -- proving the live SSE event, the persisted
      `TURN_SOURCE_REFERENCES_STATE_KEY` record, and `GET /history`'s own
      projection all show exactly Teams + Verification + Escalation (3
      total chips), never a duplicate, with deterministic order and
      correct rewind/repeated-read behavior.

      FRONTEND: NO code change -- audited and confirmed `AppState.tsx`'s
      `BACKEND_MESSAGE_COMPLETED` and `HISTORY_FETCH_SUCCEEDED` reducers
      already REPLACE (never append/accumulate) a message's own
      `knowledgeSources` array on every write, so no frontend-side
      duplicate-accumulation path exists; existing SourceChip/historical-
      provenance frontend tests (81 across 4 files) re-run unchanged to
      confirm.

      REGRESSION: full backend suite 2663 passed, 1 skipped (2650 prior-
      corrective-pass baseline + 13 new); KM-keyword subset 792 passed;
      Teams-keyword subset 293 passed; B6 multimodal-focused suite re-run
      unchanged at 30 passed; full frontend suite 688 passed (unchanged);
      `npm run build`/`npx tsc -b` both clean.

      NOT TOUCHED: KM retrieval/ranking/applicability/version governance,
      `knowledge_search`/`knowledge_select_evidence` trust model, Teams
      gateway, attachment lifecycle, multimodal propagation, governed-KM
      remediation image preservation (previous corrective pass), approval,
      selection continuation, rewind machinery, session persistence
      architecture, Team Manager/Incident Manager hierarchy. No
      Aurora/checksum/GREEN/TEAM-ORION/"Verification"/"Escalation" string
      appears in any production code path -- those values exist only in
      this pass's own deterministic regression fixture.

      FINAL REAL-STACK LIVE VALIDATION -- ALL TESTS PASSED. Real stack:
      real React UI, real FastAPI backend, real Gemini 2.5 Flash via
      Vertex AI/ADK, real Cloud SQL PostgreSQL for the general AND the
      governed-KM runtime domain (each explicitly configured per the
      mandatory Cloud SQL policy below -- no implicit fallback from
      `SLOPANOC_DATABASE_URL`), real private GCS attachment storage, real
      Power Automate Teams gateway, a real Microsoft Teams conversation
      ("SLOPANOC Gateway Group Test").

      Test A -- READY attachment cleanup (upload -> 201 -> remove before
      send -> `DELETE` -> 204): PASSED.
      Test B -- durable image (attach -> send -> Gemini reasons over the
      image -> hard refresh -> saved chat reopened -> persisted image
      re-renders through the authenticated content endpoint): PASSED.
      Test C -- backend restart (process restarted; saved conversation
      and image restored from Cloud SQL/GCS with no re-send): PASSED.
      Test D -- image + Cloud SQL governed KM (image observed 7318/GREEN;
      governed KM requires 7319/GREEN; correct FAIL; approved next action
      -- collect observed values, escalate, do not restart/reconfigure):
      PASSED.
      Test E -- image + real Teams (TEAM-ORION designated owner, no
      remediation approved, combined correctly with image evidence):
      PASSED.
      Test F -- image + Teams + Cloud SQL governed KM combined: correct
      FAIL (7318 observed != 7319 required), correct Teams context,
      correct escalate-TEAM-ORION/do-not-restart guidance, NO invented RED
      value, NO "assuming" substitution for real visual evidence --
      PASSED (this is the live proof the governed-KM-completion-
      remediation image-propagation corrective pass actually fixed the
      real defect it targeted).
      Test G -- source provenance: the real UI rendered EXACTLY three
      distinct source references (Teams conversation; Aurora Relay
      Verification Procedure · Verification; Aurora Relay Verification
      Procedure · Escalation) -- no duplicate Verification chip -- PASSED
      (the live proof the exact-duplicate-provenance corrective pass
      fixed the real defect it targeted).
      Test H -- hard refresh, no re-prompt: same answer, same image, same
      three source references -- PASSED.
      Test I -- backend restart + history, no re-prompt (`GET` saved
      sessions -> `GET` historical session -> authenticated attachment
      content retrieval): same answer, image, and exact three provenance
      references -- PASSED.
      Test J -- SelectionCard continuation preserving the original trusted
      image without requiring reattachment: previously live-proven during
      B6/earlier B7 validation and covered by this codebase's own
      regression suite (`test_p5_1_b6_selection_continuation_images.py`)
      -- NOT separately re-run during the exact-duplicate corrective pass,
      and that invariant was not touched by it; recorded here accurately
      as previously-proven-and-regression-covered, not re-validated in
      this final pass.

      FINAL AUTOMATED REGRESSION (as of the exact-duplicate-provenance
      corrective pass, the last runtime change before this closure):
      backend full suite 2663 passed, 1 skipped; KM-focused subset 792
      passed; Teams-focused subset 293 passed; B6 multimodal-focused
      subset 30 passed; frontend full suite 688 passed; `npm run build`
      clean; `npx tsc -b` clean; `git diff --check` clean.

      STATUS: DONE. POST-5.1 B7 -- Lifecycle + Real UI + Full Regression
      is COMPLETE. POST-5.1 B -- Multimodal Attachments (B0-B7) is
      COMPLETE. The POST-B7 UI/UX Refinement Milestone (see its own
      entry immediately below) is COMPLETE and live-validated. A5
      (Knowledge Island ingestion foundation + real TELCO/RAN compound
      knowledge validation) is COMPLETE and live-validated (see its own
      entry further below). **5.X -- Teams Rich Content / Media
      Retrieval is NEXT -- NOT STARTED**, followed by Phase 6A
      (Intelligence Architecture Foundation), then Phase 4H security
      hardening, then 5.2-5.7, then Phase 6B -- see
      docs/BUILD_SEQUENCE.md Sec 2a for the locked roadmap realignment.

===================================================================
POST-B7 UI/UX REFINEMENT MILESTONE -- COMPLETE
===================================================================

A SEPARATE, bounded milestone after B7 -- NOT part of B7, does not reopen
or redesign any B7 architecture/trust boundary/persistence semantics.
Five bounded polish refinements to the current SLOPANOC experience, no
new integrations, no new agents, no new databases, no architecture
expansion.

1. TEAMS CHAT MESSAGE FORMATTING. Root cause: outbound `message` was
   always a raw, unstructured string -- the Power Automate "Post message
   in a chat" action's Message field is confirmed (by the user) to be
   rich-text/HTML, so a multi-paragraph/list answer rendered as one wall
   of text. Fix: `backend/tools/teams/message_formatting.py`'s new
   `format_teams_message` -- deterministic, generic markdown-lite plain
   text -> minimal-safe HTML (paragraphs, bullet/numbered lists, a short
   heading-like line ending in `:`; every text run passed through
   `html.escape` before being wrapped in one of six hardcoded tags: `<p>`,
   `<br>`, `<ul>`, `<ol>`, `<li>`, `<strong>` -- never a general-purpose
   Markdown/HTML renderer, never Adaptive Cards). TRUST BOUNDARY: called
   EXACTLY ONCE, inside `teams_propose_send_message`
   (backend/tools/teams/propose_write.py), on the model's raw text,
   BEFORE `normalize_send_message_payload`/hashing -- never inside
   `write_validation.py` or `execute_write.py`. The formatted text IS the
   approved payload: what is hashed, what the user reviews in
   `ApprovalCard`, and what the real (Phase 4G) deterministic execution
   path (`backend/api/execution_service.py`) replays VERBATIM to
   `teams_send_message`/Power Automate -- proven safe by direct audit of
   that replay path (it reads `proposal.payload["message"]` straight from
   the stored, already-approved proposal, never re-derives from model
   text). Formatting exactly once, before hashing, means there is no
   second, unapproved rewrite after approval, and (deliberately) means
   re-running the formatter on its own output is never exercised anywhere
   in this codebase (idempotency was considered and explicitly avoided as
   a requirement by construction, not assumed safe by accident -- see the
   module's own docstring). Frontend: `ApprovalCard.tsx`'s Message row now
   renders through a NEW `src/lib/safeHtmlFragment.tsx`
   (`renderSafeTeamsMessageHtml`) -- an allowlist-only `DOMParser`-based
   walker, deliberately NOT `dangerouslySetInnerHTML` (defense in depth):
   it reproduces ONLY the same six tags, NEVER copies any attribute from
   the source string (so an injected `onerror`/`href`/`style` is always
   discarded regardless of tag), and drops `<script>`/`<style>` content
   entirely. `docs/TEAMS_TOOL_CONTRACT.md` §6 updated to document this.
   22 new backend tests (`test_teams_message_formatting.py`) + 12 new
   frontend tests (`safeHtmlFragment.test.tsx`) + 2 new `ApprovalCard`
   tests, covering the full format scope, escaping/XSS-safety, and the
   approval-payload-binding proof. Five pre-existing tests in
   `test_teams_propose_write.py`/`test_teams_execute_write.py` were
   updated -- their fixtures previously called execute-time tools with the
   model's RAW original text (identity-transform-safe before this pass);
   now correctly supply the FORMATTED text, mirroring exactly what
   `execution_service.py` really replays -- the only existing assertions
   this item changed.

   CORRECTIVE PASS (real live-test finding): a genuine live Teams write
   (SLOPANOC -> Team Manager -> Incident Manager -> Power Automate ->
   Teams selection -> approval -> execution -> Microsoft Teams, confirmed
   end to end -- `teams.listChats outcome=ok`, selection `choose 200`,
   `approve 200`, `power_automate_gateway operation=teams.sendMessage
   outcome=ok`, `execute 200`) proved the HTML formatting above did NOT
   render as intended through the real Power Automate/Teams path -- the
   send succeeded, but presentation failed. `format_teams_message`
   (`backend/tools/teams/message_formatting.py`) was REWRITTEN to
   produce DETERMINISTIC, PROFESSIONALLY STRUCTURED PLAIN TEXT instead --
   never HTML, Markdown, or Adaptive Cards. `message`/`payload["message"]`
   remain, and have always been, a plain `str` end to end -- only the
   CONTENT changed (paragraphs stay paragraphs, `- item`/`* item`/`•
   item` bullets normalize to a single `•` marker per line, `1.`/`1)`
   numbered items normalize to sequential `N.` markers, a short line
   ending in `:` is kept as a plain heading line, blank-line block
   separation is preserved/normalized) -- no HTML escaping is applied or
   needed, since plain text is never parsed as markup by anything
   downstream: literal `<script>`/`<b>` text in the model's own answer
   now passes through byte-for-byte, unlike the (correct, but no longer
   necessary) escaping the HTML version required. The exact same trust
   boundary applies unchanged: formatted exactly once, in `teams_propose_
   send_message`, before hashing -- never inside `write_validation.py`/
   `execute_write.py`, which have zero reference to `format_teams_
   message` at all (structurally guaranteed, tested). Frontend:
   `ApprovalCard.tsx`'s Message row now renders `pendingAction.message`
   as ordinary React text (`{message}`, auto-escaped, no HTML parsing of
   any kind) with `whitespace-pre-wrap` so the formatter's own blank-line/
   per-line structure (real `\n` characters) remains visually correct.
   `src/lib/safeHtmlFragment.tsx`/`safeHtmlFragment.test.tsx` -- the
   allowlist-only HTML renderer built for the (now-obsolete) HTML path --
   were DELETED entirely (confirmed via grep: used nowhere else). The
   backend test file was rewritten in full for plain-text semantics (27
   tests, replacing the prior 22 HTML-oriented ones) -- single/multiple
   paragraphs, bullet/numbered lists, headings, blank-line normalization,
   Unicode/special-character/URL/command preservation, literal `<...>`/
   script-like text surviving byte-for-byte (never interpreted as
   markup), and the full formatter-runs-once/proposal-binding/verbatim-
   replay proof chain. `ApprovalCard.test.tsx`'s two HTML-specific tests
   were rewritten for plain-text rendering (still 43 total). `docs/
   TEAMS_TOOL_CONTRACT.md` §6 corrected to describe plain text, not HTML.

2. DELAYED HORIZONTAL SCROLL ON HOVER. Audit found the marquee-on-hover
   mechanism (`src/components/ui/ScrollingText.tsx`, already wired into
   `SidebarChatRow.tsx`) already existed, with correct per-row-isolated
   local state and correct overflow-only-when-genuine detection -- it
   simply started the scroll animation immediately on `mouseEnter`, with
   no delay and no `mouseLeave` cancellation. Fix: a new
   `HOVER_ACTIVATION_DELAY_MS = 1000` gate -- `handleEnter` now starts a
   1000ms `setTimeout` (cleared/replaced on any new enter) before calling
   the pre-existing `startScrolling` logic; `handleLeave` (new) clears any
   pending timer and, if the animation had already started, resets it
   immediately (`setRun(null)`) rather than letting an in-flight pass
   finish. An unmount effect clears any pending timer. No changes to
   `SidebarChatRow.tsx` -- each row already owns its own independent
   `ScrollingText` instance, so no cross-row coordination was ever needed.
   `prefers-reduced-motion` was already handled globally
   (`src/index.css`'s existing wildcard `@media (prefers-reduced-motion:
   reduce)` block collapses ALL animation durations to near-zero) -- no
   change needed. 10 new tests (`ScrollingText.test.tsx`, fake timers)
   proving the delay, per-row isolation, leave-before/after-activation
   cancellation, timer-leak safety, and unmount safety.

   CORRECTIVE PASS (real live-test finding): the live UI showed a
   tooltip/popup appearing on hover, which the product explicitly does
   not want -- hovering an overflowing row should ONLY ever produce the
   delayed scroll, nothing else. Root cause, found by direct inspection
   (never guessed): `ScrollingText`'s outer `<span>` set a native HTML
   `title` attribute, defaulting to the full, untruncated `children` text
   whenever no explicit `title` prop was supplied -- which is every real
   caller in this codebase (confirmed via grep: none of the 11 call
   sites, including `SidebarChatRow.tsx`, ever passed one). Browsers
   render a native tooltip from that attribute on hover, independent of
   and in addition to this component's own scroll animation -- this was
   an intentional accessibility-fallback decision in the original
   implementation, but not one the product wants. FIX: the `title`
   attribute (and the now-fully-unused `title` prop) were removed
   entirely from `ScrollingText.tsx` -- no replacement tooltip/popover of
   any kind was added. This is NOT an accessibility regression: CSS
   truncation (`overflow-hidden`/`text-ellipsis`) never removes the
   underlying DOM text node, only its visual rendering, so a screen
   reader (or any assistive technology reading DOM text content) still
   encounters the full, untruncated text exactly as before; for an
   interactive wrapper (`SidebarChatRow`'s own `<button>`), the
   accessible name is computed from that same full text content, never
   from the removed `title` attribute. No other tooltip/popover source
   was found for chat rows (grep confirmed the codebase's separate custom
   `Tooltip` component, used elsewhere in the sidebar for icon buttons,
   was never wired to `SidebarChatRow`/`ScrollingText`). 4 new tests
   replace the one now-obsolete "native title attribute is always
   present" test (13 total in `ScrollingText.test.tsx`), proving no
   `title` attribute and no `[role="tooltip"]`/Radix popper wrapper ever
   appears, at rest, mid-delay, or once scrolling. CLOSURE EVIDENCE (new
   `src/components/shell/SidebarChatRow.test.tsx`, 5 tests -- this
   component had zero prior test coverage of any kind, confirmed via
   grep, so these are new coverage, not duplicates): click/select still
   calls `onSelect`; opening the row menu and choosing Rename still
   enters edit mode and calls `renameChat` with the new title on Enter;
   choosing Pin/Unpin still calls `togglePin` with the chat id; all
   proven with a genuinely overflowing title so the interaction with this
   pass's own hover change is exercised in the same render, not merely
   asserted in isolation.

3/4. SENT IMAGE THUMBNAIL + PREVIEW MODAL. Audit found live-send and
   historical/hydrated images were ALREADY converged on one shared
   component (`PersistedImageAttachment.tsx`) -- no dual-path problem to
   fix. It rendered at up to `max-h-64` (256px) inline, with no click
   affordance. Fix: the successful-load branch now renders a compact
   thumbnail (`max-h-32 max-w-48 object-contain` -- bounded, no cropping/
   distortion, aspect ratio preserved) wrapped in a real `<button>`
   (`DialogTrigger asChild`) with a hover/focus-visible expand-icon
   overlay (`Maximize2`), opening a `DialogContent` (the SAME
   `src/components/ui/Dialog.tsx` Radix primitive used project-wide) that
   reuses the EXACT SAME `objectUrl` this component already fetched --
   NEVER a second `getAttachmentContent` call, never a re-upload, never a
   chat/attachment state mutation. Radix's own Dialog gives Escape-to-
   close, click-outside-to-close, a built-in accessible X (`aria-
   label="Close"`), and correct focus trap/return for free -- an `sr-only`
   `DialogTitle` supplies the required accessible dialog name. Because
   this is the ONE shared component for both live and historical images,
   the thumbnail/preview treatment applies identically to both without
   any special-casing. 23 tests total in `PersistedImageAttachment.
   test.tsx` (12 pre-existing, all still green unchanged + 16 new)
   covering aspect-ratio/sizing, click-to-open, Escape/X-close, zero
   re-fetch/re-upload/re-ingest on open or close, object-URL reuse (not a
   second `createObjectURL` call), multi-image correctness, and keyboard
   activation.

5. ATTACHMENT/TEXT FORMATTING SEPARATION IN THE COMPOSER. AUDIT FOUND
   THIS ALREADY CORRECT -- no code change was made. `PromptComposer.tsx`
   already renders `SourceChipRow`, `AttachmentChipRow`, and the real
   `<textarea>` as three independent DOM siblings under one padded
   container, each with its own explicit Tailwind classes; the text input
   was never `contenteditable`, and attachments were never inline tokens
   inside the text. 14 new regression tests
   (`PromptComposer.attachmentSeparation.test.tsx`, using the REAL
   `AttachmentChipRow` rather than the stubbed-out version
   `PromptComposer.test.tsx` uses for its own unrelated send-gating focus)
   lock this in: typed text is never affected by attach/paste/remove/
   retry, the textarea is never wrapped by or nested inside attachment
   markup, multiple attachments never leak styling onto the textarea, and
   no draft text is ever duplicated or lost across an attachment's
   lifecycle.

NO ARCHITECTURE EXPANSION: no Knowledge Agent, Troubleshooting Manager,
Head of Automated Operations, Context Engineering runtime, ITSM/alarms/
topology/KPI/Change/Handover, direct Microsoft Graph, new database, new
attachment ownership model, new agent hierarchy, or new orchestration
framework was introduced. No keyword/regex natural-language routing was
added anywhere.

REGRESSION: full backend suite 2685 passed, 1 skipped (2663 B7-closure
baseline + 22 new); KM-keyword subset 792 passed (unchanged); Teams-
keyword subset 315 passed (293 + 22 new); B6/B7 multimodal + attachment-
lifecycle + provenance-focused subset (MultimodalAgentTool, image fast-
path, image+Teams+KM integration, selection-continuation image
preservation, turn-context isolation, governed-completion image
evidence, attachment lifecycle, historical provenance persistence,
exact-duplicate provenance normalization) re-run explicitly and
unchanged at 71 passed; full frontend suite 737 passed (688 + 49 new);
`npm run build` clean; `npx tsc -b` clean.

CORRECTIVE PASS REGRESSION (hover-popup removal + plain-text Teams
formatting + closure-evidence tests): full backend suite 2690 passed, 1
skipped (2685 + 5 net new -- 27 rewritten plain-text formatter tests
replacing the prior 22 HTML-oriented ones); Teams-keyword subset 320
passed (315 + 5 net new); Teams write/approval/execution focused subset
(message formatting, propose/execute write, write validation, security
contract, prompt contracts, Power Automate client, execution endpoints,
approval service) 194 passed; full frontend suite 733 passed (737 - 12
removed `safeHtmlFragment` tests + 3 net `ScrollingText` change + 5 new
`SidebarChatRow` tests); `npm run build` clean; `npx tsc -b` clean;
`git diff --check` clean.

LIVE VALIDATION -- FINAL CLOSURE: the user has now completed the full
live validation pass this milestone required. All four refinements are
LIVE VALIDATED:

1. TEAMS OUTBOUND MESSAGE -- LIVE VALIDATED. Real Teams discovery, real
   selection, real approval, real deterministic execution, and a real
   Power Automate `teams.sendMessage` all confirmed working end to end.
   Plain-text delivery is correct. The user confirmed the visual
   presentation is effectively unchanged from the ORIGINAL (pre-B7)
   plain-text output and EXPLICITLY ACCEPTS this current plain-text
   presentation for the current milestone. This is a recorded PRODUCT
   DECISION, not an unresolved blocker: enhanced Teams visual formatting
   was evaluated during this milestone (HTML was attempted first, then
   rejected after a real live test proved it does not render as intended
   through the current Power Automate/Teams path -- see the corrective
   pass above), and further Teams presentation enhancement is
   INTENTIONALLY DEFERRED, not attempted again in this milestone. Do
   NOT reattempt HTML, do NOT introduce Adaptive Cards, do NOT modify
   Power Automate, do NOT redesign the Teams contract to chase richer
   visual formatting -- the deterministic plain-text path
   (`backend/tools/teams/message_formatting.py`) is the accepted,
   final implementation for this milestone.
2. SIDEBAR HOVER BEHAVIOR -- LIVE VALIDATED. User confirmed: no
   tooltip/popup of any kind appears; delayed scrolling works correctly;
   current behavior is correct as implemented.
3. SENT-IMAGE THUMBNAIL + PREVIEW MODAL -- LIVE VALIDATED. User
   confirmed: thumbnail presentation is correct; preview-modal behavior
   is correct.
4. COMPOSER ATTACHMENT/TEXT SEPARATION -- LIVE VALIDATED. User
   confirmed: attachment and typed text remain properly separated.

OBSERVATION, AUDITED, NOT ACTED ON (recorded, not reopened): an earlier
live run in this milestone's own corrective pass showed "I couldn't find
a Teams chat with the exact name ..." reappearing after an earlier
successful selection/approval/execution flow. Traced (read-only, no code
changed) to `backend/agents/team_manager/prompts.py` -- a pre-existing
prompt template in the frozen Teams read/selection flow. Zero file this
milestone has touched, across all three passes, overlaps with Teams chat
resolution, `SelectionCard`/`selection_service.py`, or any orchestration/
routing code -- confirmed not a POST-B7 regression, left untouched.

STATUS: DONE. POST-B7 UI/UX REFINEMENT MILESTONE IS COMPLETE. All five
refinements (Teams message formatting -- plain text, accepted;
delayed hover-scroll with no tooltip; compact image thumbnail; image
preview modal; composer attachment/text separation) are implemented,
automated-tested, and live-validated. NEXT (at the time this milestone
closed): A5 -- Knowledge Island Ingestion Foundation + Real TELCO/RAN
Compound Knowledge Validation (see its own entry below) -- since
COMPLETE and live-validated. (HISTORICAL, as of this milestone's own
closure: "Phase 4H security hardening is NEXT" was true at that point,
before the post-A5 roadmap realignment -- see the "ROADMAP REALIGNMENT"
note near the top of this LOCKED ROADMAP section and
docs/BUILD_SEQUENCE.md Sec 2a for the current order: 5.X is now NEXT,
followed by Phase 6A, then Phase 4H, then 5.2-5.7, then Phase 6B.)

===================================================================

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

A5 — KNOWLEDGE ISLAND INGESTION FOUNDATION + REAL TELCO/RAN COMPOUND
KNOWLEDGE VALIDATION. **IMPLEMENTATION COMPLETE — REAL LIVE-RUNTIME
(Gemini/Vertex, Cloud SQL) VALIDATION PENDING.** Proves the EXISTING
Generic Governed Knowledge architecture (unchanged: domain/governance/
repository/retrieval/provenance/tools boundaries) can safely ingest,
structure, persist, and cite real COMPOUND operational knowledge
(DOCX/XLSX/PDF/TXT, embedded/nested artifacts, images) — never a
one-off MOP-specific pipeline, never a new agent, never a new
Knowledge Agent, never Phase 4H/6/7 architecture.

GENERIC COMPOUND-ARTIFACT DOMAIN MODEL (new, additive-only):
`backend/knowledge/domain/artifacts.py` — `KnowledgeArtifact` (open
`kind` string, never a closed enum — mirrors `KnowledgeSection
.section_type`'s own open-string design), `ArtifactExtractionStatus`
(COMPLETE/PARTIAL/FAILED/SKIPPED, a genuinely bounded state machine),
`validate_artifact_lineage` (unique `artifact_id`, no dangling
`parent_artifact_id`). `IngestedKnowledgeDocument`, `KnowledgeSection`
(+`StructuredKnowledgeSection`), and `KnowledgeObject` each gained one
additive, optional field (`artifacts`/`artifact_id`) — every existing
plain-text document/section is byte-for-byte unaffected (`artifacts=[]`,
`artifact_id=None`); `KnowledgeEvidenceReference`/`KnowledgeEvidenceItem`
(provenance) gained the matching optional `artifact_id`/`artifact`
field, resolved by `provenance/service.py`'s `build_evidence_set`
ONLY from the same freshly-refetched governed object the section itself
is revalidated against (never a caller/model claim) — hierarchical
provenance (section → artifact → parent artifact → ... → root) flows
through the EXISTING evidence architecture, never a parallel citation
system. `governance/service.py`'s `materialize_candidate` carries
`artifacts`/`section.artifact_id` through unchanged, and gained one new
optional, explicit `section_roles: dict[section_key, section_type]`
kwarg (trusted-caller-only, never inferred — 5.1E still never fabricates
a semantic type on its own initiative) so an operator/ingestion-time
caller may assert PROCEDURE/EXAMPLE_OUTPUT/etc. authority distinction
using the section_type field that was already fully open. **No Alembic
migration was needed or added** — `slopanoc_knowledge_objects`' existing
`payload` JSON-blob column absorbs the new artifact/lineage data
transparently (empirically proven: a real artifact-bearing
`KnowledgeObject`, including a `storage_ref`, round-trips through
`SqlAlchemyKnowledgeRepository` — dialect-neutral, so this holds for
SQLite and Cloud SQL PostgreSQL identically).

EXTRACTION (`backend/knowledge/ingestion/{extraction.py,extractors/}`,
all inside the existing, unchanged `backend/knowledge/` dependency
boundary — `python-docx`/`pypdf`/`openpyxl` are document-format
parsers, not cloud/vendor SDKs, so they may live here; confirmed by the
existing, unmodified `test_dependency_boundary.py`):
`extraction.py` — SHA-256 `hash_bytes`, deterministic
(non-random-UUID) `deterministic_artifact_id` (stable across re-
ingestion of identical content — the structural basis for idempotent
re-ingestion), `ExtractionLimits`/`ExtractionBudget` (defensive
recursion-depth/artifact-count/artifact-size/total-expanded-size
bounds, new `Settings.knowledge_ingestion_max_*` properties, defaults
chosen against this milestone's real corpus with headroom, never
tuned to one file), `sniff_media_type` (real OOXML `[Content_Types]
.xml`/PDF-magic-byte container inspection — extension never trusted,
per instruction). `extractors/docx.py` — real DOCX paragraph/heading/
table/hyperlink extraction in true document order (the standard
`_iter_block_items` OOXML-body-walk idiom, not `.paragraphs`+`.tables`
naively re-joined), Word heading styles promoted to Markdown ATX
syntax so the EXISTING, UNMODIFIED 5.1D `HeadingStructureProcessor`
segments DOCX text with zero new processor; embedded media/objects
discovered via the real `word/_rels/document.xml.rels` relationship
graph (with a raw-member-scan fallback), a `vbaProject.bin` (macro)
member is reported/skipped, never executed. `extractors/xlsx.py` —
Workbook → Sheet → header/row structure (never one flattened blob),
formulas read as literal strings (`data_only=False`) and NEVER
evaluated, a header-only template still yields real, retrievable
content. `extractors/pdf.py` — one artifact per page, page text +
1-based page locator, encrypted PDFs detected and reported (never
guessed); `pypdf` has no embedded-action/script execution capability
at all, so no explicit disabling step exists or is needed; OCR is
deliberately not implemented (out of scope). `extractors/txt.py` —
verbatim line-structure preservation. `extractors/dispatch.py` — the
recursive orchestrator (`extract_root_document`/
`extract_embedded_artifact`): every embedded object's real container
format is sniffed (never trusted from its internal member name),
recursion only continues for a further-recursable kind (currently
DOCX), a defensive limit or an unrecognized/legacy-OLE/encrypted
embedded object becomes ONE skipped/failed artifact node — never
aborts the parent's own processing (partial failure/atomicity,
instruction section 46) — proven live against a real 3-level-deep
synthetic fixture and against the real corpus (see below). Zero path
traversal surface exists BY CONSTRUCTION: every extractor reads only
`ZipFile.read(member) -> bytes` into memory; nothing is ever written
to a filesystem path, so a malicious member name (e.g. `"../../evil
.bin"`) has no traversal target at all (proven by test).

DURABLE ARTIFACT STORAGE (Layer B) — `backend/knowledge_ingestion/
artifact_storage.py` (concrete `google.cloud.storage` user; lives
OUTSIDE `backend/knowledge/` entirely, exactly like `backend/tools/
knowledge/` lives outside `backend/knowledge/tools/`, because
`google.cloud`/`google.adk`/`google.genai` imports are forbidden
anywhere under `backend/knowledge/`, enforced by the existing,
unmodified dependency-boundary test): a deliberate, line-for-line-
inspired mirror of `backend/attachments/storage.py`'s pattern (lazy
client construction, `put_bytes_if_absent`/`get_bytes`/`exists`/
`uri_for`) but a SEPARATE module/bucket setting
(`SLOPANOC_KNOWLEDGE_ARTIFACTS_BUCKET`, independently resolved, never
falling back to `SLOPANOC_CHAT_ATTACHMENTS_BUCKET`) and, critically,
NO chat-attachment `READY/LINKED/DELETED` lifecycle at all — a
Knowledge artifact's authority is decided entirely by the existing
CANDIDATE/APPROVED/ARCHIVE governance boundary, a genuinely different
question. Object keys are CONTENT-HASH-ADDRESSED
(`knowledge-artifacts/<hash[0:2]>/<hash>`), never attachment-id-
addressed — this makes deduplication (instruction section 34, Case C:
"different parent MOPs, same embedded binary -> deduplicate, preserve
both parent relationships") a structural property of the key itself,
proven both synthetically and against the real corpus (see below).
**LIVE-VALIDATED against the real, already-provisioned GCS project**
(`pr-msn-dev-gl-slopai-01`, Application Default Credentials — real,
not mocked): a real upload, a real second-upload-correctly-skipped
(content-hash dedup), a real byte-identical download round-trip, real
`gs://` URI construction, and a full self-cleaning delete leaving zero
residue — using synthetic, non-sensitive payload bytes (never real
corpus content) against the real, existing `slopanoc-chat-attachments-
sandbox01` bucket's project (no dedicated `slopanoc-knowledge-
artifacts-*` bucket has been provisioned yet — this is the SAME kind
of "production deployment identity/configuration not yet exercised"
gap README.md's own CURRENT LIMITATIONS section already documents for
chat attachments, not a new one; provisioning a dedicated bucket is an
infrastructure action outside this implementation pass's authority).

MULTIMODAL IMAGE INTERPRETATION BOUNDARY (Layer G) —
`backend/knowledge/ingestion/image_interpretation.py`: a generic,
ADK/Gemini-independent `ImageInterpreter` Protocol (mirrors
`KnowledgeSourceAdapter`/`KnowledgeContentProcessor`'s own Protocol-
then-concrete-implementation pattern) plus `apply_image_interpretation`
(pure orchestration: updates only `kind="image"` artifacts that have
raw bytes available, marks a successful result `derived=True`
[instruction section 16: SOURCE vs DERIVED — the original image binary
in durable storage always remains the source of truth, never
overwritten by the model's own description text], marks a failed
result `extraction_status=PARTIAL` with the real error preserved,
never raises, never fabricates a description, never lets one image's
failure affect any other artifact). The concrete implementation,
`backend/knowledge_ingestion/gemini_image_interpreter.py`, reuses the
EXISTING shared model client (`get_shared_llm()`,
`backend/config/settings.py` — the SAME process-cached `BaseLlm`
instance `team_manager`/`incident_manager` themselves use; no second
Gemini/Vertex client architecture was built) via the SAME bounded,
one-shot `Agent`+`Runner`+`InMemorySessionService` pattern this
codebase already uses four other places (`provenance_compliance.py`,
`governed_knowledge_completion.py`, `source_requirements_completion
.py`, `read_continuation_execution.py`) — no tools, so it structurally
cannot execute anything, approve knowledge, or set applicability/
lifecycle; the image's own pixel content is treated as untrusted
visual evidence to describe, never an instruction to follow (mirrors
the existing B6 "IMAGE EVIDENCE" prompt principle). **Live-attempted
against this session's real environment**: real `google.cloud.storage`
access worked (Application Default Credentials), but real Gemini/
Vertex access did NOT — this backend's model client currently resolves
to Google AI Studio mode with no API key configured in this session
(`GOOGLE_GENAI_USE_VERTEXAI` unset), not the deployed environment's
real Vertex AI path; the attempt correctly failed CLOSED with a clear,
captured error (`succeeded=False`, real error text preserved, no
crash, no fabricated description) — itself a genuine, useful proof
that the Layer G failure path behaves correctly under a REAL failure
condition, not merely a synthetic mock. Real live image interpretation
therefore remains VALIDATION PENDING the deployed environment's own
Vertex AI configuration — do not claim it has been live-proven.

ADMIN/DEV INGESTION ENTRY POINT (instruction section 52) —
`backend/knowledge_ingestion/local_file_adapter.py`:
`ingest_local_file`/`ingest_local_files` accept ONLY explicit file
paths (never recursively scan a filesystem location), are READ-ONLY
(never mutate/move/delete the source file — proven by test), and
independently, gracefully degrade when `storage`/`interpreter` are
unconfigured (a structural-only run still succeeds and reports
`storage_configured=False`, never a hard failure) — this is
deliberately NOT a public upload API/UI (none was built). Produces a
structural-only `IngestionReport` per file (instruction section 53:
artifact counts by kind, max nesting depth, skipped items with
reasons, upload/dedup/image-interpretation counts) — never real
document content, safe to log.

ONE-COMMAND-AT-A-TIME BEHAVIOR (Layer K, instruction section 6/7) —
PROMPT-ONLY change, `backend/agents/incident_manager/prompts.py`'s
`INCIDENT_MANAGER_INSTRUCTION` gained two new paragraphs
("ITERATIVE TROUBLESHOOTING -- ONE CHECK/COMMAND AT A TIME" and
"COMMAND TRUST AND PRESERVATION"): the DEFAULT posture for a
troubleshooting question is one grounded diagnostic action per turn,
then wait for the user's evidence before the next one — never a fixed
truncation engine, since an explicit user request for the full
procedure is honored in the same turn (natural-language judgment, no
keyword/regex routing, consistent with this codebase's existing
"no keyword-based intent routing" invariant); commands must be
reproduced exactly from Approved, selected governed knowledge, never
paraphrased/invented, with example/reference evidence explicitly never
gaining normative authority merely by resembling a procedure. No new
Troubleshooting Manager, no persistent Troubleshooting State, no
hypothesis engine — this is ordinary conversational judgment from
existing session context plus the existing `knowledge_search`/
`knowledge_select_evidence` tools, exactly as instructed.
`INCIDENT_MANAGER_INSTRUCTION`'s own byte-length regression assertion
(`test_p4b2_synthesis_compression.py`, the same test that has tracked
every prior legitimate prompt extension since P4B.2) was updated for
this addition (45707 -> 48730 chars) — the only existing assertion
this change required, and only because it is a deliberately exact
length check.

REAL CORPUS VALIDATION (instruction section 68) — `backend/tests/
test_knowledge_real_corpus_validation.py`, auto-skipping (never
fabricating a result) if the three real files are unavailable, reads
them directly from their real, external, out-of-repository paths
(never copied/moved/staged/committed) and PASSED, live, in this
environment: all 3 real root documents recognized; `Document1.docx`
correctly has zero compound structure (plain text) and its critical
VSWR-Over-Threshold "No restart" rule survives extraction verbatim,
distinct from the document's other 7 restart-allowed alarm cases; both
real Rogers MOPs are correctly recognized as compound (19 artifacts
each: 1 embedded DOCX carrying 7 of its OWN nested images at depth 1 —
real 2-level recursion — 1 embedded XLSX with 1 real sheet, 1 legacy OLE
object [CORRECTED by the A5 corrective pass below: this OLE object's own
real embedded TXT/log payload, "EnodeB_HC.txt", is now safely extracted
rather than merely reported as unsupported — this original finding was
itself incomplete, not merely a documentation gap], and 8 root-level
images); every artifact has a real,
deterministic SHA-256 `content_hash`; the two real Rogers documents
share at least one byte-identical embedded artifact (their common
"Microsoft_Word_Document.docx"/"Microsoft_Excel_Worksheet.xlsx"
boilerplate attachments) with matching `content_hash` — real Case-C
deduplication evidence, not synthetic; parent/child lineage is
internally consistent for every nested artifact. This test file itself
contains no real MOP paragraph text, no extracted screenshots, no
internal IPs/hostnames/URLs (a dedicated self-check test proves this
about its own source).

DEPENDENCIES ADDED (all justified, all pinned in requirements.txt):
`python-docx==1.2.0` (DOCX text/headings/tables/relationships/embedded-
object discovery — nothing else in this codebase covers this),
`pypdf==6.15.0` (mature, bounded PDF page text extraction, no
script/action execution capability), `openpyxl==3.1.2` (XLSX workbook/
sheet/cell structure, formulas read as literal data, never evaluated)
— the latter two were already present in this development environment
but unpinned/undeclared; both are now deliberately pinned to the
versions this environment already runs and tests against, matching
this repository's existing "pinned to what this environment currently
runs against, proven by the test suite" convention. No macro/VBA
execution library, no OCR library, no external vector database, no new
agent orchestration framework.

REGRESSION (original implementation pass; see CORRECTIVE PASS below for
the current, superseding counts): full backend suite 2822 passed, 1
skipped (2690 POST-B7 baseline + 132 net new — ~185 new A5 tests across
domain/ingestion/processing/governance/provenance/local-file-adapter/
real-corpus layers, minus a handful of pre-existing tests intentionally
updated for legitimate, documented contract extensions:
`IngestedKnowledgeDocument`'s field allow-list, `KnowledgeEvidenceItem`'s
field allow-list, and `INCIDENT_MANAGER_INSTRUCTION`'s exact-length
assertion — the only existing assertions this milestone changed, and
only because each is a deliberately exhaustive/exact check);
KM-focused subset (`backend/tests/knowledge/`) 807 passed; Incident
Manager/Teams/B6/B7 multimodal/attachment-lifecycle/history-provenance
subsets all re-run unchanged; `npm run build`/`npx tsc -b` both clean
(no frontend file was touched by A5); `git diff --check` clean.

NOT BUILT, BY DESIGN, PER EXPLICIT INSTRUCTION: no `MopRepository`/
`SopRepository`/`RcaRepository`/vendor-named KM table or pipeline, no
Knowledge Agent, no Troubleshooting Manager, no Head of Automated
Operations, no Context Engineering runtime, no persistent
Troubleshooting State, no ITSM/Alarm/Topology/KPI/Change/Handover
integration, no direct Microsoft Graph, no autonomous execution, no
arbitrary shell/SQL/HTTP tool, no external vector database, no Alembic
migration (proven unnecessary), no SharePoint/GCS/Drive/Confluence
concrete source adapter (only the local-file admin/dev adapter this
milestone's own instruction calls for), no OCR pipeline, no public
knowledge-upload API/UI.

STATUS AT THIS POINT (superseded — see "A5 FINAL LIVE-RUNTIME RETEST"
and the "STATUS: A5 ... is COMPLETE" block further above for the
authoritative, current status): **A5 — IMPLEMENTATION COMPLETE; REAL
LIVE-RUNTIME (Gemini/Vertex, Cloud SQL PostgreSQL) VALIDATION PENDING**
the deployed environment's own Vertex AI/Cloud SQL configuration (not
available in this implementation session — see above for exactly what
WAS/was not live-validated: real GCS storage was live-proven; real
Gemini multimodal interpretation and real Cloud SQL persistence for
artifact-bearing objects were not, though SQLite persistence of the
identical dialect-neutral schema was proven, and the Gemini failure
path itself was proven correct under a real failure condition). Do not
mark A5 fully COMPLETE, and do not start Phase 4H, until a full
real-runtime pass (React → FastAPI → Team Manager → Incident Manager →
Generic Governed Knowledge → Cloud SQL → Gemini/Vertex) — the same kind
of live pass every prior milestone in this project has required a human
to perform in the real deployed environment — confirms retrieval
quality, one-command behavior, and grounded citation against Approved
real corpus knowledge end to end.

A5 CORRECTIVE PASS — three targeted implementation/evidence gaps closed
before real-runtime validation begins, per an independent pre-A5 audit
of the real corpus:

CORRECTION 1 — REAL EMBEDDED OLE TXT/LOG SUPPORT. The original pass's
"1 unsupported legacy OLE object" finding was itself incomplete: both
real Rogers MOPs' `word/embeddings/oleObject1.bin` is a genuine OLE2
Compound-File-Binary (CFB) container (magic bytes confirmed) wrapping a
classic OLE 1.0 "Package" (`Insert Object > Create from File`) embed —
its real payload is a health-check log file named `EnodeB_HC.txt`
(confirmed byte-identical, 163 lines, zero non-printable characters, a
real AMOS terminal session capture, in BOTH real Rogers MOPs). New
module `backend/knowledge/ingestion/extractors/ole.py`:
`is_ole_compound_file`/`extract_ole_package_payload`, using ONLY
`olefile` (new dependency, pinned `olefile==0.47`) — a narrow, mature,
pure-Python OLE2-CFB reader with NO code-execution capability of any
kind (no VBA/macro interpreter, no COM/Word/Excel automation, no shell/
subprocess) — never a broader office-automation/forensics framework
(`oletools` was evaluated and explicitly rejected for this reason: its
own dependency tree pulls in a GUI file-dialog library and a VBA
p-code decompiler, neither appropriate here). Parses the well-documented
`"\x01Ole10Native"` stream layout (size/version/filename/paths/reserved
bytes/payload-length/payload) via plain `struct` unpacking on bytes
already read by `olefile` — never guesses: any structural inconsistency
(bad length, missing stream, corrupt directory, not a CFB file at all)
returns `None`, and the caller falls back to the pre-existing safe
"legacy OLE object, not further decomposed" SKIPPED artifact, exactly
as before this correction. Wired into `extractors/dispatch.py`'s
existing OLE2-detection branch: a successfully-parsed payload becomes a
NEW `kind="ole_package"` container artifact (COMPLETE, replacing the
old always-SKIPPED `embedded_object` classification for this case) with
the extracted payload recursively dispatched as ITS OWN child artifact
through the EXISTING generic pipeline (by extension/content: `image`/
`docx`/`xlsx`/`pdf`/`txt_log`/`unsupported_artifact` — no OLE-specific
child-classification logic was added). ARTIFACT ROLE: the extracted log
is `kind="txt_log"` (never a normative-procedure kind); the existing,
unmodified Incident Manager prompt language ("EXAMPLE/REFERENCE
evidence — a screenshot, HISTORICAL OUTPUT, or sample value the source
shows only as illustration") already covers a terminal/log capture
explicitly, so no prompt change was needed or made. 17 new focused
tests (`test_ingestion_extractors_ole.py`, synthetic-only — a hand-built
minimal CFB container, since `olefile` is read-only and cannot itself
construct fixtures) covering safe discovery, TXT extraction, line
preservation, parent lineage, deterministic hashing, a structural
no-execution proof (AST-level: the module imports/calls no
subprocess/COM/exec-capable name), malformed/corrupt-CFB safe failure,
unsupported-OLE safe reporting, and budget/depth enforcement. Real
corpus re-validated: `EnodeB_HC.txt` now discovered in BOTH real Rogers
MOPs, correctly parented under a new `ole_package` container artifact,
zero unsupported/skipped OLE objects remaining, zero sensitive log
content persisted into any repository file.

CORRECTION 2 — CONTENT DEDUP VS. LINEAGE OCCURRENCE, PROVEN. Audited
and PROVEN CORRECT with no bug found: `deterministic_artifact_id`
derives an artifact's own identity from `(parent_artifact_id, kind,
position, content_hash)` TOGETHER, never `content_hash` alone — so
CONTENT IDENTITY (the sole input to GCS's content-addressed storage
key, `build_artifact_object_name`) and ARTIFACT OCCURRENCE IDENTITY
(`artifact_id`/`parent_artifact_id`) are structurally distinct by
construction. 5 new proof tests
(`test_ingestion_dedup_lineage.py`, synthetic) covering all three
conceptual cases the corrective instruction distinguished: Case A
(identical binary, two separate root documents — proven via the
pre-existing cross-document test plus a new explicit one), Case B
(identical binary occurring twice within ONE root under two DIFFERENT
parent artifacts — both occurrences survive as two distinct artifact
records with distinct `artifact_id`/`parent_artifact_id`, sharing only
`content_hash`), Case C (identical binary at two different nesting
depths within one root — same result). No correction to the domain
model was required — the existing design already satisfies "one stored
binary, many independently-recoverable occurrences."

CORRECTION 3 — NESTING DEPTH SEMANTICS, AUDITED AND DOCUMENTED. The
original "max depth 1" reporting is VALID under the design's own,
already-documented convention: the root document itself is never
represented as an artifact at all (`KnowledgeArtifact`'s own module
docstring), so `depth=0` means "embedded directly in the root" and
`depth=1` means "nested one level inside that" — confirmed self-
consistent, no correction needed. The invariant that actually matters
— "the full parent chain must be recoverable deterministically" — was
proven directly against the real corpus (`test_root_to_embedded_docx_to_nested_image_hierarchical_provenance`):
walking `parent_artifact_id` from a real nested image back through the
real embedded Word document to the (unrepresented) root reconstructs
the exact, correct chain, independent of the absolute depth numbers.

REGRESSION (A5 corrective pass, supersedes the original pass's counts
above): full backend suite **2846 passed, 1 skipped** (2822 original-
pass baseline + 24 net new); KM-focused subset **829 passed** (807 +
22); `npm run build`/`npx tsc -b` both clean (no frontend file touched);
`git diff --check` clean. New dependency: `olefile==0.47` (justified
above). STATUS UNCHANGED (at that point): **A5 — IMPLEMENTATION
COMPLETE; REAL LIVE-RUNTIME VALIDATION PENDING** — that corrective pass
closed implementation/evidence gaps only; it did not attempt, and does
not claim, live Gemini/Vertex or Cloud SQL validation.

A5 FIRST LIVE-RUNTIME VALIDATION PASS (real Vertex Gemini, real Cloud
SQL PostgreSQL, real GCS, real FastAPI app — never direct Gemini calls
for conversational acceptance tests): most gates passed live, including
the two most safety-critical ones (VSWR restart prohibition; mandatory
conflict isolation between the 4G-only and combined 4G/5G documents'
differing timing values, never blended). One gate failed reproducibly,
twice, including after a legitimate prompt-strengthening attempt: the
model did not reliably stay to one diagnostic action/command at a time
by default — it gave 3-4 step numbered procedures even when asked "what
should I check?" Per this project's own explicit stop condition, A5 was
NOT marked COMPLETE at that point; it remained **A5 — IMPLEMENTATION
COMPLETE / REAL LIVE-RUNTIME VALIDATION PENDING**, with the exact
blocker recorded (prompt-only self-restraint proven unreliable, twice).

A5 FINAL CORRECTIVE IMPLEMENTATION PASS — closes the one-command defect
with a deterministic runtime mechanism (never a third attempt at a
stronger prompt paragraph), plus three further live-testing findings,
per explicit instruction limiting production-code changes to exactly
these four bounded areas:

CORRECTION A — deterministic one-command-at-a-time enforcement.
`backend/agents/incident_manager/schemas.py` gained a typed
`TroubleshootingGuidance` field (`TroubleshootingInteractionMode`:
`NEXT_STEP`/`FULL_PROCEDURE`; `TroubleshootingStep`) on
`IncidentManagerResponse`, populated via Gemini's own schema-guided
structured output — far more reliable than free-text self-restraint.
`backend/api/troubleshooting_guidance_context.py` (NEW) is a run-scoped,
in-process store (same proven pattern as `multimodal_turn_context.py`)
plus `render_troubleshooting_guidance`, a pure deterministic Python
renderer: in `NEXT_STEP` mode it structurally NEVER reads
`full_procedure_steps` at all — leaking a later step is not a prompt
failure mode to guard against, it is a code path that does not exist.
`evidence.py`'s existing `after_agent_callback` captures/renders it as
defense in depth; `chat_service.py` gained a HARD completion-boundary
override that unconditionally replaces the turn's final answer with the
deterministic rendering whenever `troubleshooting_guidance` was
populated — mirroring the exact, already-proven
`enforce_governed_knowledge_at_completion` pattern (Python emits a typed
field as `final_text` with zero model paraphrase involved). Default is
`NEXT_STEP`; `FULL_PROCEDURE` requires the user to actually ask for the
full procedure (model semantic judgment, deterministically rendered —
never keyword/regex routing). NOT Phase 7: no Troubleshooting State, no
hypothesis engine, no diagnostic graph — a single typed field on one
existing response schema, rendered by one pure function.

LIVE DEFECT FOUND AND FIXED DURING THIS CORRECTION (real, reproducible,
found via direct live-stack testing, not anticipated by the
instruction): the first live rerun showed `troubleshooting_guidance`
genuinely being populated by the model (confirmed by direct
instrumentation) but the user-visible answer still not matching the
deterministic renderer's own output at all. Root cause, found by
tracing the exact code path: `chat_service.py`'s own turn-scoped
cleanup `finally` block called `discard_troubleshooting_guidance(run_id)`
BEFORE the later completion-boundary override ever got to
`pop_troubleshooting_guidance(run_id)` — the captured guidance was wiped
by this codebase's own cleanup discipline before it could be consumed,
silently falling back to team_manager's own free-text paraphrase (the
exact unreliable behavior this correction exists to eliminate). Fixed by
adopting the SAME snapshot-before-discard shape this file already uses
for `selected_knowledge_evidence` immediately above it in the same
`finally` block: the guidance is now popped (read + cleared) into a
local variable inside that early `finally`, and the later override
consumes the local snapshot rather than re-popping an already-cleared
store. Re-verified live immediately after the fix: the rendered answer
now matches `render_troubleshooting_guidance`'s exact output
byte-for-byte. Full backend regression re-run clean after this fix
(counts below) — this was the single most safety-relevant defect found
in this entire A5 effort, since it is the exact mechanism the whole
correction exists to guarantee.

SEPARATE LIVE DEFECT FOUND AND FIXED (Correction D wiring, found via the
full backend suite, not live Gemini testing, but would have broken real
production conversational turns): `IncidentManagerRequest.
known_applicability_facts` was first declared as
`dict[str, list[str]] = Field(default_factory=dict, ...)`, which
triggered `pydantic_core.PydanticSerializationError: Unable to serialize
unknown type: ..._HAS_DEFAULT_FACTORY_CLASS` inside ADK's OWN production
tracing code (`google.adk.telemetry.tracing.trace_call_llm`) on every
real LLM call through incident_manager — 24 test files failed with this
exact error. Fixed by changing the field to
`Optional[dict[str, list[str]]] = Field(default=None, ...)` (plain
`None` default instead of `default_factory=dict`); confirmed clean
against the isolated failing test and then the full suite.

CORRECTION B — EMF/WMF image understanding. Real live-validation
testing had found 4 of 15 real embedded images failed Gemini
interpretation outright (`400 INVALID_ARGUMENT: Provided image is not
valid`) because they are EMF/WMF vector images, not a raster format
Gemini accepts. Audited real corpus EMF bytes directly; found Pillow
(already a pinned dependency — no new dependency added) can rasterize
them via its own bundled `WmfImagePlugin`/Windows GDI path
(`Image.core.drawwmf`), confirmed against 3 real corpus EMF files
producing genuinely meaningful, non-blank raster output.
`backend/knowledge/ingestion/image_interpretation.py` gained
`rasterize_vector_image` — attempts PNG rasterization for
`image/x-emf`/`image/x-wmf` before interpretation, falls back to the
original bytes/media type on any failure (never a crash, never a
regression versus prior behavior), and records
`rasterized_from`/`rasterized_to` provenance metadata. Honestly
documented limitation: this is a Windows-GDI-specific mechanism: it may
not be available on a Linux/Cloud Run production deployment, in which
case rasterization safely returns `None` and behavior degrades to
exactly the pre-correction path, never a hard failure. No Office/COM
automation, no shell/macro execution, no new dependency.

CORRECTION C — native-source-evidence precedence over derived evidence.
Live testing had found a query for native XLSX report fields sometimes
cited the image-derived description of a spreadsheet screenshot instead
of the real XLSX header text. `KnowledgeRetrievalItem` gained
`is_derived: bool`. `backend/knowledge/retrieval/service.py`'s ranking
now buckets relevance score first (`_relevance_bucket`, width `0.15`,
deliberately generic — not query-tuned, not XLSX/Rogers-specific), then
tie-breaks WITHIN a bucket by `is_derived` (native wins), then exact
relevance, then the existing applicability/identity tie-breakers — so
native content wins only among "sufficiently similar" relevance
candidates, never suppressing a genuinely more-relevant derived
candidate outright.

CORRECTION D — live applicability-context propagation.
`ApplicabilityContext` was always empty at runtime; document selection
came from lexical ranking/model reasoning alone, never deterministic
applicability. `IncidentManagerRequest` gained
`known_applicability_facts: Optional[dict[str, list[str]]]`, populated
by team_manager's OWN model ONLY from facts EXPLICITLY, LITERALLY stated
in the CURRENT user message — never inferred, mirroring the existing
trust discipline already governing `chat_topic`/`question`. A NEW
`before_agent_callback` on incident_manager
(`capture_known_applicability_context`, using the same
`ReadonlyContext.user_content` mechanism `MultimodalAgentTool` already
relies on) captures it into a new run-scoped store
(`backend/api/applicability_context_capture.py`), consumed by
`backend/tools/knowledge/runtime.py`'s `get_or_init_run_state` on first
`knowledge_search` call. Model-inferred/suggested facts never reach this
path; when no explicit fact is stated, `ApplicabilityContext` stays
empty exactly as before this correction (safe default, never a guess).
Not Phase 6 Context Engineering; no hardcoded TELCO vocabulary — the
dimension keys are open, whatever the model captures verbatim.

FOCUSED TESTS (all new, all passing): 14 tests for
`troubleshooting_guidance_context.py`'s store/renderer (NEXT_STEP never
leaks later steps; FULL_PROCEDURE allows multiple grounded steps;
command preservation; no persistent state), 8 tests for `evidence.py`'s
capture/render wiring, 10 tests for `rasterize_vector_image` (wrong
media type no-op, malformed bytes safe failure, successful
rasterization, Pillow-exception fallback, provenance metadata, wiring
into `apply_image_interpretation`), 7 tests for the source-precedence
tie-break (equivalent relevance → native wins twice; highly-relevant
derived still outranks barely-relevant native; unrelated native never
outranks relevant derived), 14 tests for
`applicability_context_capture.py`'s store/callback/`get_or_init_run_
state` consumption, 5 end-to-end tests through the REAL, unmodified
`KnowledgeRetrievalService.retrieve()` proving known-4G-context MATCH,
known-4G-context excluding a conflicting 5G-only document,
known-4G5G-context MATCH, missing-discriminator UNKNOWN (never a guess,
never blended), and NOT_APPLICABLE never overridden by high lexical
relevance.

REGRESSION (final corrective pass, supersedes all prior counts): full
backend suite **2904 passed, 1 skipped** (2846 corrective-pass-#1
baseline + 58 net new); KM-focused subset **965 passed**; Incident
Manager-focused subset **76 passed** (unchanged — none of the new test
files match that keyword filter by name); `npm run build`/`npx tsc -b`
both clean (no frontend file touched by this pass); `git diff --check`
clean.

A5 FINAL LIVE-RUNTIME RETEST (real Vertex Gemini 2.5 Flash, real Cloud
SQL PostgreSQL, real GCS, the real FastAPI app via ASGI — never direct
Gemini calls for conversational tests). The 3 real corpus files were
re-ingested (previous `A5-VALIDATION-*` records deleted first) so the
EMF-rasterization correction was genuinely exercised — read-back
confirmed **15/15 images interpreted, 0 failed** for both Rogers
documents (previously 11/15, 4 failed). All 8 mandatory retests passed:

Test A (one-command, first turn): PASSED — exactly one diagnostic
action, one login+read command sequence, evidence requested; content
matched `render_troubleshooting_guidance`'s deterministic output
byte-for-byte.
Test B (same-session follow-up): PASSED — exactly one further action,
one command, deterministic rendering confirmed.
Test C (full-procedure override, explicit "give me the full procedure,
all steps"): PASSED — `FULL_PROCEDURE` mode correctly selected, 7
grounded numbered steps with real commands, deterministic rendering
confirmed.
Test D (state-changing prerequisite, two turns): PASSED — with no
diagnostic evidence yet, the turn gave a safe descriptive overview with
NO concrete/executable command at all (this specific bare-statement
wording, without an explicit question, did not populate
`troubleshooting_guidance` — a live wording-sensitivity worth recording
honestly, but the underlying safety property held: nothing executable
leaked). With evidence supplied on the second turn, the response
correctly gave exactly one further diagnostic action (determine DUS vs.
Baseband unit type) and explicitly withheld the state-changing reset
command until that prerequisite is confirmed.
Test E (native XLSX preference): PASSED — the real native XLSX header
row was cited verbatim, with no image-derived screenshot description
substituted.
Test F (known 4G applicability context): PASSED with a recorded nuance
— known facts were correctly captured and both genuinely-applicable
documents (the 4G-only and the 4G/5G-combined MOP both legitimately
apply to a 4G node under the deterministic, list-overlap applicability
contract) were correctly retained; the live answer transparently noted
the two documents' differing timing values ("5 or 10 minutes, depending
on the specific procedure") rather than fabricating a blended number —
correct per the contract, though a future refinement could bias toward
the more narrowly-applicable single-technology document when several
genuinely match.
Test G (applicability ambiguity, no technology stated): PASSED —
committed cleanly to one document's specific value, never blending into
a fabricated composite figure.
Test H (EMF/WMF interpretation): PASSED — three distinct, previously-
unavailable images now correctly described and cited (a Citrix Gateway
login page, an RRU status command-line interface, a Microsoft
verification-code prompt), confirming the rasterization correction's
live effect.

NOT_APPLICABLE is never bypassable by ranking (proven at the Python
level, section-D tests above) and was not observed to be bypassed live.
No keyword/regex routing was added anywhere in this final corrective
pass.

STATUS: **A5 — Knowledge Island Ingestion Foundation + Real TELCO/RAN
Compound Knowledge Validation is COMPLETE.** Real live-runtime
validation (Gemini/Vertex, Cloud SQL PostgreSQL, GCS, the real FastAPI
app) has been performed end to end, including the two safety-critical
gates (VSWR prohibition, mandatory conflict isolation) and all 8 gates
retested after the final corrective pass. Two genuine, previously-
undiscovered defects were found and fixed during this pass (the
completion-boundary discard-ordering bug; the Pydantic
`default_factory` serialization bug) — both confirmed via full backend
regression and, for the first, via direct live re-verification. NEXT:
**5.X — Teams Rich Content / Media Retrieval** (NOT STARTED), per the
post-A5 roadmap realignment (docs/BUILD_SEQUENCE.md §2a) — followed by
Phase 6A, then Phase 4H security hardening, then 5.2–5.7, then Phase 6B.
Phase 4H is not cancelled; it is scheduled after Phase 6A instead of
directly after A5.

===================================================================
POST-A5 REFINEMENT — CLOUD SQL-ONLY RUNTIME HARDENING + SOURCE DRAWER
SOURCE+VERSION CONSOLIDATION — COMPLETE
===================================================================

A bounded, out-of-band refinement milestone, inserted after A5 and
before 5.X — does NOT reorder the roadmap (5.X remains NEXT, NOT
STARTED). Root-caused by a real live-UI corrective-pass finding: a
manual backend restart with no explicit database env vars silently
resolved BOTH persistence domains to an empty/stale local SQLite
fallback instead of the real, fully-populated Cloud SQL corpus — a
configuration/environment defect, not a retrieval/provenance bug. Two
independent tracks, both complete:

TRACK A — Cloud SQL-only runtime hardening. `backend/api/runtime_
database_policy.py` (NEW) — `validate_runtime_database_configuration`,
wired into `backend/api/app.py`'s existing `_lifespan` startup hook
(the application's one real startup boundary; unchanged otherwise) —
fails closed (`ConfigurationError`, never leaking a resolved URL/
credential) unless the resolved SQLAlchemy dialect for BOTH domains is
EXACTLY `postgresql` (a POSITIVE requirement — sqlite, mysql/mariadb,
oracle, mssql, a missing variable, and an unparseable URL are all
rejected identically) AND `session_backend == "database"` (ADK's own
`InMemorySessionService` "memory" mode is REJECTED for normal runtime,
never exempted — it never persists to Cloud SQL regardless of what
`SLOPANOC_DATABASE_URL` is set to). THERE IS NO ENVIRONMENT VARIABLE
THAT WEAKENS THIS — a first pass of this correction added
`SLOPANOC_ALLOW_SQLITE_RUNTIME` as a config-based escape hatch; this was
itself corrected and fully removed (no such `Settings` property exists)
because any env var is, by construction, something a real deployment's
configuration could also set, accidentally or otherwise. The automated
suite instead stays hermetic via a new `backend/tests/conftest.py`
autouse fixture (`bypass_runtime_database_policy_for_tests`) that
monkeypatches the *function reference* `backend.api.app` itself calls
(`backend.api.app.validate_runtime_database_configuration`) with a
stub — a pure Python-level dependency substitution no environment/
configuration value can reach — mirroring the exact pattern this
codebase already uses for `warmup_shared_model`; no `PYTEST_CURRENT_
TEST`/`sys.modules`/stack-inspection hack anywhere.
`resolve_database_url()`/`resolve_knowledge_database_url()` themselves
are deliberately UNCHANGED (still plain, policy-free resolution) —
enforcement lives only at the one real startup boundary, so the many
existing tests that explicitly configure an isolated SQLite URL through
those two methods directly (never through `_lifespan`) are completely
unaffected. A sanitized dialect-only startup log line
(`session_database_backend=.../knowledge_database_backend=...`, always
`postgresql`/`postgresql` on success) was added to `_lifespan` — never a
full URL, username, password, or Secret Manager payload.

TRACK B — Source Drawer source+version consolidation. Real live Aurora
behavior showed fragmented, repetitive source chips (one per selected
SECTION, e.g. separate "· Verification" and "· Escalation" chips for the
same document/version). `src/lib/sourceReference.ts` gained
`groupKnowledgeSourceReferences` (pure, deterministic
`KnowledgeSourceReferenceDTO[] -> KnowledgeSourceGroup[]`) and
`formatKnowledgeSourceGroupLabel` — grouping identity is
`(knowledge_id, version_label, evidence_source_id)` (audited against
`backend/knowledge/domain/models.py`'s `KnowledgeObject`: `source` is a
single OBJECT-level field — one `KnowledgeSource` per `knowledge_id`+
`version_label`, shared by every section/artifact of that object — so
`evidence_source_id` can never legitimately vary within one
`(knowledge_id, version_label)` pair for real backend data; including it
is a defensive strengthening of the identity, not a behavior change for
correctly-formed data) — never `source_id` (a synthetic per-DTO
`uuid4`, a completely different field despite the similar name), never
title/section-heading/display-name/content, which can legitimately
collide across genuinely distinct sources. Section-level dedup is by
exact `section_id` only, first-seen order preserved. `SourceChip.tsx`
gained an additive `kind: "knowledge-group"` variant (the existing
single-section `kind: "knowledge"` variant is completely untouched, so
all of its own existing tests still pass unmodified) rendering ONE
message-level chip ("Source · <title> · <version_label>") whose drawer
shows the document/version header, a Source block, BLUE-highlighted
"Matched sections" chips (one per distinct selected section, `bg-info`/
`text-info`/`border-info` — the existing design-system info token, not a
new palette), and one Supporting Evidence block PER section, in order,
exact trusted content only. `Message.tsx`'s rendering now runs
`activeChat.knowledgeSources[message.id]` through
`groupKnowledgeSourceReferences` before mapping to chips — the SAME
already-trusted, already-persisted per-section `KnowledgeSourceReference
DTO` list backs both the live SSE and rehydrated-historical paths, so a
browser refresh reprojects the identical grouped UI with no duplicate
chips and no second "grouped" persistence shape. No backend DTO/wire
contract change was needed or made; `knowledge_select_evidence`
semantics, retrieval, ranking, applicability, and provenance validation
are completely untouched — SEARCH RESULT != EVIDENCE USED still holds,
since every rendered group is still built entirely from real, already-
validated, already-selected per-section evidence references.

REAL LIVE VALIDATION (real Cloud SQL PostgreSQL for both domains, real
Vertex Gemini, a freshly started backend process with explicit env vars,
real HTTP SSE — not ASGITransport/TestClient): a real Aurora query
("do you know anything about aurora relay?" then "checksum is 73190
pink") against the pre-existing `E2E-KM-AURORA-001` fixture (not
reseeded) naturally selected BOTH the Verification and Escalation
sections in the same turn, confirming the real multi-section
consolidation scenario end to end at the data layer (frontend grouping
of this exact shape is proven by `SourceChip.groups.test.tsx`/
`sourceReference.test.ts`'s deterministic component/unit tests, since no
browser-automation tool was available in this session to click through
the rendered UI itself — not claimed here). VSWR non-regression re-
confirmed against the same fresh Cloud SQL runtime: `A5-VALIDATION-
DOCUMENT1` still resolved, no restart permitted, single governed source,
matching the original A5 correction. Negative runtime validation (a
separate controlled process, real `TestClient`-driven `_lifespan`
trigger): missing DB config fails closed with no local `.db` file
created/touched; an explicit SQLite runtime URL is rejected the same
way; valid Cloud SQL config for both domains starts normally with the
correct sanitized log line. The real Cloud SQL corpus (4 objects:
`A5-VALIDATION-DOCUMENT1`, `A5-VALIDATION-ROGERS-4G`, `A5-VALIDATION-
ROGERS-4G5G`, `E2E-KM-AURORA-001`) was confirmed byte-for-byte unchanged
before and after all validation; the local `slopanoc_sessions.db`/
`slopanoc_knowledge.db` files' modification timestamps were confirmed
unchanged throughout.

REGRESSION: full backend suite passed with no regressions (10 new
`backend/tests/test_runtime_database_policy.py` tests, KM-keyword and
Incident-Manager-keyword subsets both re-run clean); full frontend suite
752 passed, 0 failed (new coverage: `sourceReference.test.ts`'s grouping/
label cases, `SourceChip.groups.test.tsx`'s component cases, plus
`Message.historicalProvenance.test.tsx`'s pre-existing per-section-chip
assertions deliberately updated to the new consolidated-group behavior —
the intended UI change this milestone makes, not a regression);
`npm run build`/`npx tsc -b` both clean; `git diff --check` clean.

NOT TOUCHED: Knowledge search/retrieval/ranking/applicability/lifecycle,
`knowledge_select_evidence` semantics, provenance validation, Teams
source chips (`kind: "teams"`), legacy MOP citation semantics
(`kind: "mop"`), one-step troubleshooting, SSE trust gating, Case
context, Team Manager/Incident Manager topology. No Knowledge Agent, no
Troubleshooting Manager, no 5.X/6A scope.

STATUS: **POST-A5 REFINEMENT — Cloud SQL-only runtime hardening + Source
Drawer source+version consolidation is COMPLETE and live-validated.**
NEXT: **5.X — Teams Rich Content / Media Retrieval** (NOT STARTED),
unchanged by this refinement.

COMPLETE (this section is preserved as it was originally written, when
Phase 5.1 was still the next phase in this locked list — do not read the
word order below as current status; see "Phase 5.1 is now complete" a
few paragraphs down, and README.md/docs/BUILD_SEQUENCE.md, for the
authoritative current status):

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
of 5.1 -- the next phase after the locked-roadmap LOCAL GIT CHECKPOINT
is 5.X (Teams Rich Content / Media Retrieval), per the post-A5 roadmap
realignment below -- not Phase 4H directly.

Then: LOCAL GIT CHECKPOINT [DONE — see the "CURRENT (out-of-band
milestone...)" note near the top of this LOCKED ROADMAP section: POST-5.1
A (Cloud SQL) and POST-5.1 B (multimodal attachments, B0–B7, all DONE
including B7's own real-stack live validation) were inserted here,
without reordering this list. The POST-B7 UI/UX Refinement Milestone
(also inserted here, also DONE) and A5 (Knowledge Island ingestion
foundation + real TELCO/RAN compound knowledge validation — DONE,
including real live-runtime validation) were also inserted here.]

===================================================================
ROADMAP REALIGNMENT (locked, replaces everything below this point —
see docs/BUILD_SEQUENCE.md §2a for the full rationale)
===================================================================

The execution order after A5 is now:

Then: 5.X TEAMS RICH CONTENT / MEDIA RETRIEVAL — ← NEXT, NOT STARTED

  Reasons from Teams-originated rich visual evidence (starting with
  images), not just Teams message text. Distinct from the CURRENT
  capability where a user uploads an image directly into a SLOPANOC
  chat (B5/B6) — 5.X adds retrieving an image actually posted inside a
  real Teams chat and passing it into the same multimodal reasoning
  path. See docs/BUILD_SEQUENCE.md §2b for the full target definition
  and design constraints (deterministic chat→message→media binding, no
  arbitrary-URL fetch, provenance preservation, documents remaining a
  distinct governed-ingestion concern, reuse of the existing B5/B6
  architecture rather than a second vision pipeline).

Then: PHASE 6A — INTELLIGENCE ARCHITECTURE FOUNDATION — FUTURE, after 5.X

  Builds a BOUNDED intelligence/orchestration foundation against the
  context sources that already exist after A5 and 5.X (Knowledge
  Context/RAG, Case Context, Teams text + media, session state) — not
  the full future Operational Context surface (5.2–5.7 do not exist
  yet). Contains: a Context Engineering foundation; a second specialist,
  Troubleshooting Manager, attached via AgentTool alongside Incident
  Manager (Team Manager remains the sole user-facing agent); a Skills
  behavioral framework/registry ("how should this kind of work be
  performed?" — never an agent, never a MOP/SOP, never a tool, never
  memory — see docs/AGENT_CONTRACT.md §3a); an Experience Memory
  foundation/boundary ("what have we seen before?" — never Approved
  Knowledge, never silently promoted to it — see
  docs/KNOWLEDGE_CONTRACT.md §22.3–22.4). Does NOT implement Phase 7's
  mature troubleshooting loop (persistent Troubleshooting State,
  hypothesis lifecycle, next-best-diagnostic-action loop, end states) —
  6A is a foundation, not the finished troubleshooting experience. Does
  not create one agent per fault type (Skills provide behavioral
  specialization instead). Does not create a Knowledge Agent.

Then: PHASE 4H SECURITY HARDENING — FUTURE, after Phase 6A

  Not cancelled, not reduced in importance — rescheduled to evaluate the
  richer, more stable architecture Phase 6A produces (Troubleshooting
  Manager boundary, Skills framework boundary, Context Engineering
  foundation boundary, Teams rich media) rather than a boundary that is
  still being architecturally established.

4H.1 Trust boundaries + threat model
4H.2 Prompt-injection / untrusted external content isolation
4H.3 Tool authorization + output validation
4H.4 Sensitive-data / secret handling
4H.5 Model Armor integration
4H.6 Security audit events + safe failure
4H.7 Adversarial regression suite

Then: FULL REGRESSION + LIVE VALIDATION

Then: GITHUB CHECKPOINT

Then, FUTURE — after Phase 4H:

5.2 ITSM
5.3 Alarm / fault
5.4 Topology / inventory
5.5 KPI / observability
5.6 Change Management
5.7 Handover / operational context

Then: PHASE 6B — CONTEXT ENGINEERING EXPANSION — FUTURE, after 5.2–5.7

  Expands and refines the SAME Phase 6A foundation (never a second,
  competing Context Engineering architecture) against the complete
  Operational Context surface (Teams text + media, Knowledge/RAG, Case
  Context, Experience Memory, ITSM, Alarms, Topology, KPIs, Change,
  Handover): multisource context assembly, relevance/authority/
  freshness ranking, context budgeting, conflict handling, cross-source
  correlation, Experience Memory refinement, specialist context policy
  refinement.

Then: PHASE 7 — JOC / ADVANCED TROUBLESHOOTING (product target)

This is the end of the current locked execution roadmap
(5.X → 6A → 4H → 5.2–5.7 → 6B → 7).

===================================================================
BEYOND THE CURRENT LOCKED ROADMAP — CONTROLLED AUTONOMY (Phase 8,
long-term concept only, NOT scheduled, NOT part of the locked sequence
above)
===================================================================

Phase 8 — Controlled autonomy — is preserved here as a pre-existing
long-term product concept, not as a next implementation step. It is
explicitly beyond the current locked roadmap: it has no scheduled
position after Phase 7, no dependency ordering has been assigned to it,
and it must not be read as "Then: Phase 8" following Phase 7. Any future
decision to schedule it into the locked roadmap requires its own
explicit roadmap decision, not implied by its presence here.

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
