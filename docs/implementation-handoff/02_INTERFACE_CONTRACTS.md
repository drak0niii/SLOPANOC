# 02 — Interface Contracts

Status: **UI/UX frozen.** These are canonical logical contracts, not REST/OpenAPI
specifications and not database schemas. They are written TypeScript-style for
precision, but the types themselves are language-neutral concepts. Every contract
below states purpose, producer, consumer, fields, lifecycle, source of truth, and
invariants.

Fields already present in the frozen frontend (`src/types.ts`) are marked
**[frontend-existing]**. Fields introduced here for production needs that the
frontend does not read or render are marked **[production-only]** — adding them
never requires a UI change, since the UI simply ignores fields it doesn't consume.
Where the frontend's actual field name or shape differs from what a clean
production contract would use, both are shown and the mapping is explicit.

---

## 1. User (authenticated identity)

```ts
interface User {
  id: string;
  displayName: string;
  email: string;
  avatarUrl?: string;
}
```

- **Purpose:** the authenticated identity behind every request. Not modeled in the
  frontend at all today (the Account menu is a static placeholder with a disabled
  "Log out" item) — this is a pure production addition.
- **Producer:** Identity/Auth provider (unresolved — see Open Questions).
- **Consumer:** Application/API layer, Agent Runtime (for personalization/audit),
  Connector Gateway (for per-user connector authorization), Persistence.
- **Lifecycle:** created at first login; updated on profile change; never deleted
  from the auth system's perspective (soft-delete/deactivation is an
  authorization concern, not an identity concern).
- **Invariant:** `User.id` is the stable join key for every other contract that
  needs "who did this" (Chat ownership, ActionApproval, audit records). It must
  never be inferred from `displayName` or `email`, both of which are mutable.

**Authentication vs. authorization.** `User` establishes *who you are*. It does
**not** establish *what you can do*. Every other contract below that references a
`User` (Project access, connector entitlement, knowledge access, action
authorization) requires a separate authorization check — see the Authorization
Boundary in `03_INTEGRATION_BOUNDARIES.md`. Full IAM design is explicitly out of
scope for this pass; only the requirement that identity and authorization are
distinct concepts is being locked in now.

---

## 2. WorkspaceScope

```ts
type WorkspaceScope =
  | { type: "general" }
  | { type: "project"; projectId: string };
```

- **Purpose:** identifies which workspace a Chat, draft, or request belongs to.
  **[frontend-existing]** — this is `WorkspaceScope` in `src/types.ts`, used as-is.
- **Producer:** set by the client based on navigation (entering a Project, or
  General "New chat"); **must be validated server-side**, not trusted as
  presented.
- **Consumer:** Application/API layer (to select which Project's context to
  compose), Agent Runtime (context assembly), Knowledge/Retrieval Service (scope
  selection).
- **Invariant:** Project scope must reference a real, authorized `Project.id`.
  This is not decorative UI state — in production it is the execution context
  that determines which instructions, knowledge, and connectors are eligible for
  a run. A request claiming Project scope for a Project the user cannot access
  must be rejected at the Application/API layer before it ever reaches the Agent
  Runtime.

---

## 3. Project

```ts
interface Project {
  id: string;
  name: string;
  instructions: string;              // frontend: instructionsText
  enabledConnectorIds: string[];     // frontend: connectorIds
  approvedKnowledgeRefs: string[];   // frontend: knowledgeDocIds
  fileRefs: string[];                // frontend: fileIds
  createdAt: string;                 // ISO 8601; frontend: number (epoch ms)
  updatedAt?: string;                // [production-only] — not tracked in frontend
}
```

- **Purpose:** a conversational workspace/container, not a dashboard entity.
- **Producer:** Application/API layer, on user-initiated create
  (`POST /projects` equivalent — see API Surface Inventory).
- **Consumer:** Agent Runtime (instructions + connector + knowledge context),
  Knowledge/Retrieval Service (Project-scoped retrieval), Connector Gateway
  (Project connector-enablement check), Experience Layer (sidebar, Project
  Settings, context header).
- **Source of truth:** Application/API persistence. The frontend never invents
  Project state locally beyond optimistic UI updates.
- **Field naming note:** the frontend's field names (`instructionsText`,
  `connectorIds`, `knowledgeDocIds`, `fileIds`) are UI-local naming and do not need
  to match the production contract's field names 1:1 — the Application/API layer
  is expected to translate. This contract uses clearer production names; the
  mapping above documents the correspondence for anyone reading both sides.

**Explicit rule: `Project` has no Skill field of any kind.**

```text
Project.defaultSkill = INVALID
```

There is no `skillId`, no `defaultSkillId`, no skill list, nothing skill-related
on `Project`, ever. A Project must never determine, store, suggest, or inherit an
active Skill for any Chat. This is enforced today by the simple fact that the
frontend's `Project` type has no such field — the production schema must preserve
that absence structurally, not just by convention. See Invariant Register, item 3.

---

## 4. Chat

```ts
interface Chat {
  id: string;
  projectId: string | null;         // frontend-existing, called projectId
  title: string;
  pinned: boolean;
  pinnedAt?: string;                 // ISO 8601; frontend: number (epoch ms) | undefined
  selectedModelId: string;
  thinkingEffort: "standard" | "extended";
  activeSkillId: string | null;
  connectorIds: string[];            // see conclusion below — chat-persistent
  createdAt: string;
  updatedAt: string;                 // [production-only] — not tracked in frontend
}
```

- **Purpose:** a single conversation, in General or Project scope.
- **Producer:** Application/API layer, created on first send (never before —
  see the New Chat Draft Contract).
- **Consumer:** Experience Layer (sidebar, active conversation), Agent Runtime
  (per-chat config for a run), Persistence.
- **Source of truth:** Application/API persistence once created; before creation,
  the client-held `NewChatDraft` is authoritative (see §5).

### Conclusion: connector selection is Chat-persistent, not request-only

The task requires an explicit determination here, based on actual frozen
behavior, not assumption. **Verified from `AppState.tsx`:** `TOGGLE_CONNECTOR`
writes to `chat.connectorIds` whenever a Chat is active, and reading
`activeConnectorIds` reads `activeChat.connectorIds` whenever a chat is active
(falling back to the draft only when no chat is active yet). Switching chats
correctly restores each chat's own connector selection — this mirrors Model,
Effort, and Skill exactly. **Conclusion: `Chat.connectorIds` is a first-class,
persistent field on the Chat record**, representing which of the currently
*available* connectors (see the Connector Availability Contract, §11) this
specific chat has selected for use. It is not merely transient per-request
context, and it must not be discarded between turns.

This is distinct from, and must never be merged with:
- `Connector.state` — global connection health (§10).
- `Project.enabledConnectorIds` — Project-level intent to allow a connector (§3,
  §11).

`Chat.connectorIds` is the third, narrowest layer: "of what's available to me
right now, which do I actually want engaged in this chat." Do not duplicate
Project's `enabledConnectorIds` semantics inside Chat, and do not treat
`Chat.connectorIds` as authorization — a connector present in `Chat.connectorIds`
still must pass the full availability + authorization chain before any tool call
executes.

- **Invariant:** a Chat always belongs to exactly one scope (`projectId: null` for
  General, or a valid Project id). Moving a Chat between scopes (see §5 lifecycle
  note below and Document 1 §2) mutates `projectId` in place — a Chat is never
  duplicated across scopes, and it always remains associated with whichever
  Project it currently belongs to once persisted (Invariant Register, item 17).
- **Invariant:** pinned ordering across all chats in a given scope is fully
  determined by `pinnedAt` ascending among pinned chats (see Document 1 §2.1).
  `pinnedAt` must be present whenever `pinned = true`, and absent/null whenever
  `pinned = false`.

---

## 5. NewChatDraft

```ts
interface NewChatDraft {
  workspaceScope: WorkspaceScope;
  text: string;
  attachments: Attachment[];
  selectedModelId: string;
  thinkingEffort: "standard" | "extended";
  activeSkillId: string | null;
  connectorIds: string[];
}
```

- **Purpose:** the pre-Chat state that exists the instant the app opens (General)
  or a Project is entered (Project scope), before any message has been sent.
- **Producer/owner:** Experience Layer, held entirely client-side (or
  session-scoped server-side if the production architecture wants draft recovery
  across reloads — that is an open question, not a requirement).
- **Consumer:** becomes the seed for the first `Chat` on send; otherwise consumed
  only by the composer UI itself.
- **Lifecycle:** created fresh on app load, `New Chat`, `Enter Project`, `Create
  Project`, and after deleting the active chat. Discarded (promoted into a `Chat`)
  on first send.

**Why Draft and Chat are separate concepts:** a `Chat` is a durable, addressable,
listable, persisted conversation. A `Draft` is not — it is disposable
configuration state that may never produce a Chat at all (the user may open the
app, look at the composer, and leave without sending anything). Modeling them as
one type would force either (a) persisting an empty Chat record for every app
visit, which pollutes chat history with nothing, or (b) awkward nullable/partial
Chat fields to represent "not sent yet." Keeping them separate keeps `Chat`
always-valid and always-real.

**Invariant (locked):** opening the app, or entering/creating a Project, must
**never** require creation of an empty Chat record. If a future technical
constraint makes this genuinely unavoidable (e.g. a persistence layer that cannot
represent an unsent draft), that Chat record must be excluded from every list,
history, and count the user can see, and must be silently reclaimed if abandoned —
but the strong preference, and the current frontend's actual behavior, is that no
such record is created at all. See Invariant Register, item 2.

---

## 6. Message

```ts
type MessageRole = "user" | "assistant";
type MessageStatus = "pending" | "complete" | "failed"; // "failed" is [production-only]

interface Message {
  id: string;
  chatId: string;
  role: MessageRole;
  text: string;
  status: MessageStatus;
  attachments?: Attachment[];
  citations?: Citation[];
  groundingResult?: GroundingResult;      // see §17; frontend today only ever sets "insufficient"
  searchedScope?: string[];
  actionProposalId?: string;              // reference only — see §7 for why Message and
                                            // ActionProposal are kept separate
  connectorUnavailable?: ConnectorUnavailableInfo;
  runId?: string;                          // [production-only] — see AssistantRun (§7)
  createdAt: string;
}

interface ConnectorUnavailableInfo {
  connectorId: string;
  connectorName: string;
  reason: "global_unavailable" | "project_disabled";
}
```

- **Purpose:** one turn in a conversation — user input or assistant output.
- **Producer:** user Messages are produced by the Application/API layer on send;
  assistant Messages are produced by the Agent Runtime as a run progresses (see
  §7–§8) and finalized when the run completes.
- **Consumer:** Experience Layer (rendering), Persistence.
- **Internal-only role note:** the frontend's `Message["role"]` union is only
  `"user" | "assistant"`. If the production Agent Runtime needs internal
  system/tool messages for its own orchestration, those are **internal to the
  Agent Runtime's context assembly** and must not be exposed to the Experience
  Layer as a `Message` unless there is a specific, deliberate product reason to
  show one (there is none identified today). Do not widen the user-facing
  `role` union casually to accommodate backend plumbing.
- **Invariant:** a completed assistant Message carries **at most one** of
  `citations`, `groundingResult: "insufficient"`, `connectorUnavailable`, or
  `actionProposalId` as its "special" payload — these are mutually exclusive
  outcomes of a single run, matching `MockResponseResult` in the current mock
  (`grounded | no_answer | action_proposal | action_unavailable`). A plain grounded
  or ungrounded conversational reply carries none of them.
- **Invariant:** `Message` is not an "everything bag." Do not add a single
  untyped `metadata: any` field to Message to carry future needs — extend this
  contract explicitly instead, the same way `connectorUnavailable` and
  `groundingResult` were added as named, typed fields.

---

## 7. AssistantRun

```ts
type RunStatus =
  | "queued"
  | "reasoning"
  | "retrieving"
  | "tool_calling"
  | "awaiting_confirmation"
  | "responding"
  | "completed"
  | "failed"
  | "cancelled";

interface AssistantRun {
  id: string;
  chatId: string;
  assistantMessageId: string;   // the Message this run is producing
  status: RunStatus;
  startedAt: string;
  completedAt?: string;
  failureReason?: ErrorCode;    // see §21
}
```

- **Purpose:** the execution lifecycle behind one assistant turn, separated from
  `Message` so that streaming status, retries, and tool-calling steps have
  somewhere to live without overloading the Message record itself.
- **Producer:** Agent Runtime.
- **Consumer:** Application/API layer (persists run history, exposes it over the
  stream), Experience Layer (renders a safe subset of status as UX, e.g. the
  existing "thinking" indicator).
- **Frontend gap, explicitly noted:** the current mock has **no** run concept at
  all — it is a single `window.setTimeout` that flips `Message.status` from
  `pending` directly to `complete`. This contract is a production addition that
  must be able to satisfy the same minimal UI surface the frontend already
  renders (a pending/thinking state, then a complete message) without regressing
  it, while giving the production system room to expose richer status later
  (e.g. "searching approved knowledge") **without requiring a UI redesign** —
  the existing `ThinkingIndicator` component can absorb additional safe status
  strings without any structural change.
- **Invariant (hard):** the UI may receive safe, product-language status signals
  such as *"thinking," "searching approved knowledge," "preparing action."* It
  must **never** receive private model chain-of-thought, raw tool-call
  arguments/results, or provider-internal reasoning tokens, whether as a status
  string or as any other field, ever, on this or any other contract. See
  Invariant Register, item 16.
- **Invariant:** exactly one `AssistantRun` exists per pending/completed
  assistant `Message`. `awaiting_confirmation` is the status a run enters when it
  has produced an `ActionProposal` and is blocked on user approval — see §14.

---

## 8. Streaming event contract

Transport-neutral (SSE vs. WebSocket is a Document 3/Open Questions decision, not
fixed here). This is the logical event sequence the Experience Layer needs to
reproduce the existing progressive-reveal response UX (`Message.tsx`'s
word-by-word reveal) plus the richer states production adds.

```ts
type RunEvent =
  | { type: "RunStarted"; runId: string; chatId: string; assistantMessageId: string }
  | { type: "StatusChanged"; runId: string; status: RunStatus }
  | { type: "TextDelta"; runId: string; assistantMessageId: string; delta: string; seq: number }
  | { type: "CitationAdded"; runId: string; assistantMessageId: string; citation: Citation }
  | { type: "GroundingInsufficient"; runId: string; assistantMessageId: string; searchedScope: string[] }
  | { type: "ActionProposed"; runId: string; assistantMessageId: string; proposal: ActionProposal }
  | { type: "ActionStatusChanged"; actionProposalId: string; status: ActionProposalStatus }
  | { type: "ConnectorUnavailable"; runId: string; assistantMessageId: string; info: ConnectorUnavailableInfo }
  | { type: "RunCompleted"; runId: string; assistantMessageId: string }
  | { type: "RunFailed"; runId: string; errorCode: ErrorCode; retryable: boolean };
```

For each event:

| Event | Required IDs | Ordering | Replay/idempotency | Frontend effect |
|---|---|---|---|---|
| `RunStarted` | `runId`, `chatId`, `assistantMessageId` | always first for a run | must be idempotent by `runId` (duplicate delivery is a no-op) | shows the pending/thinking indicator |
| `StatusChanged` | `runId` | any time after `RunStarted`, before terminal event | last-write-wins per `runId`; out-of-order delivery should be tolerated (ignore if status regresses) | optionally updates a safe status label |
| `TextDelta` | `runId`, `assistantMessageId`, `seq` | strictly increasing `seq` per `assistantMessageId` | consumer must reassemble by `seq`, not arrival order; duplicates by `seq` are dropped | appends to the streamed response text |
| `CitationAdded` | `runId`, `assistantMessageId` | any time before `RunCompleted` | idempotent by `citation.id` | appends a citation chip |
| `GroundingInsufficient` | `runId`, `assistantMessageId` | terminal-adjacent (precedes `RunCompleted`) | idempotent (single delivery expected) | renders the strict-grounding no-answer state |
| `ActionProposed` | `runId`, `assistantMessageId` | terminal-adjacent | idempotent by `proposal.id` | renders the Action Proposal card; run enters `awaiting_confirmation` |
| `ActionStatusChanged` | `actionProposalId` | any time after approval | idempotent by `(actionProposalId, status)` pair | updates the Action Proposal card's state (approved/processing/completed/cancelled/failed) |
| `ConnectorUnavailable` | `runId`, `assistantMessageId` | terminal-adjacent | idempotent | renders the connector-unavailable card with reason-aware CTA |
| `RunCompleted` | `runId`, `assistantMessageId` | always last for a successful run | idempotent (safe to receive after already-completed) | marks the message complete, persists final state |
| `RunFailed` | `runId` | terminal, replaces `RunCompleted` | idempotent | surfaces a user-safe error (§21), never a raw provider error |

**Invariant:** every event after `RunStarted` must be attributable back to a
`runId` that a `RunStarted` was already delivered for, so a client that
reconnects mid-stream can request replay from a known point rather than guessing
state. Exact reconnection/replay mechanics are an Open Question (transport
choice), not fixed here.

---

## 9. Model

```ts
interface Model {
  id: string;
  displayName: string;
  availability: "available" | "unavailable" | "restricted";  // [production-only]
}
```

- **Purpose:** the selectable model shown in the composer's Effort → Advanced →
  Model row.
- **Frontend today:** `AiModel { id, name }` with a hardcoded list (`GPT-5.6 Sol`,
  `GPT-5.5`, `o3`) in `data/mock.ts`. These three names are **prototype
  placeholders**, not a commitment to any real provider or model family.
- **Producer:** Application/API layer (a Model catalogue endpoint), ultimately
  configured by whoever owns provider relationships in production.
- **Consumer:** Experience Layer (composer Model picker), Agent Runtime (routes a
  run to the correct provider).
- **UI contract vs. provider mapping — kept strictly separate:** the frontend
  contract is only `{ id, displayName }` (plus optional availability so the UI can
  gray out a model without needing to know why). The mapping from `Model.id` to an
  actual provider API, model version string, region, or deployment is entirely a
  production/Agent-Runtime concern and must never leak into this contract or into
  the frontend. The frontend must be able to add/remove/rename models purely by
  changing what this contract returns, with zero frontend code changes.

---

## 10. Thinking Effort

```ts
type ThinkingEffort = "standard" | "extended";
```

- **Purpose:** a product-level intensity control, not a model parameter. **[frontend-existing]**, exact union.
- **Producer/consumer:** set by the user per-chat (composer), read by the Agent
  Runtime when starting a run.
- **Mapping boundary:** the Agent Runtime (or a thin mapping layer in front of it)
  translates `standard`/`extended` into whatever provider-specific parameter
  achieves that intent (reasoning budget, extended thinking flag, temperature
  profile, etc.) for the currently-selected `Model`. The frontend must never need
  to know provider parameter names, ranges, or defaults — it only ever sends and
  displays these two literal values.
- **Invariant:** exactly two values exist today. Do not add a third value to
  satisfy a provider-specific need — instead, extend the mapping layer. If the
  product genuinely needs a third effort tier, that is a UI change and is out of
  scope for this frozen pass.

---

## 11. Skill

```ts
interface Skill {
  id: string;
  name: string;
  description: string;
  instructions: string;
  createdAt?: string;   // [production-only]
  updatedAt?: string;   // [production-only]
}
```

- **Purpose:** a reusable, globally-managed behavioral instruction set, selectable
  per chat.
- **Producer:** Application/API layer (Skill CRUD), authored via the Skills
  editor in Settings.
- **Consumer:** Agent Runtime (applies `instructions` as part of context
  assembly when `Chat.activeSkillId` is set), Experience Layer (Skill selector,
  Skills management panel).
- **Rules (all verified in the frozen frontend):**
  - Skills are globally managed (Settings → Skills), never Project-scoped.
  - Maximum one active Skill per Chat (`Chat.activeSkillId: string | null`);
    selecting a new Skill replaces the previous one for that Chat only.
  - A new Chat defaults to no active Skill (`null`).
  - `Project` cannot assign a Skill (see §3).
  - Editing a Skill (`updateSkill`) retains its stable `id` — chats referencing it
    continue to reference the updated instructions live; there is no versioning
    or snapshot-on-select today.
  - Deleting a Skill (`deleteSkill`) clears `activeSkillId` on every Chat that
    referenced it (frontend: `DELETE_SKILL` walks all chats and nulls the
    reference) and clears it from the draft if it was pending selection there.
    The Skills management UI warns the user before deletion if any chat currently
    references the skill.
- **Versioning:** intentionally not designed here. The current product has no
  concept of Skill history/versions; if production needs Skill audit/rollback,
  that is a future addition layered on top of this contract (e.g. an
  append-only revision table keyed by `Skill.id`), not a redesign of it.

---

## 12. Connector

```ts
type ConnectorState =
  | "connected"
  | "not_connected"
  | "permission_required"
  | "error";
  // "reconnect_required" is a documented-but-unimplemented optional state — see
  // Document 1's Discrepancies section and Open Questions.

type ConnectorCapability = "read" | "read_write"; // UI label: "Read" / "Read + Write"

interface Connector {
  id: string;
  name: string;
  description: string;   // frontend field name: purpose
  state: ConnectorState;
  capability: ConnectorCapability;
}
```

- **Purpose:** an external system the assistant can access, managed globally.
- **Producer:** Application/API layer (connector catalogue + connection-state
  updates from the Connector Gateway).
- **Consumer:** Experience Layer (Global Settings → Connectors, Project Settings →
  Connectors, composer `+` menu connector picker), Agent Runtime (tool
  selection), Connector Gateway (authorization + execution).
- **Invariant:** `Connector` is never treated as an `Attachment`. They are
  disjoint concepts even though both eventually relate to "external content" —
  attaching a file and enabling a connector are different actions with different
  contracts (§12 here vs. §19).

---

## 13. Connector availability

Two independent dimensions determine whether a connector is actually usable in a
given chat. **Both frontend representations of these already exist and are
already independent** (`Connector.state` is global; `Project.enabledConnectorIds`
is Project-local); production must preserve that independence exactly.

```text
General workspace effective availability:
  Connector.state === "connected"

Project workspace effective availability:
  Connector.state === "connected"
  AND
  connector.id ∈ Project.enabledConnectorIds
```

- **Invariant (hard):** a connector going globally offline (`state` changes away
  from `"connected"`) must **never** cause it to be silently removed from
  `Project.enabledConnectorIds`. That list represents the Project's *intended*
  availability, independent of the connector's current live health. When the
  connector reconnects, the Project's prior enablement is still there, with no
  reconfiguration needed. This is exactly why the frontend computes availability
  as a derived boolean at read time (`connector.state === "connected" &&
  (!project || project.connectorIds.includes(id))`) rather than ever mutating
  `Project.enabledConnectorIds` in response to a state change — production must
  compute availability the same way, never by mutating the Project's enabled list.
- **Reason routing:** when a connector is unavailable, the reason (`
  global_unavailable` vs. `project_disabled`) must be derivable the same way the
  frontend derives it — global unavailability always takes priority when both
  conditions fail simultaneously (see Document 1 §5 and the invariant in
  `ConnectorUnavailableInfo`, §6 above).

---

## 14. Connector tool contract (execution boundary)

This is the logical interface the Agent Runtime is allowed to call — never a raw
connector SDK.

```text
listCapabilities(connectorId) -> Connector capability summary
validateAuthorization(userId, connectorId, operation) -> allowed: boolean
executeRead(connectorId, operation, params) -> structured result
prepareWrite(connectorId, operation, params) -> ActionProposal (unexecuted)
executeApprovedWrite(actionProposalId) -> structured result
```

- **Purpose:** the only path from the Agent Runtime to an external system.
- **Producer:** Connector Gateway (Document 3).
- **Consumer:** Agent Runtime only.
- **Hard boundary:** the model/Agent Runtime never invokes a raw connector SDK,
  never holds a connector credential, and never calls an external system
  directly. Every read and every write passes through this tool interface, which
  in turn is the only component allowed to hold and use connector credentials.
  See the Connector Gateway boundary in Document 3 and Invariant Register, item
  15.
- **`prepareWrite` vs. `executeApprovedWrite` split is deliberate:** it is the
  mechanism that makes the approval gate real rather than advisory — see §14 of
  the task / §15 (ActionProposal) below.

---

## 15. ActionProposal

```ts
type ActionProposalStatus =
  | "pending_confirmation"   // frontend calls this "pending"
  | "approved"
  | "processing"
  | "completed"
  | "cancelled"
  | "failed"      // [production-only] — no failure path exists in the mock today
  | "expired";    // [production-only] — see invariant below

interface ActionProposalField {
  label: string;
  value: string;
}

interface ActionProposal {
  id: string;
  chatId: string;
  runId?: string;                 // [production-only] — no Run concept exists in the mock
  connectorId: string;
  actionType: string;             // e.g. "send_email"
  title: string;
  summary?: string;               // frontend uses the assistant Message text as the lead-in instead
  displayFields: ActionProposalField[];   // frontend field name: fields
  payloadReference?: string;      // [production-only] — see invariant below
  status: ActionProposalStatus;
  createdAt: string;
  approvedAt?: string;
  completedAt?: string;
}
```

- **Purpose:** the exact, user-visible representation of a proposed
  state-changing action, and the record that the UI's Approve/Cancel controls act
  on.
- **Producer:** Agent Runtime (via the Connector Gateway's `prepareWrite`).
- **Consumer:** Experience Layer (`ActionProposalCard`), Connector Gateway
  (execution), Application/API layer (persistence, audit).
- **Status mapping note:** the frontend's `ActionProposalStatus` is `"pending" |
  "approved" | "processing" | "cancelled" | "completed"` — no `failed` or
  `expired`, because the mock never fails or expires an action. Production needs
  both (a write can genuinely fail at the connector, and a stale unapproved
  proposal should expire rather than remain forever actionable) — these are
  additive states the UI does not yet have explicit presentation for. **This is
  called out as an Open Question**, not silently assumed: the current
  `ActionProposalCard` component has no visual state for "failed" or "expired,"
  and adding one is a small but real UI change that falls outside this frozen
  pass. Production should treat `failed`/`expired` as valid backend states from
  day one even before the corresponding UI states are designed, so the contract
  doesn't need to change later.

### Security invariant (critical, non-negotiable)

**Approval must be bound to the exact action payload/version shown to the user.**
If the underlying payload the proposal represents changes after the user has seen
it — for any reason, including a race with another process, a stale cache, or a
retried run — the existing approval becomes invalid and the user must be shown
the new payload and asked to confirm again. This is why `payloadReference` exists
as a field: production must resolve it to a content hash or immutable version
identifier of the exact payload the user approved, and `executeApprovedWrite`
must re-verify that the payload has not changed since `approvedAt` before
executing. **No production implementation may execute a write against a payload
the user did not literally see and approve.** See Invariant Register, item 13,
and the mandatory write data-flow in Document 3.

---

## 16. Action approval record

```ts
interface ActionApprovalRecord {
  id: string;
  userId: string;
  actionProposalId: string;
  payloadVersion: string;     // hash or immutable version id, resolved from payloadReference
  decision: "approved" | "rejected";
  decidedAt: string;
  workspaceScope: WorkspaceScope;
  chatId: string;
}
```

- **Purpose:** the durable, auditable record of a user's approve/reject decision,
  distinct from the mutable `ActionProposal.status` field it causes to change.
- **Producer:** Application/API layer, written the moment the user clicks
  Approve or Cancel.
- **Consumer:** Audit/Observability boundary, Connector Gateway (as the
  authorization check immediately before `executeApprovedWrite`).
- **Not implemented yet — documented only.** This record does not exist in the
  frontend (there is no approval history anywhere in the UI) and is not being
  built in this pass. It exists here so that Stage 7 of the build sequence
  (Document 4) has a concrete target rather than an implied one.

---

## 17. Knowledge source

```ts
type KnowledgeScope = "global" | "project";

interface KnowledgeSource {
  id: string;                   // stable document identity — never changes across revisions
  title: string;
  documentType: string;        // frontend field name: docType
  scope: KnowledgeScope;
  projectId?: string;          // required when scope === "project"
  owner?: string;               // [production-only] — described in UX_SPEC, not in frontend type
  currentRevisionId?: string;   // [production-only] — points at the KnowledgeRevision currently
                                 // surfaced by retrieval; see revision policy below
}

// [production-only] — added by the Stage 0 pass (05_STAGE0_ARCHITECTURE_DECISIONS.md §3)
// to resolve the gap between the original optional `revisionId` field and the
// citation-provenance guarantee in Document 01 §4 / Document 03 §5.
interface KnowledgeRevision {
  id: string;                   // immutable per content version — this is what a Citation pins to
  documentId: string;           // -> KnowledgeSource.id
  version: string;               // human-facing version label (e.g. "v2.1")
  approvalStatus: "approved";   // frontend union has exactly one value today — see note below
  sourceLocation?: string;      // [production-only] — e.g. a reference into the controlled repository
  validFrom?: string;           // [production-only]
  validUntil?: string;          // [production-only]
  updatedAt?: string;           // [production-only]
}
```

**Revision policy (locked in Stage 0, `05_STAGE0_ARCHITECTURE_DECISIONS.md` §3):**
retrieval returns specific `KnowledgeRevision`s, not just `KnowledgeSource` IDs.
A revision is immutable once created — editing a document's content produces a
*new* `KnowledgeRevision` under the same `KnowledgeSource.id`; the old revision
is never mutated or deleted, even once superseded or the source document is
revoked. Revocation only removes a revision from *future* retrieval results —
it never invalidates a `Citation` that already pinned to it. This is what lets
`Citation.revisionId` (§18) remain resolvable indefinitely, satisfying the
"citations resolve to exact provenance" invariant even as documents change
over time.

- **Purpose:** canonical metadata for one approved document, sufficient to
  support identity, versioning, approval status, ownership, scope, and citation
  resolution.
- **Producer:** for Global scope — the controlled external repository ingestion
  process (out of scope for this frontend entirely; see Document 1 §6). For
  Project scope — Project Settings → Knowledge (currently read-only in the
  frontend; there is no "add knowledge document" affordance in the UI today,
  matching `PRODUCT.md`'s explicit "no global knowledge administration" and the
  absence of any Project-Knowledge-add control in `ProjectSettingsModal.tsx`).
- **Consumer:** Knowledge/Retrieval Service (source of retrieval), Experience
  Layer (Project Settings → Knowledge list), Citation contract (§18, by
  reference).
- **`approvalStatus` note:** the frontend's `KnowledgeDocument["status"]` type is
  literally `"approved"` — a single-value union, because the prototype only ever
  shows approved documents (there is no "pending approval" or "rejected" state
  anywhere in the UI, matching `PRODUCT.md`'s explicit exclusion of document
  approval workflows from this phase). Production's real approval pipeline may
  have more states internally, but this frontend-facing contract should only ever
  expose documents that have already cleared approval — do not surface
  pending/rejected documents through this contract to the current UI.
- **Deliberately not designed:** a full enterprise KM schema (retention,
  classification, legal hold, lineage, etc.). Only what's needed to support
  identity, versioning, approval status, ownership, scope, and citation
  resolution is defined here, per instruction.

---

## 18. Citation

```ts
interface Citation {
  id: string;
  documentId: string;         // frontend field name: docId
  revisionId?: string;        // [production-only] — optional in the type for backward
                                // compatibility with pre-revisioning/mock citations, but
                                // REQUIRED for every production-issued Citation; see the
                                // revision policy in §17 and 05_STAGE0_ARCHITECTURE_DECISIONS.md §3
  documentTitle: string;      // frontend field name: docTitle
  version: string;
  approvalStatus: "approved"; // mirrors KnowledgeSource.approvalStatus
  scope: KnowledgeScope;
  scopeLabel: string;         // e.g. "Global approved baseline", "<Project name> knowledge"
  section?: string;
  excerpt: string;
  sourceReference?: string;   // [production-only] — resolves back to KnowledgeSource.sourceLocation
}
```

- **Purpose:** evidence used for one specific answer — deliberately a separate
  contract from `KnowledgeSource`, because a Citation is a *usage* of a source in
  a particular response (with a specific excerpt and section), not the source
  itself.
- **Producer:** Agent Runtime, from Knowledge/Retrieval Service results.
- **Consumer:** Experience Layer — this contract must allow the existing source
  drawer (`SourceCitation.tsx`) to render with **zero additional guesswork**:
  every field the drawer displays today (title, "Approved" badge, version,
  document id, scope label, section, excerpt) has a direct 1:1 field here. This
  is already true of the frontend's current `Citation` type; production must not
  remove or rename any of these without a corresponding UI change (which is out
  of scope).
- **Invariant:** a Citation always resolves, directly or via `sourceReference`,
  to a real `KnowledgeSource`. There is no such thing as a citation to nothing.
  For every production-issued Citation, it resolves specifically to an exact,
  immutable `KnowledgeRevision` (§17) — never to a document reference that
  could mean a different thing tomorrow than it meant when the citation was
  created.

---

## 19. Grounding result

```ts
type GroundingResult = "grounded" | "insufficient";

interface GroundingDetail {
  result: GroundingResult;
  searchedScopes?: string[];   // present for both outcomes in production; frontend only sets it for "insufficient"
  usedSourceIds?: string[];    // [production-only]
  reasonCode?: string;         // [production-only] — free-form diagnostic, not user-facing
}
```

- **Purpose:** distinguishes a successfully grounded answer from an explicit
  "approved knowledge insufficient" outcome — the two valid grounding outcomes
  the UI must render differently (citations vs. the calm no-answer state).
- **Frontend representation note:** the frontend's actual `Message.groundingResult`
  field is `GroundingResult = "insufficient"` — a single-value optional field
  where *absence* implicitly means "grounded." This works for the UI (it only
  ever branches on `groundingResult === "insufficient"`), but it is not an
  explicit two-value union. **Recommendation for production:** keep the
  frontend-facing field exactly as-is (optional, only ever `"insufficient"` when
  present) to avoid any UI change, but have the Agent Runtime/Application layer
  reason internally about the explicit `"grounded" | "insufficient"` union shown
  above — i.e., the richer `GroundingDetail` is an internal/production
  contract, and only its "insufficient" branch needs to cross into the
  Message contract the frontend already reads. This is a case where the
  production-internal contract is intentionally slightly richer than the
  frontend-facing field, by design, not by oversight.
- **Invariant (hard):** `insufficient` is never represented as, logged as, or
  surfaced as an infrastructure failure. It is a valid, expected product outcome
  with its own event type (`GroundingInsufficient`, §8) distinct from
  `RunFailed`.

---

## 20. Attachment

```ts
type AttachmentSource = "chat" | "project_file" | "knowledge_document"; // [production-only] discriminator

interface Attachment {
  id: string;
  name: string;
  mimeType?: string;         // [production-only] — frontend does not track this
  size?: string;              // frontend: human-readable meta string (e.g. "2.4 MB"), already formatted
  source: AttachmentSource;   // [production-only] — see note
  status?: "selected" | "uploading" | "available" | "failed"; // [production-only]
  kind: "file" | "folder";    // frontend-existing
}
```

- **Purpose:** something the user attached to the current turn/chat.
- **Frontend today:** `Attachment { id, kind, name, meta? }` is purely local,
  mocked selection metadata — there is no real upload, no `mimeType`, no
  `status`. The `meta` string is pre-formatted display text (e.g. file size),
  not structured data.
- **Producer:** Experience Layer (file/folder picker), eventually the File
  Service once real upload exists.
- **Consumer:** Application/API layer (attaches to the persisted user Message on
  send), Agent Runtime (turn context only — never automatically Knowledge).

**The three-way distinction that must never collapse:**

| | Chat attachment | Project File | Knowledge document |
|---|---|---|---|
| Scope | one turn/chat | one Project | Global or Project |
| Governance | none | none — explicitly "not automatically approved knowledge" | approved |
| Where shown | composer, message bubble | Project Settings → Files | Project Settings → Knowledge / citations |
| Frontend type | `Attachment` | `ProjectFile` | `KnowledgeDocument` / `KnowledgeSource` |

These remain three distinct contracts even though all three are "files," because
they carry fundamentally different governance and lifecycle guarantees. See
Invariant Register, items 7–8.

---

## 21. File lifecycle (conceptual)

```text
selected → uploading → available → (deleted)
                     ↘ failed
```

- **Purpose:** the states a file attachment/upload passes through, for whichever
  UI element is showing it (attachment chip, Project Files row).
- **Not designed here:** actual cloud storage implementation, virus/malware
  scanning implementation, or content-processing pipelines. Only the lifecycle
  boundary is documented: **malware/security scanning, file-type/size policy
  enforcement, and access control must sit in the File Service** (Document 3),
  strictly before a file transitions from `uploading` to `available`. A file that
  fails scanning or policy transitions to `failed`, never to `available`, and is
  never handed to the Agent Runtime as context.
- **Invariant:** an `available` chat attachment or Project File is never
  automatically promoted to Knowledge — promotion, if it ever exists as a
  product feature, would be an explicit separate user/admin action, not a
  side-effect of upload completing.

---

## 22. Usage

```ts
interface UsageBreakdownItem {
  label: string;
  percent: number;
}
interface ConnectorUsageItem {
  label: string;
  actions: number;
}
interface UsageSummary {
  periodLabel: string;
  messagesUsed: number;
  messagesLimit: number;
  modelBreakdown: UsageBreakdownItem[];
  thinkingBreakdown: UsageBreakdownItem[];
  connectorBreakdown: ConnectorUsageItem[];
}
```

- **Purpose:** the minimum data needed to populate the existing Usage panel
  exactly as built — this contract is already an exact match to the frontend's
  `UsageSummary` type (`src/types.ts`), because the mock was designed to be
  realistic-but-simple from the start.
- **Producer:** a metering/usage service (Document 3, Stage 9).
- **Consumer:** Experience Layer (Settings → Usage) only.
- **Explicitly not:** a billing engine. No invoicing, no plan/tier modeling, no
  payment concepts. If billing becomes a product requirement later, it is a new
  contract layered on top of usage metering, not an extension of this one.

---

## 22a. Real-time streaming context (Skill/Effort at run-start)

Not a separate named contract, but worth stating explicitly since it affects
several contracts above: when an `AssistantRun` starts, the Agent Runtime must
snapshot `Chat.selectedModelId`, `Chat.thinkingEffort`, `Chat.activeSkillId`, and
`Chat.connectorIds` **at that moment** and use that snapshot for the entire run,
even if the user changes one of these values in the composer mid-generation. This
matches frontend behavior implicitly (the mock reads `state.draft.*` once, at
send time, to build the new Chat) and prevents a run from silently changing
behavior partway through based on a setting the user adjusted for their *next*
message.

---

## 23. Error contract

```ts
type ErrorCode =
  | "validation_error"
  | "authentication_error"   // [added in Stage 0] — "who are you" failure; distinct from
                               // authorization_error ("you can't do this") because the
                               // correct client response differs: re-authenticate vs. don't retry
  | "authorization_error"
  | "connector_unavailable"
  | "knowledge_insufficient"   // see invariant below — this is not really an "error"
  | "run_failure"
  | "upload_failure"
  | "action_failure"
  | "not_found"
  | "rate_limited"
  | "internal_error";

interface SafeError {
  errorCode: ErrorCode;
  userMessage: string;     // pre-written, calm, product-language copy — never a raw provider message
  retryable: boolean;
  correlationId?: string;  // for support/debugging; never shown as raw stack trace to the user
}
```

- **Purpose:** the only shape of error information that may reach the Experience
  Layer.
- **Producer:** every backend boundary (Application/API, Agent Runtime,
  Knowledge Service, Connector Gateway, File Service) must translate its
  internal failures into this shape before they cross into anything the browser
  receives.
- **Consumer:** Experience Layer.
- **Per-code retryability:** see the retryability table locked in
  `05_STAGE0_ARCHITECTURE_DECISIONS.md` §9 — not repeated here to avoid two
  sources of truth for the same table.
- **Invariant (hard):** `knowledge_insufficient` is listed here only for
  completeness of the taxonomy of "things that can prevent a normal happy-path
  answer" — but as established in §19, an insufficient-grounding outcome is a
  **valid product result**, delivered via `GroundingInsufficient` (§8), not via
  this error contract. If a future implementation is tempted to model
  "insufficient" as a thrown error, that is incorrect — it must remain a
  first-class successful-run outcome. This entry exists in the enum only so
  that logging/observability has one consistent taxonomy to reference; it should
  essentially never appear inside a `SafeError` payload delivered to the UI.
- **Invariant:** raw provider/backend error messages, stack traces, and
  internal exception text must never be forwarded to the browser. Every surface
  the UI shows (`ConnectorUnavailableCard`, failed uploads, a failed run) must be
  backed by a `SafeError` with pre-written, calm copy consistent with the
  product's existing tone (see the strict-grounding and connector-unavailable
  copy already in the frontend as the tone bar).
