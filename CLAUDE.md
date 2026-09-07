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
POST-5.1 A — CLOUD SQL POSTGRESQL (A1–A4) is COMPLETE. Next is POST-5.1 B
(multimodal attachments), then A5 (real TELCO/RAN MOP ingestion — moved
to AFTER Attachments; the 3 real TELCO/RAN MOPs are ingested only once
Attachments is complete), before Phase 4H security hardening proceeds.

The CURRENT next milestone is POST-5.1 B — MULTIMODAL ATTACHMENTS (not yet
started — planning/documentation only at this point). Scope: paste a
temporary screenshot into SLOPANOC, image upload, composer preview/
remove, React → FastAPI image transport, Gemini multimodal input, text +
image in the same turn, and Teams + KM + image reasoning where relevant.

Locked persistence rule for POST-5.1 B and beyond:
  - a temporary user screenshot → NO Cloud Storage required
  - a governed knowledge image → Cloud Storage required
  - persistent incident evidence → Cloud Storage required

A5 — REAL TELCO/RAN MOP INGESTION now follows POST-5.1 B, not the other
way around. A5 ingests the 3 real TELCO/RAN MOPs through the existing,
unchanged Generic KM pipeline (MOP → source adapter/import boundary →
IngestedKnowledgeDocument → processing → governance → KnowledgeRepository
→ Cloud SQL PostgreSQL) — it is a separate milestone from Attachments, not
part of it. If a MOP contains images, its text/metadata/governed content
still goes to Cloud SQL PostgreSQL and any governed knowledge image goes
to Cloud Storage per the rule above — but the actual image extraction/
storage mechanics belong to A5's own future implementation pass, not
POST-5.1 B and not this docs-only correction.

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
