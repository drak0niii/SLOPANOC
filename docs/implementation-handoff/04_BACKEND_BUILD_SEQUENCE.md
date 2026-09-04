# 04 — Backend Build Sequence

> Historical planning document, written before backend implementation began.
> Superseded by README.md, docs/AGENT_CONTRACT.md, and
> docs/TEAMS_TOOL_CONTRACT.md for current architecture.

Status: **UI/UX frozen.** This is a recommended implementation **dependency
order**, not a calendar plan. Each stage is expressed in terms of what it
requires from prior stages, so a team can validate the ordering against their
own constraints rather than following dates.

Contracts referenced (`Chat`, `Project`, `ActionProposal`, etc.) are defined in
`02_INTERFACE_CONTRACTS.md`. Component boundaries (`Agent Runtime`, `Connector
Gateway`, etc.) are defined in `03_INTEGRATION_BOUNDARIES.md`.

---

## 1. Proposed build stages

### Stage 0 — Freeze contracts

Finalize, in writing, before any integration work begins:
- Canonical types for every contract in Document 2.
- ID formats/generation strategy (the frontend currently generates client-side
  ids via `createId(prefix)` — a purely local prototype concern; production
  needs a real, collision-safe id strategy, decided once, here).
- Lifecycle states for `AssistantRun`, `ActionProposalStatus`, `ConnectorState`,
  `MessageStatus`.
- The streaming event protocol (Document 2 §8), including its ordering and
  idempotency guarantees.
- The `SafeError` taxonomy (Document 2 §23).

No integrations happen in this stage. Its output is documentation and
type/schema definitions only — exactly what Documents 2 and 3 already provide as
a starting point.

**Why first:** every later stage produces or consumes these contracts. Building
persistence or a run transport before the contracts are locked risks having to
redo both.

---

### Stage 1 — Identity + application persistence

Implement:
- Auth/session boundary (produces `User`, Document 2 §1) — provider unresolved,
  see Open Questions, but the *boundary* (Application/API validates a session on
  every request) can and should be built before the provider is finalized, by
  building against an interface.
- `Project` CRUD.
- `Chat` CRUD, including pin/unpin, rename, delete, move-between-Projects — all
  the lifecycle operations in Document 1 §2, with no assistant behavior yet.
- `Message` persistence (user messages only is enough to unblock this stage;
  assistant messages arrive in Stage 2).
- `Skill` CRUD.
- Settings/preferences data the UI needs that isn't assistant-related
  (Connectors catalogue + state can be stubbed/static here; real Connector
  Gateway work is Stage 6).

**Dependency:** requires Stage 0's contracts for `Project`, `Chat`, `Skill`.
**Unblocks:** a real frontend can now be pointed at real persistence for
everything except actually talking to an assistant — Projects, chat history,
pinning, Skills management all become real.

---

### Stage 2 — Assistant run transport

Implement:
- "Send prompt" endpoint that creates an `AssistantRun` and a placeholder
  assistant `Message` (mirrors the frontend's existing `SEND_MESSAGE` behavior
  exactly).
- Run lifecycle plumbing and the streaming transport (Document 2 §8) — start
  with a **mock assistant runtime** if the real Agent Runtime (Stage 3) isn't
  ready yet. The mock can literally reuse the logic in `src/data/mock.ts`
  (`generateMockAssistantResponse`) server-side, so the frontend's exact current
  behavior is reproducible end-to-end before any real model is involved.
- Persistence of the completed run/message.
- Cancellation and error behavior at the transport level (even though, as noted
  in Document 1's Discrepancies, the current frozen UI has no cancel button —
  the transport should still support cancellation as a capability, so it isn't a
  breaking addition later if a Stop control is ever added to the UI).

**Dependency:** requires Stage 1 (a Chat must exist to attach a run to).
**Unblocks:** end-to-end streaming works, provably, against the mock — this is
the point at which "does our transport reproduce the frozen UX" can be verified
before any real model risk is introduced.

---

### Stage 3 — Model runtime

Replace the mock assistant generation with a real, controlled model runtime
(Document 2 §9–§10: `Model` catalogue + `ThinkingEffort` mapping). No connectors,
no retrieval yet — this stage proves real model invocation, streaming, and the
Model/Effort mapping boundary in isolation.

**Dependency:** requires Stage 2's transport.
**Unblocks:** real assistant responses (ungrounded/general) flow through the
exact same event contract already proven in Stage 2.

---

### Stage 4 — Knowledge grounding

Implement:
- Global approved baseline access (read-only integration with the controlled
  external repository — `CLAUDE.md`/`PRODUCT.md` name Google Cloud Storage as
  the *expected* future source; this stage is where that integration would
  actually happen, not before).
- Project approved knowledge (`KnowledgeSource`, scope: project).
- Authorization-filtered retrieval (Knowledge/Retrieval Service, Document 3 §5).
- Citation generation matching Document 2 §18 exactly.
- Strict no-answer behavior (`GroundingInsufficient`, Document 1 §4 / Document 3
  §14) — including proving that insufficient grounding never falls back to
  ungoverned model knowledge.

**Dependency:** requires Stage 3 (a real model must exist to ground).
**Unblocks:** the product's core trust promise — grounded answers with real
provenance, and a real (not keyword-mocked) insufficient-knowledge outcome.

---

### Stage 5 — Files

Implement:
- Chat attachments (real upload, replacing the mocked `Attachment` metadata).
- Project Files (real upload + listing, replacing `ProjectFile`).
- File Service lifecycle (Document 2 §21): `selected → uploading → available /
  failed`, including malware/security scanning before `available`.

**Do not** automatically turn Files into Knowledge — this is a hard rule
(Document 1 §6, Invariant Register item 7) and must be verified explicitly at
this stage, not assumed.

**Dependency:** can be built in parallel with Stage 4 (Files don't depend on
grounding), but is sequenced after Stage 3 so that "attach a file, ask about
it" can be tested against a real model rather than the mock.

---

### Stage 6 — Read connectors

Implement the normalized Connector Gateway (Document 3 §6) for read/search/
retrieve capabilities only:
- Real `Connector` catalogue + live `ConnectorState`.
- `listCapabilities`, `validateAuthorization`, `executeRead` (Document 2 §14).
- The two-dimensional availability model (global connection × Project
  enablement, Document 2 §13) — this must be built exactly as specified,
  including the invariant that global disconnection never mutates
  `Project.enabledConnectorIds`.
- Connector-unavailable routing (`global_unavailable` vs. `project_disabled`,
  Document 1 §5) driven by real state instead of the mock's deterministic
  toggle.

No writes yet.

**Dependency:** requires Stage 3 (Agent Runtime must exist to call tools) and
Stage 1 (Project connector enablement is persisted state from Stage 1's Project
CRUD).

---

### Stage 7 — Action proposals + approval gate

Implement, in full, before any write connector exists:
- `prepareWrite` in the Connector Gateway (produces `ActionProposal`, not yet
  executed).
- The Action Approval / Policy Gate (Document 3 §7) as a real, enforced
  component — not a formality.
- `ActionApprovalRecord` persistence (Document 2 §16).
- The payload/version-binding invariant (Document 2 §15) — an approval must be
  provably tied to the exact payload the user saw, and this must be tested by
  deliberately trying to approve a stale payload and confirming it is rejected.
- Audit events for every proposal and every decision.

**This stage exists specifically so that Stage 8 has nothing to build except the
connector-specific write call itself** — the approval architecture must be
complete and proven before any real external system can be written to.

**Dependency:** requires Stage 6 (read connectors prove the Gateway pattern
works) but does not require any specific write connector to exist yet — it can
and should be tested with a stub/fake write operation first.

---

### Stage 8 — Write connectors

Only now implement `executeApprovedWrite` for real external systems (e.g. the
Outlook "send email" flow the mock already demonstrates end-to-end). Because
Stage 7 already built and proved the approval gate, this stage is narrowly
scoped to: connector-specific request construction, response normalization into
`SafeError`/structured result, and connector-specific audit detail.

**Dependency:** hard-blocked on Stage 7. No write connector work should start
before the approval gate exists and is tested.

---

### Stage 9 — Usage, observability, production hardening

Implement:
- Real `UsageSummary` (Document 2 §22) fed by actual metering, replacing
  `MOCK_USAGE`.
- Full audit/observability (Document 3 §10) across every prior stage's events.
- Monitoring, alerting, resilience (retries, backpressure, rate limiting —
  `rate_limited` is already in the `ErrorCode` taxonomy from Stage 0).
- Security hardening pass across every trust boundary in Document 3 §12.

**Dependency:** requires every prior stage to exist, since this stage
instruments and hardens all of them rather than building new user-facing
capability.

---

## 2. Deviation from a naive "connectors first" ordering

A team might be tempted to build connectors before knowledge grounding, since
connectors feel more concretely "backend-y." This sequence deliberately puts
**Knowledge (Stage 4) before Read Connectors (Stage 6)** and puts **the full
approval gate (Stage 7) before any write connector (Stage 8)**, for two reasons
found directly in the repository inspection:

1. The product's core trust promise, expressed repeatedly in `CLAUDE.md` and
   `PRODUCT.md`, is grounded-answer quality and the no-answer guarantee — not
   connector breadth. Proving grounding works correctly (including the
   never-fall-back-silently rule) before adding connector complexity keeps the
   highest-risk trust behavior isolated and testable on its own.
2. The write-action security invariant (payload/version binding, Document 2
   §15) is the single most security-critical piece of this entire system. Building
   it once, generically, and proving it with a throwaway stub write (end of Stage
   7) before wiring up any real external system (Stage 8) means the first real
   write connector inherits a already-tested gate instead of being the thing that
   proves the gate works.

No other deviation from the task's suggested stage list was made — Stages 0–3
and 9 follow the order given.

---

## 3. Frontend mock → production mapping

| Current prototype | Production replacement | Notes |
|---|---|---|
| `AppState.tsx` local reducer (`chats`, `messages`, `projects`, etc.) | Application/API layer + Persistence | The reducer's *shape* (normalized entity maps + order arrays) is a reasonable model for API response shapes, but the reducer itself does not survive — it becomes client-side cache/state management over real API calls. |
| `generateMockAssistantResponse()` keyword triggers (`"email"`, `"budget"`) | Agent Runtime + Knowledge/Retrieval Service | The deterministic keyword logic is prototype-only scaffolding; production groundedness/action-detection is real model + retrieval reasoning, not string matching. |
| `window.setTimeout(..., ASSISTANT_DELAY_MS)` | `AssistantRun` lifecycle + streaming events (Document 2 §7–§8) | The fixed-delay complete/pending flip becomes a real multi-stage run with genuine intermediate status. |
| Mock `Citation` objects (`buildGroundedCitation`) | Knowledge/Retrieval Service | Real citations resolve to real `KnowledgeSource` records with real revisions. |
| `MOCK_CONNECTORS` static array + `CONNECT_CONNECTOR`/`DISCONNECT_CONNECTOR` reducer actions | Connector Gateway | Real connector state comes from actual OAuth/connection status, not a two-state toggle. |
| Mock `ActionProposal` (fixed "Send email to John Smith" fields) | Agent Runtime `prepareWrite` + Action Approval/Policy Gate | Real proposals are generated per-request from actual conversation content and connector schemas. |
| `Attachment` with client-only `File` selection, no real upload | File Service | Real upload lifecycle, storage, scanning. |
| `MOCK_USAGE` static object | Usage/Metering service | Real per-user/per-org usage aggregation. |
| `createId(prefix)` (`src/lib/id.ts`, likely a simple random/counter id) | Production id strategy decided in Stage 0 | Needs to be collision-safe and consistent across services, not a per-tab client generator. |
| `KnowledgeDocument` (2 mock rows, read-only in UI) | Knowledge/Retrieval Service + controlled repository ingestion | Global baseline ingestion is entirely outside this frontend's concern per `PRODUCT.md`; Project knowledge similarly needs a real add/approve path not present in the current UI. |
| No auth at all (Account menu is a static disabled placeholder) | Auth/session boundary (Stage 1) | Nothing in the current frontend assumes a specific auth provider — this is a clean integration point. |
| `sidebarCollapsed`, popover-open booleans, draft text | Stays client-side, Experience Layer only | Pure UI state with no backend equivalent — explicitly not part of this mapping's "replace" list. |

---

## 4. API surface inventory

Logical operations only — no URL paths, verbs, or transport chosen here.

```text
Projects
- list
- create
- read
- update instructions
- update connector configuration (enable/disable per connector)
- (knowledge and files are read via their own sections below, scoped to a project)

Chats
- list (per workspace scope: general or a given project)
- create (implicitly, on first send — see Runs/start below; there is no
  standalone "create empty chat" operation, consistent with Document 2 §5)
- rename
- pin / unpin
- move (change projectId)
- delete

Runs
- start (send a prompt; creates the Chat first if this is the first message)
- stream (subscribe to run events for a given run/chat)
- cancel (capability to support; no current UI trigger — see Document 1
  Discrepancies)

Skills
- list
- create
- update
- delete

Connectors
- list (global catalogue + current state)
- connect / manage / disconnect (global state changes)
- get project availability (derived; see Document 2 §13 — may not need its own
  persisted operation if computed at read time)
- update project enablement (toggle a connector on/off for a Project — this may
  logically live under Projects instead; listed here for completeness since the
  frontend's `toggleProjectConnector` action touches both concepts)

Files
- register/upload (chat attachment or Project File)
- list (per chat or per project)
- delete

Knowledge
- list (per project, and/or global baseline reference data if the UI ever needs
  to display it — currently read-only, no create/update/delete surface in the
  frozen UI)

Actions
- approve
- reject (frontend calls this "cancel")
- get status (or receive via the run/action stream)

Usage
- get summary
```

---

## 5. Invariant register

Non-negotiable. A production implementation that violates any of these has
regressed the approved product, regardless of how the violation happened.

1. Project opens directly to conversation. No Project dashboard, ever.
2. An empty Chat is not persisted until first send, unless a specific technical
   constraint makes it genuinely unavoidable — and even then, it must be
   invisible to the user (excluded from every list/count) and reclaimed if
   abandoned.
3. Skill is chat-scoped only. `Project` has no Skill field, ever, structurally —
   not just by convention.
4. Maximum one active Skill per Chat, always.
5. Model and Effort are chat-scoped (persist on `Chat`, restored when switching
   chats; snapshot at run-start per Document 2 §22a).
6. Global and Project Knowledge remain distinct, composable scopes — never
   merged into one undifferentiated pool.
7. Project Files are not automatically Knowledge. Ever. Promotion, if it exists
   at all, is a separate explicit action.
8. Chat attachments are not automatically Knowledge.
9. Governed/operational questions do not silently fall back to unapproved,
   ungoverned generic knowledge when approved sources are insufficient — the
   product surfaces `GroundingInsufficient` instead.
10. Citations resolve to exact source provenance (document, version, section,
    excerpt) — never a vague or unresolvable reference.
11. Project connector enablement (`Project.enabledConnectorIds`) and global
    connector connection state (`Connector.state`) are independent dimensions.
    A global disconnect never mutates a Project's enablement list.
12. Connector writes (and any state-changing operation) require explicit user
    approval before execution. No exceptions for "obviously safe" writes.
13. Approval is bound to the exact action payload/version shown to the user. A
    changed payload invalidates the approval and requires re-confirmation.
14. Authorization is enforced server-side, at every layer that performs a
    sensitive operation — never inferred from client-side UI state.
15. The Agent Runtime never receives external-system credentials — those live
    only in the Connector Gateway.
16. Private chain-of-thought / raw model reasoning is never exposed to the user
    and never stored as a user-visible or auditable "reasoning" record. Only
    safe, pre-approved status language crosses into the UI.
17. Project chats remain associated with their Project once persisted — a Chat
    is mutated in place when moved, never duplicated across scopes.
18. Moving a Chat between General and a Project (in either direction) preserves
    its full state: messages, title, Model, Effort, Skill, attachments, and
    pinned state.
19. Pinned status and pin order survive persistence and survive a Chat moving
    between workspaces — a moved pinned Chat becomes the last pinned chat in its
    new scope, per `pinnedAt` semantics (Document 1 §2.1, Document 2 §4).
20. Backend architecture adapts to the frozen UX; the frozen UX is not
    redesigned to accommodate backend convenience.
21. *(Found during inspection, not in the task's seed list.)* A completed
    assistant `Message` carries at most one "special" outcome
    (`citations` / `groundingResult: insufficient` / `connectorUnavailable` /
    `actionProposalId`) — these are mutually exclusive per turn.
22. *(Found during inspection.)* `Chat.connectorIds` is chat-persistent state,
    distinct from both `Connector.state` and `Project.enabledConnectorIds` — it
    must not be discarded between turns or conflated with either of the other
    two connector concepts (Document 2 §4).
23. *(Found during inspection.)* An `AssistantRun`'s configuration snapshot
    (Model, Effort, Skill, connectors) is fixed at run start and does not change
    mid-run even if the user edits the Chat's settings for their next message
    (Document 2 §22a).

---

## 6. Open questions / decisions required

For each: the decision required, why it matters, what depends on it, and when it
should be made.

**Authentication provider.**
*Decision:* which identity provider/protocol (enterprise SSO, OAuth/OIDC
provider, etc.) issues the `User` identity.
*Why it matters:* shapes the Application/API layer's session model and how
`User.id` is minted and kept stable.
*Depends on it:* Stage 1 (identity + persistence) cannot fully complete without
it, though the session boundary can be built against an interface first.
*Recommended timing:* before Stage 1 begins in earnest; the interface can be
stubbed slightly earlier.

**Final model/provider mapping.**
*Decision:* which real model(s) sit behind `Model.id` values, replacing the
prototype's "GPT-5.6 Sol / GPT-5.5 / o3" placeholders, and how
`ThinkingEffort` maps to that provider's actual parameters.
*Why it matters:* directly affects Stage 3 scope and cost/latency
characteristics of every run.
*Depends on it:* Stage 3 (Model runtime); indirectly Stage 4 (grounding quality
depends on model choice).
*Recommended timing:* before Stage 3, can be decided in parallel with Stage 2.

**Production persistence technology.**
*Decision:* database(s) for the Persistence boundary (Document 3 §9).
*Why it matters:* affects query patterns for chat history, pinning/ordering,
and how citation/provenance history is retained.
*Depends on it:* Stage 1 onward.
*Recommended timing:* Stage 0, alongside contract finalization, since schema
design follows directly from the Document 2 contracts.

**Streaming transport (SSE vs. WebSocket vs. other).**
*Decision:* the transport carrying the event contract in Document 2 §8.
*Why it matters:* affects reconnection/replay behavior, infrastructure
(load balancer / proxy support), and client implementation complexity.
*Depends on it:* Stage 2.
*Recommended timing:* Stage 0–1, since Stage 2 is built directly on top of it;
the logical event contract itself is already transport-neutral and does not
need to change regardless of the choice.

**File size/type limits.**
*Decision:* maximum attachment/Project File size, accepted mime types.
*Why it matters:* affects File Service design and user-facing error messaging
(`upload_failure` `SafeError` copy).
*Depends on it:* Stage 5.
*Recommended timing:* before Stage 5.

**Retention policy (messages, files, audit records).**
*Decision:* how long Chats/Messages/Files/audit records are retained, and any
per-workspace override.
*Why it matters:* compliance posture, storage cost, and whether "delete chat"
needs to cascade to a hard-delete or a soft-delete/retention-hold model.
*Depends on it:* Persistence design (Stage 1) and Audit design (Stage 9), though
the decision itself can be made independently of implementation timing.
*Recommended timing:* before Stage 1's persistence schema is finalized, since
retroactively adding retention semantics to an existing schema is costly.

**Audit retention.**
*Decision:* separate from general retention — how long `ActionApprovalRecord`
and connector audit events are kept, given their compliance/security role.
*Why it matters:* action-approval auditability is a named product requirement
(Document 2 §16); its retention window is a policy decision, not an engineering
default.
*Depends on it:* Stage 7 and Stage 9.
*Recommended timing:* before Stage 7, since the audit record schema may need to
account for retention-driven partitioning.

**Project membership model.**
*Decision:* who can access a given Project — is it open to all authenticated
users, or scoped to explicit members/roles? Neither `PRODUCT.md` nor
`UX_SPEC.md` defines this, and the frontend has no membership UI at all.
*Why it matters:* directly determines the Authorization Boundary's "access a
Project" check (Document 3 §11).
*Depends on it:* Stage 1 (Project CRUD/authorization).
*Recommended timing:* before Stage 1's authorization checks are implemented —
this cannot be deferred past that point.

**Connector credential ownership model.**
*Decision:* are connector credentials owned per-user, per-workspace/org, or
some hybrid (e.g. an org-level Outlook connection that all Projects can enable)?
The frontend's UI (global Connect/Manage/Disconnect, with per-Project
enable/disable) is consistent with either model, so this is genuinely open.
*Why it matters:* shapes the Connector Gateway's credential storage and the
authorization checks in Document 3 §11.
*Depends on it:* Stage 6.
*Recommended timing:* before Stage 6.

**Exact Global Knowledge repository contract.**
*Decision:* the concrete interface to the controlled external repository
(`CLAUDE.md`/`PRODUCT.md` name Google Cloud Storage as the expected source, but
not the exact bucket layout, indexing trigger, or update cadence).
*Why it matters:* directly shapes Stage 4's ingestion path for the Global
baseline.
*Depends on it:* Stage 4.
*Recommended timing:* before Stage 4 begins.

**Knowledge indexing implementation.**
*Decision:* how approved documents become retrievable (embeddings/vector store,
lexical search, hybrid, or something else) — intentionally not chosen anywhere
in this handoff.
*Why it matters:* core to Knowledge/Retrieval Service design and grounding
quality.
*Depends on it:* Stage 4.
*Recommended timing:* before Stage 4; can be prototyped/evaluated earlier in
parallel with Stages 1–3 without affecting them.

**Deployment topology.**
*Decision:* how the logical components in Document 3 map to actual deployed
services (monolith vs. microservices, which components co-locate, region
strategy).
*Why it matters:* affects the trust-boundary table (Document 3 §12) in
practice, latency, and operational complexity.
*Depends on it:* cross-cutting; most directly affects Stage 9's hardening work
but should be considered from Stage 0.
*Recommended timing:* Stage 0, as an architectural decision informed by but not
blocking the contract work.

**Failed/expired `ActionProposalStatus` UI presentation.**
*Decision:* how the Experience Layer should visually represent
`failed`/`expired` action proposals (Document 2 §15) — this is a small UI
addition outside the current frozen scope, not a backend architecture question,
but it blocks Stage 8 from being fully user-visible without it.
*Why it matters:* without it, a failed or expired write silently has nowhere
correct to render in the current `ActionProposalCard`.
*Depends on it:* Stage 8's user-facing completeness.
*Recommended timing:* flagged for product/design review before Stage 8 ships,
not before Stage 8's backend work begins.

**"Reconnect required" connector state.**
*Decision:* whether to implement the fifth connector state `UX_SPEC.md`
mentions as optional, and if so, how it's distinguished from `error` in the UI.
*Why it matters:* affects the `ConnectorState` union (Document 2 §12) and,
like the item above, is a small UI addition outside this frozen pass if adopted.
*Depends on it:* Stage 6.
*Recommended timing:* product decision before Stage 6; not required for Stage 6
to begin (the four existing states are sufficient to ship).

**Run cancellation UI.**
*Decision:* whether to add a "stop generating" control to the composer.
*Why it matters:* the transport is recommended to support cancellation as a
capability regardless (Stage 2), but whether users can trigger it is a UX
decision outside this frozen pass.
*Depends on it:* nothing blocks on this — it's a future UI enhancement, not a
backend blocker.
*Recommended timing:* whenever a future UX pass is authorized; not before.
