# Stage 0 Architecture Decisions

> Historical planning document, written before backend implementation began.
> Superseded by README.md, docs/AGENT_CONTRACT.md, and
> docs/TEAMS_TOOL_CONTRACT.md for current architecture.

Status: **UI/UX frozen. No backend implemented in this pass.** This document
resolves the architecture decisions required before backend implementation can
safely begin, per the approved Implementation Handoff package
(`docs/implementation-handoff/01`–`04`). It does not implement anything. Where
this pass discovered a genuine ambiguity or gap in the existing four documents,
that finding is stated explicitly (not silently fixed), and any resulting
contract change is applied narrowly and reported in §13.

---

## Executive summary

The handoff package (Documents 01–04) is internally sound on the product-level
rules that matter most — Project/Skill/Chat separation, the connector
availability model, the write-approval gate, and the knowledge-hierarchy rules
all hold up consistently across all four documents and against the frozen
frontend. Two genuine gaps were found during this review (grounding-decision
ownership was ambiguous between two documents; citation revision-binding was
under-specified relative to the trust guarantee it's meant to support) and both
are resolved below, with narrow, targeted updates to Documents 02 and 03.

This pass makes 15 decisions required before Stage 1 can begin (identity
strategy, Chat-creation transaction semantics, authentication/authorization
boundary shape, Project access model, persistence ownership, Message/Run
separation, Skill and Project-instructions revisioning, the error taxonomy, and
the security invariant set). It defers roughly 20 further decisions to their
naturally-relevant later stage, and classifies a small number as
implementation-specific details that do not need to become product-level
architecture decisions at all. No frontend behavior changes. No backend code is
written.

---

## Handoff package consistency review

Per-topic check, as requested:

| Area | Finding |
|---|---|
| IDs and references | Consistent, but incomplete — no document previously fixed a generation strategy, uniqueness requirement, or provider-ID-leakage rule. Not a contradiction; resolved in §1 below. |
| Ownership of state | Consistent in spirit (Document 3 §3–§9) but not tabulated per-entity. Resolved in §6 below. |
| Chat vs Draft lifecycle | Consistent across Documents 01 and 02. No issue found. |
| Project inheritance | Consistent across Documents 01, 02, 03. No issue found. |
| Skill scope | Consistent. `Project` has no Skill field in the type, in Document 02, or in Document 03. No issue found. |
| Connector scope | Consistent three-layer model (global state / Project enablement / Chat selection) across Documents 01, 02, 03. No issue found. |
| Run snapshot behavior | Consistent but incomplete — Document 02 §22a only names Model/Effort/Skill/connectors; Project instructions, attachments, and Knowledge revisions were not addressed. Resolved in §3 below. |
| Citation provenance | **Genuine gap found.** `Citation.revisionId` was marked optional/production-only in Document 02 §18 with no stated policy, which is not strong enough to satisfy Document 01 §4's and Document 03 §5's trust guarantee that citations "resolve to exact source provenance." Resolved in §3/§13 below. |
| Grounding behavior | **Genuine ambiguity found.** Document 03 §4 (Agent Runtime) states the Agent Runtime "call[s] the Knowledge/Retrieval Service... and determine[s] groundedness," while Document 03 §5 (Knowledge Service) states the Knowledge Service is responsible for "returning an explicit 'insufficient' signal." Both cannot be the sole owner of the same decision without contradiction. Resolved in §3/§13 below. |
| Action approval lifecycle | Consistent across Documents 01, 02, 03. Payload-binding invariant stated identically in all three. No issue found. |
| File distinctions | Consistent three-way distinction (chat attachment / Project File / Knowledge document) across all documents. No issue found. |
| Error semantics | Consistent, but incomplete — Document 02 §23's `ErrorCode` union conflates authentication and authorization failure under one code. Resolved in §3/§13 below. |
| Authorization boundaries | Consistent list in Document 03 §11; no contradiction found, but "does the frontend receive permissions or only derived capabilities" was left open. Resolved in §3 below. |
| Persistence assumptions | Consistent list of what needs persisting (Document 03 §9) but no owning-service assignment. Resolved in §6 below. |

No other contradictions were found. Everything else in the four documents is
taken as-is and extended, not corrected.

---

## Decisions required before Stage 1

### 1. Canonical ID strategy

**Decision:** All product-facing IDs (`User`, `Project`, `Chat`, `Message`,
`AssistantRun`, `Skill`, `Connector`, `Attachment`/File, `KnowledgeDocument`,
`KnowledgeRevision`, `Citation`, `ActionProposal`, `ActionApproval`) are
**server-generated**, minted by the owning service (see §6) at the moment of
successful persistence — never client-generated and trusted as authoritative.
All IDs are **globally unique** (UUID/ULID/KSUID-family, not per-table
auto-increment integers). For entities where chronological listing is a primary
access pattern — `Chat`, `Message`, `AssistantRun`, `ActionProposal` — IDs
should additionally be **sortable by creation time** (ULID/KSUID/UUIDv7-style);
for entities without that access pattern (`User`, `Project`, `Skill`,
`Connector`, `KnowledgeDocument`), plain random UUIDs are sufficient. **Product
IDs must never be, or be derived from, a provider/external-system ID** —
`Model.id` never becomes a provider's model string, `Connector.id` never
becomes an external OAuth client ID, `KnowledgeDocument.id` never becomes a
repository object path, and a completed `ActionProposal` may *reference* an
external system's own object ID (e.g. an Outlook message ID) as a separate
field, but that external ID never becomes the product's primary key for the
record.

**Why:** client-minted IDs make idempotency harder to reason about (a retry
could mint a new ID instead of naturally deduplicating — see §2) and create
collision risk across sessions/devices. Global uniqueness avoids leaking
sequential counts (e.g., "how many Projects exist") and simplifies future
multi-service/multi-region deployment without an ID migration. Keeping product
IDs independent of provider IDs is what lets the Model catalogue, connector
catalogue, and Knowledge repository all change or be re-platformed underneath
the product without invalidating every historical Chat, Citation, or
ActionProposal that references them.

**Contract impact:** none of the Document 02 contracts' `id: string` fields
change shape — this decision fixes *how* those IDs are generated and *what they
may never be derived from*, not their type.

**Future flexibility preserved:** a database/ID-library choice can still be made
freely at implementation time — this decision fixes ID *shape properties*
(unique, sometimes sortable, provider-independent), not a specific technology.

---

### 2. Chat-creation transactional/idempotency semantics

**Decision:** "First send" (and every subsequent send) is one logical,
atomic operation at the Application/API layer, made idempotent via a
**client-generated `sendId`** (a UUID, generated once per send attempt, before
the network call — not the entity's ID, purely a deduplication key):

```text
1. Client generates sendId, includes it on the send request.
2. Server checks: has this sendId already been processed?
   → yes: return the previously-created result (chatId, userMessageId,
     assistantMessageId, runId) unchanged. Nothing new is created.
   → no: proceed.
3. In a single transaction: create the Chat if this is the first message
   (or resolve the existing Chat), create the user Message, create the
   placeholder assistant Message + AssistantRun, record sendId against
   this result. Commit.
4. A client retry using the SAME sendId after a network failure is caught
   at step 2 — no duplicate Chat, Message, or Run is ever created.
```

A retry with a genuinely new `sendId` (e.g. the user actually sent a second,
distinct message) is treated as new — idempotency protects against
network-level retries of the *same* logical send, not against the user sending
twice on purpose.

**Why:** the three creations (Chat, user Message, placeholder assistant
Message + Run) are transactionally inseparable from the product's perspective
— Document 01 §2 already describes them as one atomic step. A single
idempotency key covering the whole compound operation is simpler and safer
than three separate keys that could partially succeed and desynchronize.

**Contract impact:** none to Document 02's `Chat`/`Message`/`AssistantRun`
shapes. Adds one new, purely transport-level concept (`sendId`) that is not
part of any persisted entity.

**Future flexibility preserved:** the exact HTTP/transport shape of the send
request is not fixed here — only that it carries a client-generated
deduplication key and that the server treats the three creations as one
transaction.

---

### 3. Persisted-entity revision policy: Skill, Project instructions, Knowledge

**Decision:**
- **Skill:** each `updateSkill` produces a new immutable revision (a snapshot
  of name/description/instructions); `Skill.id` stays stable (already true in
  Document 02 §11). An `AssistantRun` records which Skill *revision* it used,
  not just which Skill ID. No versioning UI — Settings continues to show and
  edit only the current revision; revisions exist purely for audit/
  reproducibility.
- **Project instructions:** the same pattern — each instructions update
  produces an immutable revision; a Chat's runs are NOT given a permanent copy
  of the instructions text (no duplication onto `Chat`); each `AssistantRun`
  records which Project-instructions revision it resolved and used at that
  moment.
- **Knowledge:** `KnowledgeSource` (Document 02 §17) is extended with an
  explicit, first-class `KnowledgeRevision` concept — see the narrow Document
  02 update in §13. Retrieval returns specific revisions, not just document
  IDs; a `Citation`, once created, **permanently and immutably references the
  exact `KnowledgeRevision` used** — `revisionId` is required (not merely
  optional) for every production-issued Citation. Revisions are never deleted
  outright even if a document is later superseded or revoked — only future
  retrieval stops surfacing a revoked revision; historical citations continue
  to resolve to exactly what was used.

**Why:** without this, a Skill or Project-instructions edit — or a Knowledge
document being revised or revoked — would silently change the meaning of a
*historical* run or citation, breaking the reproducibility and trust guarantees
Documents 01 and 03 already promise ("citations must resolve to exact
provenance"). Pinning a revision at the moment it's used, rather than a live
reference, is the only way to keep history stable while still always using
current content for new runs.

**Contract impact:** narrow additions to Document 02 §17/§18 (see §13). No
change to `Skill` or `Project`'s frontend-facing shape — revisioning is an
internal/production-only concept layered underneath the existing IDs, exactly
as the handoff's `[production-only]` field pattern already establishes
elsewhere.

**Future flexibility preserved:** revision storage/compaction strategy (e.g.
whether old revisions are ever archived to cold storage) is left to
implementation; only the requirement that they remain resolvable is locked.

---

### 4. Authentication vs. authorization boundary shape

**Decision:** Authentication establishes a stable `User.id` (Document 02 §1)
via a session credential validated on **every** Application/API request; there
is no unauthenticated endpoint in this product. Authorization is a fully
separate, per-operation, server-side concern (Document 03 §11), never
inferred from client state. **The frontend receives only derived
capabilities/outcomes (e.g. an operation succeeds or returns
`authorization_error`), never raw roles or permission grants.** Session expiry
must result in a clean re-authentication path, not a silent failure — the
frozen frontend currently has no login/re-auth surface at all, which is
flagged as a genuine future UI need (not solved in this pass).

**Why:** this is exactly Invariant 14 ("authorization enforced server-side...
never inferred from client-side UI state") made concrete for the session/
identity layer specifically — the frontend already has zero
permission-aware conditional rendering anywhere, which is the correct pattern
to preserve rather than retrofit a client-side permissions model onto later.

**Contract impact:** confirms and slightly sharpens Document 02 §1's existing
authentication-vs-authorization distinction; no shape change.

**Future flexibility preserved:** the actual identity *provider* remains an
open question (Document 04) — this decision fixes only the boundary's shape,
not its implementation, so Stage 1 can be built against a stubbed session
interface before a provider is chosen.

---

### 5. Project authorization model (V1 minimum)

**Decision:** V1 uses **single-owner Project access** — a Project is
accessible to the `User` who created it (via an `ownerId`-equivalent field),
gating every Project-scoped operation (Chat list/create, Settings read/write).
No membership list, no per-Project roles, no sharing/invitation flow.

**Why:** the frozen frontend has zero UI for adding collaborators, viewing
members, or setting roles anywhere in Project Settings — building a
multi-member authorization model now would be speculative complexity with no
UI to exercise or validate it against.

**Contract impact:** `Project` (Document 02 §3) gains an owner/creator
reference used for authorization; no other shape change.

**Future flexibility preserved:** a later multi-member model can be added as
an additive authorization layer on top of the single-owner baseline (e.g. an
owner can always act as if they were the sole "member") without breaking
existing Projects.

---

### 6. Persistence ownership

**Decision:**

| Domain | Owning service | Note |
|---|---|---|
| Project | Application/API layer | includes owner reference (§5) |
| Chat | Application/API layer | |
| Message | Application/API layer | Agent Runtime produces assistant content but reports it through the Application/API layer, which performs the write |
| Skill (+ revisions) | Application/API layer | |
| AssistantRun (status/metadata) | Application/API layer | Agent Runtime drives transitions but reports them through, rather than writing directly |
| ActionProposal | Application/API layer | content produced by Agent Runtime/Connector Gateway's `prepareWrite`, persisted by Application/API layer |
| ActionApprovalRecord | Application/API layer | written at the moment of user decision |
| Connector global catalogue + credentials | Connector Gateway | |
| Connector per-Project enablement | Application/API layer | it's a `Project` field, owned like any other Project configuration |
| File metadata + bytes | File Service | Application/API layer stores only references |
| Citation (as attached to a Message) | Application/API layer | |
| KnowledgeSource / KnowledgeRevision catalogue | Knowledge/Retrieval Service | |
| Usage summary | Usage/Metering service (Stage 9) | derived/aggregated, not a primary source of truth for anything else |

**Principle (locked):** no service writes another service's authoritative
state directly. A service that *produces* data owned elsewhere (Agent Runtime
producing Message content, Connector Gateway producing proposal content)
reports it through a defined interface to the owning service, which performs
the actual write. This keeps exactly one write path per entity.

**Why:** without this, two services racing to update the same record (e.g.
Agent Runtime and Application/API both thinking they own `AssistantRun.status`)
is a predictable source of production bugs; fixing the pattern now, before any
service is built, is far cheaper than untangling it after Stage 2+ exist.

**Contract impact:** none — this formalizes Document 03 §2–§9's existing
component responsibilities into an explicit per-entity table; no responsibility
was moved from where Document 03 already implied it lived.

**Future flexibility preserved:** database technology per service remains
entirely open (Document 04 Open Questions).

---

### 7. Message / AssistantRun separation and failure semantics

**Decision:** `AssistantRun` and `Message` remain **separate persisted
entities**, joined by `Message.runId` (already Document 02 §7's direction —
ratified here, not changed). A placeholder assistant Message (`status:
pending`) is created in the same transaction as the user Message and the Run
(§2). As a run progresses, the message's text is **persisted incrementally**
(not only at the end — see the streaming decision, §8), so a browser
disconnect or a mid-run failure never loses already-generated content. Every
`AssistantRun` **must** reach a terminal status (`completed`, `failed`,
`cancelled`); a run that never reaches one (e.g. due to a server crash) must be
caught by a bounded reconciliation process and marked `failed` — a Message may
never be left in `pending` indefinitely, and a failed run's partial text is
preserved and shown, marked `failed`, rather than silently discarded.

**Why:** conflating Run and Message would either bloat every Message with
execution metadata that's irrelevant once it succeeds, or force awkward
multi-attempt/retry fields onto the conversational record. Keeping them
separate keeps Message purely "what the user sees" and Run purely "how it was
produced." The terminal-status guarantee is what prevents the ambiguous-history
failure mode the task specifically calls out.

**Contract impact:** none — ratifies Document 02 §6–§7 as already specified.

**Future flexibility preserved:** the exact reconciliation timeout/mechanism is
an implementation detail, not fixed here.

---

### 8. Streaming: logical guarantees, persistence, and transport recommendation

**Decision (logical guarantees, transport-neutral):**
- **Ordering:** `TextDelta` strictly ordered by `seq` per `assistantMessageId`;
  `RunStarted` always first, `RunCompleted`/`RunFailed` always terminal; other
  event types tolerant of minor reordering.
- **Reconnect/replay:** **snapshot + resume**, not full event replay — the
  server exposes "current accumulated text + current status" as a cheap
  resumption point (enabled by incremental persistence, below) and streams only
  new events from there. Full replay of every historical delta is wasteful for
  long responses.
- **Duplicate handling:** every event type is idempotent client-side (already
  specified per-event in Document 02 §8); the **server** must also treat its
  own re-delivery attempts idempotently via `(runId, seq)` or
  `(runId, eventType, entityId)` keys — the burden isn't only on the client.
- **Completion semantics:** `RunCompleted` is emitted **only after** the
  assistant Message is durably persisted as `complete` — persistence happens
  before the event, not after, so a client that sees `RunCompleted` can trust
  the message is already safely stored.
- **Failure semantics:** `RunFailed` carries a `SafeError`; the Message's
  status becomes `failed`; partial text already generated is preserved, not
  discarded (see §7).
- **Partial text — persisted incrementally, not only at the end.** This is
  what makes reconnect-and-resume and honest partial-failure display possible.
- **Browser disconnect while a run continues:** the run is a server-side
  process, not tied to any specific connection's lifetime. A disconnected
  browser does not stop or lose the run — it continues to completion/failure
  server-side, and its result is available on reconnect or simply reloading
  the chat. Abandoning a run on disconnect would waste already-incurred
  generation cost and could leave a Chat permanently missing an answer the
  user actually sent for.

**Transport recommendation: Server-Sent Events (SSE).**

**Why:** the actual traffic pattern is unidirectional (server → client); the
frontend never needs to push data mid-stream (approvals, cancellation, and new
messages are all separate ordinary request/response calls, not part of the run
stream itself). SSE runs over plain HTTP (simpler infrastructure than
WebSocket's upgrade/connection-management needs) and has a native
`Last-Event-ID` reconnection primitive that maps directly onto the
snapshot-plus-resume requirement above. WebSocket would be justified by a
genuine bidirectional low-latency need on the *same* connection (e.g. live
collaborative editing) — none exists in the frozen UI. Even a future
cancellation control (§9) does not require WebSocket: a cancel request can be
an ordinary separate HTTP call, honored by stopping the run and emitting the
result over the existing SSE stream.

**Contract impact:** none to Document 02 §8's event shapes or ordering — this
decision fixes framing/reconnection mechanics only, exactly as the task
requires ("keep the logical event contract independent from transport").

**Future flexibility preserved:** if a genuine bidirectional need emerges
later (e.g. true low-latency cancellation-with-immediate-ack), the logical
event contract does not need to change to move to WebSocket — only the framing
layer would.

---

### 9. SafeError taxonomy freeze

**Decision:** Document 02 §23's `ErrorCode` union is extended with a distinct
**`authentication_error`**, separate from `authorization_error` (gap found in
the consistency review — see §13). Retryability is fixed per code:

| Code | Retryable | Note |
|---|---|---|
| `validation_error` | No | user must correct input |
| `authentication_error` | No (requires re-auth first) | new code, see §13 |
| `authorization_error` | No | permission must change first |
| `connector_unavailable` | Yes | transient |
| `knowledge_insufficient` | N/A | not an error — see invariant below |
| `run_failure` | Yes | transient; also covers a failed read-tool-call within a run |
| `upload_failure` | Yes | transient |
| `action_failure` | No (without a new proposal) | write was approved and then failed at execution — narrower and more consequential than `run_failure`, kept as its own code deliberately |
| `not_found` | No | |
| `rate_limited` | Yes, after backoff | |
| `internal_error` | Yes | treated as transient unless proven otherwise |

**Invariant restated (unchanged, just reconfirmed as locked):**
`knowledge_insufficient` is not really an error — it is delivered as a
`GroundingInsufficient` stream event (Document 02 §8), a successful run
outcome, never as a thrown `SafeError`. It remains in the taxonomy only for
observability/logging completeness.

**Why:** authentication and authorization failures require different client
handling (the former should trigger re-login; retrying an authorization
failure after re-login will still fail) — conflating them under one code
would force the client to guess which one actually happened.

**Contract impact:** narrow addition to Document 02 §23 (see §13).

**Future flexibility preserved:** none needed — this is a closed, small
taxonomy extension.

---

### 10. Grounding-decision ownership

**Decision:** **Knowledge/Retrieval Service** owns evidence retrieval and
returns candidate `KnowledgeRevision`s plus a per-candidate confidence/
relevance signal — it does not itself decide whether the *overall question* is
answerable. **Agent Runtime** owns the final `grounded`/`insufficient` label,
using the Knowledge Service's retrieved evidence as required, non-optional
input — it may label a response `grounded` only when it can point to specific
retrieved revisions that the generated answer directly cites with an excerpt;
it may never label a response `grounded` using knowledge the retrieval step
did not actually return. Minimum evidence for a `grounded` label: at least one
cited `KnowledgeRevision` per claim the grounding depends on — an answer with
zero citations is never `grounded` (already true of Document 02 §6's Message
invariant).

**Why:** this resolves the genuine ambiguity found in the consistency review
between Document 03 §4 and §5 — both documents implied ownership without
reconciling it. Splitting "what evidence exists" (retrieval's job — it has no
way to judge whether an answer *uses* the evidence correctly) from "is this
specific answer actually supported" (generation's job — only the component
producing the answer can know what it actually relied on) is the only split
that avoids the answering component grounding itself in evidence it never
looked at, or the retrieval component making a judgment about text it never
generated.

**Contract impact:** narrow clarifying update to Document 03 §4/§5 (see §13).
No change to Document 02's `GroundingResult`/`GroundingDetail` shapes.

**Future flexibility preserved:** retrieval scoring algorithms and the exact
confidence-threshold logic are explicitly not designed here or anywhere in
this pass.

---

### 11. Connector credential ownership (confirmed)

**Decision (confirmed, not new):** the Connector Gateway is the sole owner and
holder of all connector credentials — never the frontend, `Chat`, `Project`, or
any LLM prompt/context. Global connection state (`Connector.state`) and Project
enablement (`Project.enabledConnectorIds`) remain the two independent
dimensions already modeled by the frozen frontend.

**Why:** already Document 03 §6's position; restated here as formally locked
because it is directly referenced by several other Stage 0 decisions (§12, the
idempotency and concurrency registers) and should not be left only implicit.

**Contract impact:** none.

**Future flexibility preserved:** n/a — this is a hard security boundary, not
meant to flex.

---

### 12. Connector/action authorization timing and payload binding

**Decision:** authorization is checked at every point Document 03 §11 already
lists, **plus** the following timing is locked explicitly: tool selection is
checked against the full three-layer availability model (Document 02 §13)
before any call is attempted; read execution re-checks authorization
immediately before executing, not reusing the tool-selection check; action
proposal creation authorizes only *showing* a proposal, never execution;
approval authorizes the user's decision, not the write itself; **final write
execution re-checks authorization one more time, independently of the
approval-time check** — a previous approval never substitutes for a fresh
permission check at execution time.

Payload binding (already Document 02 §15's principle) is locked mechanically:
`ActionProposal.payloadReference` resolves to a content-addressed digest of
the exact payload shown to the user; `ActionApprovalRecord.payloadVersion`
records that digest at approval time; execution re-derives the digest of the
payload it is about to send and rejects execution on any mismatch, requiring a
new proposal and a new approval. A `pending_confirmation` proposal **expires**
after a bounded window rather than remaining actionable indefinitely (exact
duration is an implementation parameter).

**Why:** time passes between proposal, approval, and execution — permissions,
connector health, and even the proposal's own payload can all change in that
window. Re-checking at each step, rather than trusting an earlier check, is
what makes the approval gate a real security control rather than a one-time
formality.

**Contract impact:** none — this locks Document 02 §15's and Document 03 §7's
existing principles as non-negotiable mechanics rather than changing them.

**Future flexibility preserved:** the exact expiry duration and digest
algorithm are implementation parameters, not fixed here.

---

### 13. Audit taxonomy and retention separation

**Decision:** the auditable/telemetry split already stated in Document 03 §10
is confirmed and locked as the taxonomy Stage 1 onward must use from day one
(since `ActionApprovalRecord`, a Stage 7 concept, is itself an audit record and
benefits from the framing existing early). **Message/Chat retention and audit
retention are separate, independently-configurable policies** — a user
deleting their chat history must not delete the compliance/audit trail, and
audit retention must not block or complicate a user's data-deletion request.
Exact durations remain open (Document 04 Open Questions).

**Why:** conflating the two either breaks compliance (audit disappears when a
user deletes chats) or breaks user trust/data rights (deletion requests
blocked by audit retention). Separating them avoids both failure modes and is
cheap to decide now, before any retention-coupled schema exists.

**Contract impact:** none new — ratifies Document 03 §10.

**Future flexibility preserved:** exact retention windows remain open
questions, deliberately not decided here per the instruction not to invent
numbers the product hasn't defined.

---

### 14. Model abstraction boundary (confirmed) + historical-Chat stability

**Decision:** the Document 02 §9 boundary (`Model.id` → Model Configuration
Registry → provider/model/version) is confirmed unchanged. **New:** a
`Chat.selectedModelId`, once set, is never retroactively rewritten even if
that model is later deprecated. The Registry must retain enough historical
mapping to keep routing old chats to a still-callable deprecated model, or to
apply a documented per-model fallback (mirroring §15's Effort-fallback
treatment). If a model becomes fully unroutable, new messages in that chat
surface a clear `SafeError` (a `model_unavailable`-class case falling under
`run_failure`) rather than silently switching the user to a different model
without telling them.

**Why:** silently changing which model answers a historical conversation
violates the same "don't surprise the user" spirit that governs strict
grounding, even though it wasn't one of the originally-named invariants —
surfaced during this review and worth locking now, before any Model Registry
is built.

**Contract impact:** none to Document 02 §9's shape.

**Future flexibility preserved:** the Registry's internal fallback-mapping
mechanism is not designed here.

---

### 15. Effort-mapping fallback behavior (confirmed)

**Decision:** Document 02 §10's mapping boundary is confirmed. For a
Model/Effort combination the provider doesn't support directly, the adapter
applies a **deterministic, documented per-model fallback** (e.g. mapping
`extended` to that model's maximum available reasoning budget) — never a
silent, undocumented substitution, and the *stored* `thinkingEffort` value
always reflects what the user selected, never rewritten to reflect what the
provider actually did.

**Why:** keeps the frontend's two-value contract exactly as-is while giving
the backend room to handle real provider heterogeneity, without ever making
the stored product data lie about user intent.

**Contract impact:** none.

**Future flexibility preserved:** per-model fallback tables are an
implementation/configuration concern, not fixed here.

---

## Decisions deferred to later stages

| Decision | Required before stage | Reason deferred |
|---|---|---|
| Run cancellation UI trigger | (deferred indefinitely, not stage-gated) | Frozen UI has no Stop control; backend capability (`cancelled` status, cancel API) already exists in the contract and needs no further Stage 0 work — see rationale below |
| Run cancellation backend capability | Stage 2 | Contract already supports it (`RunStatus.cancelled`); implementation is Stage 2 scope, not a new architecture decision |
| Attachment/authorization-scope of a run's snapshot for connector calls | Stage 2 | Execution-time revalidation already locked (§12); no further decision needed until Stage 2 builds actual run execution |
| Model/provider mapping (which real models) | Stage 3 | Model Registry content is a vendor/provider selection, not an architecture boundary decision (boundary already locked, §14) |
| Effort per-model fallback tables | Stage 3 | Implementation configuration, not architecture |
| Knowledge repository adapter contract (GCS specifics) | Stage 4 | Canonical `KnowledgeSource`/`KnowledgeRevision` shape is locked (§3); the repository-specific adapter is Stage 4 implementation |
| Knowledge indexing implementation (embeddings/lexical/hybrid) | Stage 4 | Explicitly out of scope per the original handoff instruction; retrieval scoring is not designed anywhere in this pass |
| Connector credential ownership model (per-user vs per-org) | Stage 6 | Gateway ownership is locked (§11); the finer-grained "whose credential is it" question doesn't block Gateway design |
| Connector `reconnect_required` state | Stage 6 (optional) | Minor, optional UI/state addition; maps cleanly into existing `error` state for V1 — implementation-specific, does not need to become a product decision now |
| File size/type/quota limits | Stage 5 | No existing requirement defines numbers; must be configured but isn't an architecture question |
| Malware scanning vendor/mechanism | Stage 5 | Implementation selection |
| Encrypted-storage approach | Stage 5 | Implementation selection |
| Retention policy (messages/files) | Before Stage 1's schema finalizes ideally, hard-required by Stage 5 | Policy decision the product hasn't made yet; flagged, not invented |
| Audit retention duration | Stage 7 (schema), Stage 9 (enforcement) | Same — policy decision, not architecture |
| `ActionProposalStatus` failed/expired visual treatment | Before Stage 7 ships (not before Stage 7 starts) | Backend states are required from day one (§ below); only the UI presentation is a gap, and UI changes are outside this frozen pass |
| Deployment topology | Stage 0-adjacent but genuinely open | Affects trust-boundary mechanics in practice but not the logical boundaries already fixed in Document 03 |
| Persistence technology (specific DB per service) | Before Stage 1 implementation starts (not before Stage 1 is designed) | Logical ownership is locked (§6); physical technology is a vendor/tooling choice |
| Authentication provider | Before Stage 1 implementation starts (not before Stage 1 is designed) | Boundary shape is locked (§4); provider selection is vendor choice |
| Usage "connector activity" semantics | Stage 9 | Decided now for contract clarity (completed operations, both read and write) but not required to be *implemented* until Stage 9 |

---

## Run snapshot specification

| Context element | Snapshotted at run start | Revalidated at execution time |
|---|---|---|
| Model / Effort | Yes — fixed for the run's duration | N/A |
| Skill instructions | Yes — resolved revision content, not just the ID (§3) | N/A |
| Project instructions | Yes — resolved revision content, not just the ID (§3) | N/A |
| Chat connector selection (which IDs) | Yes — the set chosen at run start | Availability + authorization of each, at each individual tool call |
| Project connector enablement | Yes — the set, for context assembly | Availability + authorization, at each individual tool call |
| Attachments | Yes — fixed set of file references (content itself is immutable once `available`, per the File Service lifecycle) | Access-control re-checked at read time |
| Knowledge search scope/policy (what's searchable) | No — evaluated live, at the moment of retrieval | — |
| Specific Knowledge revisions actually retrieved/cited | Yes — pinned permanently once used, for citation stability (§3) | — |
| User authorization/permissions | No | Yes — revalidated before every sensitive operation: retrieval, each connector call, action approval, action execution (§12) |

**Distinction (as required):** context needed to make a run's *output*
reproducible and explainable later (Model, Effort, Skill/Project instruction
content, which Knowledge revisions were actually used, which attachments were
present) is **snapshotted**. Context that determines whether an operation is
currently *allowed* (connector availability, user permissions) is **always
revalidated at the moment of the operation**, never trusted from a snapshot,
because allowing a stale permission to authorize a live action would be a
security hole, not a reproducibility feature.

---

## Idempotency register

| Operation | Idempotency key/source | Expected duplicate behavior |
|---|---|---|
| First-send Chat creation | Client-generated `sendId` (§2) | Server returns the original result; nothing new created |
| Subsequent user-message submission | Client-generated `sendId` per send | Same as above |
| Run creation | Implicit — created atomically within the send transaction | Covered by `sendId`; no separate key needed |
| File registration / upload completion | Client-generated upload key per file-select action | Server returns the original file record; no duplicate metadata |
| Skill create | Recommended defensive idempotency key; not a strict product requirement | Low risk if omitted — a genuine duplicate create is a minor, recoverable nuisance, not a correctness break |
| Skill update | Naturally idempotent by content (identical updates produce identical revision content) | Harmless if duplicated; may create a redundant identical revision, acceptable |
| Connector action approval | `ActionProposal.id` + payload digest (§12) | Second approval attempt on an already-approved proposal is a no-op |
| Connector action execution | `ActionProposal.id` + payload digest re-check (§12) | Retried execution on an already-`completed`/`processing` proposal is a no-op; never double-executes externally |

---

## Concurrency register

| Scenario | Correct invariant |
|---|---|
| Same Chat open in two browser tabs | Both read the same server-authoritative state; messages from either tab appear in both via the stream/reconnect mechanism; no tab "owns" the Chat — concurrent sends from different tabs simply both append, ordered by server-assigned sequence |
| Skill edited while a run is active | The active run keeps using the Skill revision it snapshotted at start (§3); the edit applies to the *next* run, never retroactively |
| Project connector disabled while a run is active | The run's snapshotted connector set is what was available at start, but any tool call attempted *after* the disablement still fails live re-authorization (§12) — calls already completed are unaffected; calls attempted after are correctly refused |
| Connector globally disconnected after a proposal exists but before approval | The proposal stays visible/pending, but approval-time or execution-time authorization (§12) correctly fails; the proposal moves to `failed` rather than silently succeeding against a disconnected connector |
| Knowledge document revoked while a run is active | If retrieval already happened, the run's pinned citations (§3) are unaffected — it already has its evidence; revocation only affects future retrieval |
| Chat moved between Projects while a run is active | The run continues under the scope it was started with (its snapshot); the Chat's `projectId` change takes effect for the *next* message/run, not the in-flight one — the same "snapshots don't change mid-flight" rule applied uniformly |

---

## Security invariant register

1. Browser never owns connector secrets.
2. Authorization enforced server-side.
3. Model cannot bypass the Connector Gateway.
4. Write execution requires a valid approval.
5. Approval binds the exact payload/version shown to the user (§12).
6. Authorization is revalidated immediately before execution — a prior
   approval never substitutes for a fresh check (§12).
7. Retrieval always respects document-level authorization.
8. Citations refer to an exact, immutable source revision, never a mutable
   document reference (§3).
9. File access is authorization-controlled, re-checked at access time.
10. Private chain-of-thought is never exposed to the user or stored in any
    audit/telemetry record.
11. User-provided Project instructions or Skill instructions are content fed
    to the model — they can never disable or override the approval gate, the
    authorization boundary, or any other structural control, which are
    enforced by the Agent Runtime's orchestration independent of what the
    model was told.
12. Prompt injection in retrieved or connector-read content is data, never an
    instruction — only the Agent Runtime's own orchestration logic may
    initiate a write proposal; text found inside retrieved/read content can
    never itself trigger or authorize a tool call.
13. *(surfaced in this pass)* A deprecated/retired Model is never silently
    substituted for a user without an explicit, calm `SafeError` (§14) — the
    same "don't surprise the user" principle strict grounding already
    establishes, extended to model routing.

This is the security-focused subset of the fuller 23-item product invariant
register already in Document 04 §5 — no duplication of the non-security items
here; both registers should be read together.

---

## Stage 1 entry criteria

Stage 1 (identity + application persistence) may begin once the following are
true:

- Canonical contracts stable: Documents 02, 03, 04, and this document, with
  the narrow updates in §13 applied.
- ID strategy locked (§1).
- Chat/Draft lifecycle locked (already stable from the original handoff;
  reconfirmed, unchanged).
- Persistence ownership locked for Stage-1-relevant entities: Project, Chat,
  Message, Skill (§6).
- Auth/session **interface shape** locked (§4) — the concrete provider remains
  an open implementation choice and does not block starting against a stubbed
  interface.
- Authorization boundary locked for Project access (§5, single-owner V1).
- SafeError taxonomy locked, including `authentication_error` (§9).
- Idempotency pattern locked for send/Chat-creation (§2) — Stage 1's
  Message/Chat schema must accommodate it even though the send transaction
  becomes user-visible starting Stage 2.
- Skill revision model locked (§3) — needed because Stage 1 builds the Skill
  table.
- Project-instructions revision model locked (§3) — needed because Stage 1
  builds the Project table.
- Security invariant register locked (this document) — governs Stage 1's
  authorization implementation from day one.

**Not required for Stage 1** (correctly deferred, no runs exist yet in Stage
1): run snapshot semantics beyond the schema accommodation above, streaming
transport, Model/Effort provider mapping, Knowledge/connector/action
architecture, file limits, and usage semantics.

---

## Remaining blockers

Only genuine blockers to *starting* Stage 1 — not to designing it further:

1. **Authentication provider selection.** The boundary shape is locked (§4);
   Stage 1 can be designed and even partially built against a stubbed
   interface, but cannot be considered complete/shippable without a real
   provider chosen.
2. **Persistence technology selection.** Logical ownership is locked (§6);
   schema design can proceed against the Document 02 contracts, but
   implementation cannot begin writing real persistence code without a
   concrete database choice per service.
3. **Deployment/tenancy model nuance** (single-tenant vs. multi-tenant org
   structure). Affects exactly how single-owner Project access (§5) is
   enforced at the org boundary; the logical access model is sufficient to
   design against today, but final tenant-isolation mechanics depend on this
   still-open choice.

None of these block further documentation or design work — they block actual
implementation *starting*, and were already flagged with recommended timing in
Document 04's Open Questions ("before Stage 1 begins in earnest"). This pass
does not resolve them further, consistent with the instruction not to invent
decisions the product hasn't actually made yet.
