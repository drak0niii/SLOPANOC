# 01 — Implementation Handoff

> Historical planning document, written before backend implementation began.
> Superseded by README.md, docs/AGENT_CONTRACT.md, and
> docs/TEAMS_TOOL_CONTRACT.md for current architecture.

Status: **UI/UX frozen.** This document explains the product behavior a production
implementation must reproduce behind the existing, approved frontend.

Source of truth for this handoff: `CLAUDE.md`, `docs/PRODUCT.md`, `docs/UX_SPEC.md`,
and the finished frontend in `src/` (particularly `src/types.ts`,
`src/state/AppState.tsx`, `src/data/mock.ts`, and the component tree under
`src/components/`). Where the finished frontend and the original docs disagree in a
material way, both are described and the discrepancy is called out explicitly — see
`06 — Discrepancies` at the end of this document. The frontend, as built, is the
tie-breaker for interaction behavior; the docs remain the tie-breaker for product
intent.

This document is descriptive, not prescriptive. It does not introduce new UI
behavior. It explains, in implementation-relevant terms, what the frontend already
does and what the production system must therefore supply.

---

## 1. Product runtime model

The application has exactly two workspace scopes, modeled in the frontend as
`WorkspaceScope = { type: "general" } | { type: "project"; projectId }`.

### General workspace

A user may start an independent conversation that is not associated with any
Project. Before the first message is sent, no `Chat` record exists — the frontend
holds a transient, in-memory **`NewChatDraft`** (composer text, attachments,
selected Model, Effort, Skill, and chat-level connector selection). Opening the app
puts the user directly into this draft state; the composer is immediately usable.

On first send, the draft is promoted into a persisted `Chat`:

```text
Draft → Chat
Chat.projectId = null
```

### Project workspace

Opening a Project (`enterProject`) or creating one (`createProject`) immediately
switches `workspaceScope` to `{ type: "project", projectId }`, clears
`activeChatId`, and resets the draft. **There is no Project dashboard.** The main
workspace renders the same empty-composer state as General, with a `Project`
context header above it (`ProjectContextHeader`) showing only the Project name and
a settings affordance. The user can type and send immediately.

On first send inside a Project:

```text
Draft → Chat
Chat.projectId = <active Project id>
```

This is a locked product rule (see Invariant Register, item 1): **a Project must
never require any step before the composer is usable.**

---

## 2. Chat lifecycle

The frontend's actual lifecycle (`sendMessage` in `AppState.tsx`) is:

```text
Open empty state (General or Project)
→ user optionally configures Model / Effort / Skill / connectors / attachments in the draft
→ user sends
→ if no active chat: create Chat from draft (title derived from first prompt text,
  Model/Effort/Skill/connectorIds copied from the draft at send time)
→ append user Message (status: complete) and a placeholder assistant Message
  (status: pending) to the chat
→ "assistant run" begins (today: a single fixed-delay mock; see §4.5 of
  02_INTERFACE_CONTRACTS.md for the production run/streaming model)
→ assistant Message is completed in place (text, and exactly one of:
  citations / groundingResult+searchedScope / connectorUnavailable / actionProposalId)
→ composer remains usable; draft text/attachments reset, Model/Effort/Skill/
  connectorIds remain as configured (they now live on the Chat, not the draft)
```

**New Chat** (`newChat`): resets `activeChatId` to `null`, resets `workspaceScope`
to General, and resets the draft to defaults (Standard effort, no Skill, no model
override beyond default, empty connector selection, empty attachments/text). It does
**not** create a Chat record. It is available at all times, in both General and
Project scope, and always returns the user to a clean General empty state.

**Project "new chat"**: there is no separate action — entering/re-entering a Project
(`enterProject`) performs the same reset but keeps `workspaceScope` pointed at that
Project, so the resulting empty composer will create its next Chat inside that
Project.

**Switching chats** (`selectChat`): sets `activeChatId`, derives `workspaceScope`
from the selected chat's `projectId` (Project scope if set, General otherwise), and
clears the draft's transient text/attachments. Model, Effort, Skill, and connector
selection are **not** reset — they are read directly off the newly-active `Chat`
record, which is why switching chats correctly restores each chat's own
configuration.

**Delete** (`deleteChat`): removes the `Chat` and all of its `Message` records. If
the deleted chat was active, the view returns to a clean General empty state
(consistent with New Chat).

**Rename** (`renameChat`): sets `Chat.title` directly; no side effects.

**Pin / unpin** (`togglePin`): toggles `Chat.pinned` and sets/clears
`Chat.pinnedAt` to the toggle timestamp. See §2.1 for ordering semantics.

**Moving a Chat between workspaces** (`moveChatToProject`): sets `Chat.projectId`
to the destination Project id (or `null` for General). All other Chat fields —
messages, title, Model, Effort, Skill, attachments, pinned state — are preserved
unchanged. If the chat is pinned, its `pinnedAt` is refreshed to "now" **only when
moved**, so it always lands as the *last* pinned chat in the destination scope
(see §2.1). If the chat is currently active, `workspaceScope` follows it to the new
scope.

### 2.1 Pinned ordering

`Chat.pinnedAt` is the sole ordering key for pinned chats (a `pinOrder`-style
integer was considered and rejected in favor of a timestamp, since pin/unpin/move
all naturally produce monotonically increasing values without needing a counter).
Display order for any chat list (General "Chats" section, or a Project's chat
list) is: **all pinned chats first, sorted ascending by `pinnedAt`, followed by all
unpinned chats in their existing chronological order** (newest-created first, the
existing General/Project chat ordering). There is **no separate "Pinned" section**
in the sidebar — pinned chats render inline in the same list, distinguished only by
a small pin icon to the left of the title. This applies identically inside General
and inside each Project's chat list; pinned Project chats are never duplicated
elsewhere.

### 2.2 Draft reset semantics

The draft is reset to defaults (`freshDraft()`) exactly on: `NEW_CHAT`,
`ENTER_PROJECT`, `CREATE_PROJECT`, and after a chat is deleted while active. It is
**partially** reset (text and attachments only, not Model/Effort/Skill/connectors)
on `SELECT_CHAT`, because those latter fields are irrelevant once a Chat is
active — the UI reads them from the Chat, not the draft, while a Chat is selected.

---

## 3. Context inheritance

A message sent inside a Project Chat is built from three independent context
sources. This composition must be preserved exactly — none of these sources may be
collapsed into another.

**Project contributes:**
- Project instructions (`Project.instructionsText`) — always active for every chat
  in the Project.
- Approved Project knowledge (`Project.knowledgeDocIds`).
- Enabled Project connectors (`Project.connectorIds`) — connectors the Project
  *intends* to allow, independent of whether they are currently globally connected
  (see §6 and the Connector Availability Contract in Document 2).

**System contributes:**
- Approved Global baseline knowledge (system/admin-managed; not user-managed from
  this frontend; expected to originate from a controlled external repository).
- Platform/system instructions and safety controls (not modeled in the frontend at
  all — this is a pure production-side responsibility).

**Chat contributes:**
- Conversation history (its `Message` list).
- `selectedModelId`, `thinkingEffort`, `activeSkillId` — all chat-scoped, set once
  at chat creation from the draft and then mutable per-chat afterward (switching
  chats restores each chat's own values; this is verified frozen behavior).
- `connectorIds` — which of the currently-available connectors this specific chat
  has selected for use (via the composer's "+ → Add connector" picker). See the
  explicit conclusion on this in the Chat Contract (Document 2, §9): this is
  **chat-persistent**, not merely per-request context — it lives on `Chat`, exactly
  like Model/Effort/Skill, and is restored when switching back to that chat.
- Chat attachments (temporary, turn/chat-scoped files — never automatically
  Knowledge).

**Explicit rule — Skill is never inherited from a Project.** `Project` has no
Skill-related field of any kind (not `defaultSkill`, not a skill list, nothing). A
new chat opened inside a Project always starts with `activeSkillId: null` unless the
user explicitly selects a Skill for that chat. This is enforced structurally in the
frontend types (`Project` has no skill field) and must remain enforced structurally
in the production `Project` schema — see Invariant Register, item 3.

---

## 4. Grounding model

Production behavior must reproduce exactly two grounded outcomes, both of which the
frontend already models explicitly via `Message.groundingResult`:

```text
Approved sources contain an answer
→ Message.citations is populated with one or more Citation records
→ groundingResult is absent (implicitly "grounded")
```

```text
Approved sources are insufficient
→ Message.groundingResult = "insufficient"
→ Message.searchedScope lists which knowledge scopes were searched
  (e.g. "Global approved baseline", "<Project name> knowledge")
→ the UI renders this as a valid, calm answer (NoAnswerNotice), never as an error,
  with a one-click "Add a file to this chat" affordance
```

**This is a hard product requirement, not a UX nicety:** the production Agent
Runtime must never silently supplement a governed/operational answer with
ungoverned generic model knowledge when approved sources are insufficient. The
"insufficient" outcome is the correct, expected outcome in that situation, and must
be surfaced as such. See Invariant Register, item 9.

The current mock triggers this outcome deterministically by keyword (`"budget"`) —
this trigger logic is prototype-only and carries no product meaning; the production
Knowledge/Retrieval Service determines groundedness for real.

---

## 5. External action model

Two connector-operation classes exist, and the frontend already enforces the
distinction represented here:

**No approval required (may execute directly, subject to authorization):**
`READ`, `SEARCH`, `RETRIEVE`.

**Approval required before execution:** `WRITE`, `SEND`, `UPDATE`, `CREATE`,
`DELETE`, `EXECUTE`.

The frontend's `ActionProposalCard` is the concrete UI for this: it renders the
exact proposed action (title, fields, body preview) with **Cancel** / **Approve &
Send**, and the mock lifecycle it drives — `pending → approved → processing →
completed` (or `→ cancelled`) — must be treated as a genuine **execution gate** in
production, not merely an informational card. No write action may reach the
external system before the user has approved that exact proposal. See the Action
Proposal Contract and the mandatory write data-flow in Documents 2 and 3.

When the connector required for an action is unavailable, the frontend already
distinguishes **why** (`ConnectorUnavailableReason`) and routes the user
accordingly rather than presenting one generic error:

- `global_unavailable` — the connector itself is not connected/authorized at the
  workspace level → CTA routes to Global Settings → Connectors.
- `project_disabled` — the connector is globally connected but not enabled for the
  active Project → CTA routes to Project Settings → Connectors for that Project.

This distinction must be preserved by whatever computes connector availability in
production (see the Connector Availability Contract).

---

## 6. Knowledge hierarchy

Four distinct concepts exist and must never collapse into one another:

**Global approved baseline knowledge.** System/admin-managed. Not user-managed from
this frontend (there is intentionally no global knowledge-management screen). The
production source is expected to be a controlled, approved external repository
(e.g. Google Cloud Storage or equivalent) — this frontend only ever displays mock
citations that *represent* this source.

**Project approved knowledge.** Approved sources scoped to one Project
(`Project.knowledgeDocIds`), shown in Project Settings → Knowledge. Composable with
the Global baseline for a Project chat's grounding (a grounded answer inside a
Project can cite either scope — the mock's `Citation.scope`/`scopeLabel` already
models this).

**Project Files.** Workspace files attached to a Project (`Project.fileIds`,
Project Settings → Files). Explicitly **not** automatically approved Knowledge —
the UI copy says so directly ("Workspace files for this project. Not automatically
approved knowledge."). Files and Knowledge are shown in separate Project Settings
tabs with separate empty states and must remain separately governed in production.

**Chat attachments.** Files/folders attached to a single chat via the composer's
`+` menu. Turn/chat context only — never automatically Knowledge, never promoted to
Project Files automatically.

This four-way distinction (Global Knowledge / Project Knowledge / Project Files /
Chat attachments) is a locked product rule — see Invariant Register, items 6–8.

---

## Discrepancies between frozen frontend and original product docs

These are genuine differences worth flagging explicitly, per this task's
instruction not to silently resolve them:

1. **Connector "Reconnect required" state.** `UX_SPEC.md` §12.2 lists "Reconnect
   needed" as an *optional* additional connector state. The frozen frontend's
   `ConnectorState` union only implements four states: `connected`,
   `not_connected`, `permission_required`, `error`. There is no distinct
   "reconnect_required" state in code — an unreachable/expired connection is
   currently represented as `error`. Production may introduce a fifth state, but
   doing so is additive to the frontend's type, not a correction of it (see Open
   Questions).

2. **Thinking-effort naming.** `UX_SPEC.md` §5.4 gives "Standard / Deeper" as an
   *example* naming, explicitly noting "Claude may propose different naming." The
   frozen frontend uses **Standard / Extended**. This is a resolved naming
   decision, not a conflict — flagged only for traceability.

3. **Knowledge document metadata is thinner than the docs describe.**
   `UX_SPEC.md` §9.4 describes a Project Knowledge document row as potentially
   including "updated date" and "source/owner where helpful." The frozen
   frontend's `KnowledgeDocument` type is `{ id, title, docType, version, status }`
   only — no `updatedAt`, no owner/source field. The rendered UI shows only title,
   type, version, and an "Approved" badge. Production's canonical
   `KnowledgeSource` contract (Document 2) restores the richer fields the docs
   describe; the current UI simply doesn't surface them, which is an acceptable
   frontend simplification, not a contract limitation — production may return more
   than the current UI displays.

4. **Pinned-chat presentation.** `UX_SPEC.md` §8.4 offered two options ("above
   recent history **or** in a small pinned subsection"). The frozen frontend
   implements the first option only (pinned chats sorted inline above unpinned
   chats, no separate section/heading) — this was an explicit correction made
   during the UI audit pass. Not a conflict; the spec permitted this choice.

5. **No run-cancellation affordance in the UI.** Neither `PRODUCT.md` nor
   `UX_SPEC.md` mandate a "stop generating" control, and the frozen composer has
   none — the only mock "run" state is a fixed-delay `pending → complete`
   transition with no user-facing cancel action. Production streaming
   infrastructure will likely support run cancellation as a capability (see
   `04_BACKEND_BUILD_SEQUENCE.md`, Stage 2), but there is currently no frontend
   affordance to trigger it. This is listed as an open question, not assumed.

No other material discrepancies were found. The empty-state "no answer" copy, the
example action-confirmation fields ("To: John Smith" / "Subject: Incident
summary"), the four-connector-state model, the Skill/Project separation, and the
Project prompt-first entry are all implemented in the frontend exactly as the docs
describe or stricter than the docs require.
