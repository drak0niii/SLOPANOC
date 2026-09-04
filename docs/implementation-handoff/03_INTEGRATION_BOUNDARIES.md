# 03 — Integration Boundaries

> Historical planning document, written before backend implementation began.
> Superseded by README.md, docs/AGENT_CONTRACT.md, and
> docs/TEAMS_TOOL_CONTRACT.md for current architecture.

Status: **UI/UX frozen.** This document defines logical components and the trust
boundaries between them. It intentionally does not commit to specific cloud
products, frameworks, or vendors beyond what the source-of-truth docs already
name (Google Cloud Storage is mentioned in `CLAUDE.md`/`PRODUCT.md` only as the
*expected* future source for the Global approved baseline — not as a general
architecture decision, and it is treated that narrowly here).

Contracts referenced below (`Chat`, `Project`, `ActionProposal`, etc.) are defined
in `02_INTERFACE_CONTRACTS.md`.

---

## 1. Logical components

```text
┌─────────────────┐
│ Experience Layer │  (frozen React frontend)
└─────────┬────────┘
          │
┌─────────▼────────────┐
│ Application/API Layer │  (backend-for-frontend)
└─────────┬─────────────┘
          │
┌─────────▼────────┐      ┌──────────────────────┐      ┌────────────────────┐
│  Agent Runtime    │─────▶│ Knowledge/Retrieval   │      │ Connector Gateway   │
│                    │      │ Service               │      │                     │
└─────────┬──────────┘      └──────────┬────────────┘      └──────────┬──────────┘
          │                            │                              │
          │                 ┌──────────▼────────────┐        ┌────────▼─────────┐
          │                 │ Controlled Document     │        │ External systems │
          │                 │ Repository (Global) +   │        │ (Outlook,        │
          │                 │ Project Knowledge store │        │ SharePoint, etc.)│
          │                 └────────────────────────┘        └──────────────────┘
          │
┌─────────▼────────────────┐
│ Action Approval / Policy  │  (gate — see §6)
│ Gate                      │
└────────────────────────────┘

     also, orthogonal to all of the above:
┌─────────────────┐  ┌───────────────┐  ┌──────────────────────────┐
│ File Service     │  │ Persistence   │  │ Audit / Observability     │
└─────────────────┘  └───────────────┘  └──────────────────────────┘
```

---

## 2. Experience Layer

**Is:** the frozen React frontend in this repository.

**Responsible for:**
- Rendering the approved UI exactly as built (sidebar, composer, conversation,
  Projects, Settings, citation drawer, action confirmation, etc.).
- Local interaction state: the `NewChatDraft`, sidebar collapse, which
  popover/menu is open, in-progress text input.
- Rendering streamed run events (§8 of Document 2) as they arrive.
- Collecting the user's explicit confirmation decision (Approve & Send / Cancel)
  and submitting it.
- Displaying provenance (citations, source drawer) exactly as delivered.
- File/folder selection UI (the actual upload mechanics belong to the File
  Service, reached only through the Application/API layer).
- Settings UX (Usage, Connectors, Skills, Project Settings) as a thin
  read/write surface over the Application/API layer.

**Must NOT contain:**
- Business authorization logic (e.g. "can this user see this Project" must never
  be decided client-side).
- RAG/retrieval logic of any kind.
- Connector credentials, tokens, or secrets, ever, in any form.
- Knowledge approval logic.
- External action execution of any kind — the browser never calls Outlook,
  SharePoint, Teams, or any other external system directly.

**Trust level:** untrusted. Every decision the Experience Layer makes about what
the user is allowed to do is advisory UI convenience only, and must be
independently enforced server-side (see §9, Authorization Boundary).

---

## 3. Application/API layer (backend-for-frontend)

**Responsible for:**
- Authenticated session handling (validates identity established by whatever
  auth provider is chosen — see Open Questions).
- Persistence-backed CRUD for `Chat`, `Project`, `Skill`, and their relationships.
- File registration (delegating actual storage to the File Service).
- Initiating `AssistantRun`s and streaming their events back to the client.
- Accepting action-approval submissions and forwarding them to the Action
  Approval / Policy Gate.
- Serving Settings data (Connectors catalogue + state, Skills, Usage summary).
- Enforcing that every request is scoped to an authorized `User` and, where
  applicable, an authorized `Project`.

**Boundary rule:** the browser never talks to the Agent Runtime, Knowledge
Service, Connector Gateway, or File Service directly. Everything the Experience
Layer needs goes through this layer. This is what keeps sensitive enterprise
systems, model providers, and connector credentials entirely out of reach of the
browser.

---

## 4. Agent Runtime

**Responsible for:**
- Orchestrating one `AssistantRun` end to end.
- Model invocation (routing `Model.id` + `ThinkingEffort` to the correct
  provider call — see Document 2 §9–§10).
- Context assembly: composing Chat history, Project instructions, active Skill
  instructions, and (via the Knowledge Service) retrieved approved sources into
  the actual model context.
- Applying the active Skill's `instructions` when `Chat.activeSkillId` is set.
- Applying Project instructions for every Project-scoped run.
- Calling the Knowledge/Retrieval Service to fetch authorized approved sources,
  and **owning the final `grounded` vs. `insufficient` label decision** — using
  the Knowledge Service's retrieved evidence and confidence signal as required
  input, never labeling a response `grounded` using evidence the Knowledge
  Service did not actually return. (Ownership split clarified in
  `05_STAGE0_ARCHITECTURE_DECISIONS.md` §10 — the Knowledge Service reports
  what it found and how well it matches; the Agent Runtime decides whether
  what was found actually supports the answer it produced.)
- Selecting and invoking connector tools (via the Connector Gateway's tool
  contract — Document 2 §14) when a request needs read/search/retrieve or
  proposes a write.
- Producing grounded responses with citations, or an explicit
  `GroundingInsufficient` outcome.
- Producing `ActionProposal`s for write-class operations — never executing them
  itself.

**Must NOT bypass:**
- Authorization (every retrieval and every tool call is subject to the
  Authorization Boundary, §9).
- The Connector Gateway (no direct external-system calls, no raw SDK usage).
- The Action Approval / Policy Gate (no write executes without a verified
  approval).
- Knowledge governance (no retrieval outside what the Knowledge Service
  authorizes for this user/workspace).

**Trust level:** trusted execution environment, but explicitly **not** trusted
with connector credentials (see Connector Gateway, §5) and explicitly not the
place where approval decisions are made or enforced (see §6).

---

## 5. Knowledge / Retrieval Service

**Responsible for:**
- Discovering approved sources — Global baseline and Project-scoped knowledge as
  two distinct, composable scopes (`KnowledgeScope = "global" | "project"`,
  Document 2 §17).
- Filtering everything it returns by the requesting user's and workspace's
  authorization — a user must never receive a source they are not entitled to,
  regardless of what the Agent Runtime asks for.
- Revision-aware retrieval (so a citation always resolves to the exact version
  of a document that was actually used).
- Generating citation/provenance data in the shape defined by the Citation
  contract (Document 2 §18).
- Supporting strict grounding by reporting retrieved candidates with a
  confidence/relevance signal per candidate — **the Knowledge Service reports
  evidence and match quality; it does not itself decide whether the overall
  question is answerable.** That final `grounded`/`insufficient` label is owned
  by the Agent Runtime (see §4 and `05_STAGE0_ARCHITECTURE_DECISIONS.md` §10),
  using this service's output as required input. The Knowledge Service must
  never silently return zero results dressed up as a normal empty response —
  a weak or empty match set is itself a meaningful signal the Agent Runtime
  needs, not something to paper over.

**Global baseline + Project approved knowledge are distinct but composable:**
a Project-scoped query is expected to search both scopes and may cite either
(exactly as the current mock's `Citation.scope`/`scopeLabel` already
distinguishes). Global scope alone is searched for General-workspace queries.
Neither scope ever substitutes silently for the other — a Project chat missing a
Project-specific answer does not get topped up from ungoverned general model
knowledge; it gets `GroundingInsufficient`.

---

## 6. Connector Gateway

**Responsible for:**
- Connector registration and the canonical `Connector` catalogue (Document 2
  §12).
- Credential handling and storage for every connected external system.
- Per-user, per-operation authorization checks before any tool call executes.
- Exposing the normalized tool contract (Document 2 §14) — `listCapabilities`,
  `validateAuthorization`, `executeRead`, `prepareWrite`, `executeApprovedWrite`
  — as the *only* interface the Agent Runtime is allowed to use.
- Executing read operations directly (subject to authorization).
- Preparing write operations as an `ActionProposal` — never executing a write
  itself until instructed by the Action Approval / Policy Gate.
- Executing a write only after the gate confirms a valid, unexpired,
  payload-matching approval.
- Normalizing errors from external systems into the `SafeError` contract
  (Document 2 §23) before anything propagates upward.
- Emitting audit events for every read and every write attempt, successful or
  not.

**Hard rule:** the LLM (and the Agent Runtime generally) must never hold or
receive external-system credentials. All credential material lives and stays
inside the Connector Gateway.

---

## 7. Action Approval / Policy Gate

**Responsible for enforcing this exact sequence, with no shortcuts:**

```text
proposed action (ActionProposal, status: pending_confirmation)
→ user approval (ActionApprovalRecord written)
→ payload/version verification (the payload has not changed since it was shown)
→ authorization re-check (the user is still authorized for this operation, now)
→ execution (Connector Gateway's executeApprovedWrite)
```

**Hard rule:** no state-changing external action may go directly from "model
proposed it" to "connector executed it." This gate is mandatory and sits
logically between the Agent Runtime/Connector Gateway's `prepareWrite` output and
its `executeApprovedWrite` input — it is not optional middleware, and it is not
something an individual connector integration can opt out of. See Document 2 §15
for the payload-binding invariant this gate is responsible for enforcing.

---

## 8. File Service

**Responsible for:**
- Upload lifecycle (`selected → uploading → available/failed`, Document 2 §21).
- Storage abstraction (technology unresolved — see Open Questions).
- File metadata (name, size, mime type, owner, scope: chat attachment vs. Project
  File).
- Access control per file.
- Malware/security scanning, strictly before a file is marked `available`.
- Handing off to whatever content-processing pipeline a future feature needs
  (e.g. if Project Files are ever promoted to Knowledge — an explicit, separate,
  not-yet-designed action, never an automatic side effect of upload).

**Hard rule:** upload completing is not the same event as Knowledge approval.
These remain two separate actions with two separate authorization models, even
though both eventually involve the File Service for the underlying bytes.

---

## 9. Persistence boundary

Logical persistence is required for:

- Users / authorization references (owned by whatever identity system is
  chosen — the Application/API layer stores only what it needs to join against
  it).
- `Project` records.
- `Chat` records.
- `Message` records.
- `Skill` records.
- Connector configuration (global catalogue state + per-Project enablement).
- Attachments/Files (metadata; bytes live wherever the File Service's storage
  abstraction puts them).
- `ActionProposal` and `ActionApprovalRecord`.
- `Citation` / provenance references (at minimum enough to reconstruct what a
  historical message cited, even if the underlying `KnowledgeSource` has since
  changed version).
- Usage/audit metadata.

**No database technology is selected here** — this is a logical requirement list
only (see Open Questions).

---

## 10. Audit / Observability boundary

Production must eventually support auditability for: user prompts, assistant
runs, which knowledge sources were used per run, every connector/tool call,
every proposed action, every user approval/rejection, every executed action,
failures, and security-relevant events (auth failures, authorization denials).

**Two distinct categories — do not merge them:**

- **Operational telemetry** — latency, error rates, run throughput, system
  health. Used for reliability and performance, not for individual accountability.
- **Auditable business/security records** — who did what, when, to what, with
  what approval. Used for compliance, security review, and dispute resolution.
  This is what `ActionApprovalRecord` (Document 2 §16) feeds.

**Hard rule, restated from Document 2 §7:** private chain-of-thought / raw model
reasoning must never be written into either category. Operational telemetry may
log *that* a run happened and its safe status transitions; audit records may log
*that* a tool was called with which normalized parameters and what the
approved-and-executed payload was — neither ever logs the model's internal
reasoning tokens.

---

## 11. Authorization boundary

Authorization must be checked, server-side, at minimum at every one of these
points:

- Access a Project.
- Access a Chat (including: is this Chat's Project one the user can access).
- Retrieve a source document (Knowledge Service, per-document).
- Use a connector at all (is this connector available to this user/workspace).
- Invoke a specific connector operation (read vs. write may have different
  authorization requirements even on the same connector).
- Approve an action (is this user allowed to approve this class of action).
- Execute an action (re-checked at execution time, not just at proposal time —
  see the Action Approval / Policy Gate, §7).
- Access a file (chat attachment or Project File).

**Hard rule:** authorization is never inferred from what the UI happens to show
or hide. A disabled button, a hidden menu item, or a connector rendered as
"unavailable" in the composer is a UX convenience, not a security control. Every
one of the operations above must be independently authorized at the
Application/API, Agent Runtime, Knowledge Service, or Connector Gateway layer —
whichever actually performs the operation — regardless of what the client sent.

---

## 12. Trust boundaries

| Boundary | What crosses it | Must be authenticated | Must be authorized | Must never cross |
|---|---|---|---|---|
| Browser → Application/API | User actions, draft/Chat/Project data, approval decisions | Yes (session) | Yes, per-operation | Connector credentials, raw model output internals, other users' data |
| Application/API → Agent Runtime | Run start requests, Chat/Project context references | Yes (service-to-service) | Yes (Application/API has already checked Project/Chat access) | End-user credentials for external systems |
| Agent Runtime → Knowledge Service | Retrieval queries scoped to workspace + user | Yes | Yes (Knowledge Service re-checks; does not trust Agent Runtime's word alone) | Unfiltered access to documents outside the caller's authorization |
| Agent Runtime → Connector Gateway | Tool calls (read execution, write preparation) | Yes | Yes (Gateway re-checks per-operation) | Connector credentials (flow the other way only, and never past the Gateway) |
| Connector Gateway → External system | Normalized API calls, using Gateway-held credentials | Yes (Gateway's stored credential) | Enforced by the external system + Gateway's own policy | End-user identity should be passed only if the external system needs it for its own audit; raw internal prompts/reasoning never |
| Agent Runtime / Application → Controlled Document Repository | Retrieval/read requests only | Yes | Yes | Write access — this frontend and its backend are read-only consumers of the Global baseline, which is system/admin-managed outside this product |

---

## 13. Data flow — normal grounded response

```text
User sends prompt
→ Application/API layer (validates session, Chat/Project authorization)
→ create/start AssistantRun
→ Agent Runtime
  → build context (Chat history + Project instructions + active Skill instructions)
  → call Knowledge Service: retrieve authorized approved sources
    (Global baseline, plus Project knowledge if Project-scoped)
→ Model invocation (using Chat.selectedModelId + Chat.thinkingEffort)
→ grounded response text + citations produced
→ stream TextDelta / CitationAdded events to frontend (Document 2 §8)
→ RunCompleted
→ persist final Message (with citations) via Application/API layer
```

---

## 14. Data flow — strict no-answer

```text
User prompt
→ Agent Runtime → Knowledge Service: authorized retrieval
→ retrieval returns insufficient evidence to answer
→ Agent Runtime/Model does NOT supplement from uncontrolled/generic knowledge
→ GroundingInsufficient event (with searchedScope) streamed to frontend
→ RunCompleted (this is a successful run with an "insufficient" outcome —
  never a RunFailed)
→ UI displays the approved calm no-answer state (NoAnswerNotice), never an error
```

---

## 15. Data flow — read connector

```text
User prompt
→ Agent Runtime determines a read/search/retrieve tool call is needed
→ authorization check (is this connector + operation available to this user/workspace)
→ Connector Gateway: executeRead(connectorId, operation, params)
→ External system
→ structured result returned to Connector Gateway → Agent Runtime
→ Agent Runtime incorporates the result into a grounded/traceable response
→ streamed to frontend as a normal response (citations if applicable)
```

No user confirmation is required solely because a read occurred, provided
authorization permits it — this matches Document 1 §5 exactly.

---

## 16. Data flow — write connector (mandatory sequence)

```text
User request implies a write (e.g. "send this summary to John")
→ Agent Runtime determines the proposed action and its exact payload
→ Connector Gateway: prepareWrite(connectorId, operation, params)
  → validates the action shape against the connector's schema
  → returns an ActionProposal (status: pending_confirmation), NOT executed
→ ActionProposed event streamed to frontend
→ UI renders the exact proposal (ActionProposalCard: title, fields, body) —
  this is the literal payload the user is being asked to approve
→ User clicks Approve & Send
→ Application/API layer writes an ActionApprovalRecord (decision: approved)
→ Action Approval / Policy Gate:
  → verifies the payload has not changed since it was shown (payload/version check)
  → re-validates authorization (still allowed, right now)
→ Connector Gateway: executeApprovedWrite(actionProposalId)
→ result returned
→ ActionStatusChanged events stream to frontend (approved → processing → completed,
  or → failed)
→ UI updates the Action Proposal card to its terminal state
```

This flow is mandatory for every write-class connector operation, with no
exceptions and no fast path that skips the approval gate, regardless of how
"obviously safe" a particular write might seem.
