# Teams Tool Contract

Status: **Design only. No runtime code in this pass.** This document defines
the five initial Teams tools available to `incident_manager`
(`docs/AGENT_CONTRACT.md` §7 — the Teams specialist agent, previously named
`bridge_engineer` and renamed; see `docs/AGENT_CONTRACT.md` §1). It is the
concrete Teams-domain instance of the generic Connector tool contract already
established in `docs/implementation-handoff/02_INTERFACE_CONTRACTS.md` §14
and the Connector Gateway boundary in
`docs/implementation-handoff/03_INTEGRATION_BOUNDARIES.md` §6 — it does not
replace those, it fills them in for Teams specifically.

---

## 1. Execution boundary

```text
incident_manager (Gemini/ADK reasoning, ADK specialist agent invoked by
                  team_manager via AgentTool — docs/AGENT_CONTRACT.md §4)
        │  calls only these 5 tool functions, by name, with typed params
        ▼
tools/teams/*.py  (deterministic, non-agent code — docs/AGENT_CONTRACT.md §5)
        │  write tools (create_chat, send_message) call approval/policy_gate.py
        │  FIRST — see §6, §7, and docs/AGENT_CONTRACT.md §11
        ▼
gateway/power_automate_client.py  ── the ONLY Power Automate HTTP client
        │                             abstraction in the system (no per-tool
        │                             client; see docs/AGENT_CONTRACT.md §16)
        ▼
Power Automate  ── the permanent Microsoft 365 execution gateway
        │
        ▼
Microsoft Teams (via whatever connector Power Automate itself uses internally)
```

**Hard rules (non-negotiable, per instruction):**

- **Power Automate is the only path to Microsoft 365 from this system.**
  No tool in this contract, and no future Teams tool, may call Microsoft
  Graph or any other Microsoft 365 API directly.
- **Do not implement Microsoft Graph directly, anywhere in this stack.** The
  `tools/teams/*.py` layer's job is to shape a request for a Power Automate
  flow and shape that flow's response back into this contract's typed output
  — it is not a Graph client and must not become one, even as an
  implementation shortcut.
- **There is exactly one Power Automate HTTP client abstraction:**
  `gateway/power_automate_client.py`. No tool file introduces its own client
  (there is no `tools/teams/client.py`); every tool under `tools/teams/`
  calls the shared gateway module.
- **Power Automate flow URLs and any associated key/secret live only in
  `gateway/power_automate_client.py`'s server-side configuration** (resolved
  from Secret Manager/config via `config/settings.py` at call time). They are
  never included in a model prompt, a tool's return value, an agent-to-agent
  message (`docs/AGENT_CONTRACT.md` §8), a log line visible outside the
  backend, or any response that could reach the Application/API layer's
  frontend-facing surface. `incident_manager` calls tools by name with typed
  parameters and receives typed results — it never sees a URL.
- **`incident_manager` is the only caller of these five tools.** `team_manager`
  never calls a `teams_*` tool directly (`docs/AGENT_CONTRACT.md` §6).
- **Write-tool execution is gated by deterministic backend code, not by agent
  reasoning.** `teams_create_chat` and `teams_send_message` each call
  `approval/policy_gate.py` before calling `gateway/power_automate_client.py`
  — see §6, §7, and `docs/AGENT_CONTRACT.md` §11. Prompt instructions alone
  are not a sufficient approval control.
- Every tool call is subject to the standard Authorization Boundary
  (`03_INTEGRATION_BOUNDARIES.md` §11) — connector availability
  (Document 02 §13) and per-operation authorization are checked before
  execution, not assumed from an earlier check in the same turn
  (`05_STAGE0_ARCHITECTURE_DECISIONS.md` §12).

---

## 2. Tool summary

| Tool | Class | Confirmation required? | Teams concept |
|---|---|---|---|
| `teams_list_chats` | Read | No | Discover chats visible to the calling identity |
| `teams_get_messages` | Read | No | Retrieve messages in one chat |
| `teams_get_members` | Read | No | Retrieve members of one chat |
| `teams_create_chat` | Write | **Yes** — enforced by `approval/policy_gate.py` | Create a new chat |
| `teams_send_message` | Write | **Yes** — enforced by `approval/policy_gate.py` | Send a message into an existing chat |

This matches the read/write split already locked in
`01_IMPLEMENTATION_HANDOFF.md` §5 and `04_BACKEND_BUILD_SEQUENCE.md`
Invariant Register item 12: read/search/retrieve may execute directly
(subject to authorization); write/send/create requires explicit approval,
with no exception for an "obviously safe" write. Read tools require no
confirmation and are called freely by `incident_manager`; write tools cannot
execute without passing the deterministic backend check regardless of what
`incident_manager` or `team_manager` "decided."

---

## 3. `teams_list_chats` (read)

**Purpose:** resolve which Teams chat(s) a request refers to, or enumerate
chats available to the calling identity.

```text
Input:
  query?: string        // optional free-text hint (chat name / participant name)
  limit?: number         // default a small page (e.g. 20); pagination cursor is
                           // an implementation detail, not fixed here

Output:
  chats: {
    chatId: string        // Teams' own chat id, opaque to this system — never
                            // reused as a product-facing primary key (05_STAGE0 §1)
    title: string          // display name, or a synthesized label for 1:1 chats
    participantCount: number
    lastActivityAt?: string
  }[]
```

- No confirmation required.
- If `query` matches nothing, returns `chats: []` — not an error. This is
  what backs `IncidentManagerResponse.outcome: "no_result"` in
  `docs/AGENT_CONTRACT.md` §8.
- If `query` matches more than one plausible chat and the caller needs exactly
  one, disambiguation is `incident_manager`'s responsibility (returning
  `outcome: "ambiguous"` upstream) — this tool itself just returns candidates,
  it does not guess.

---

## 4. `teams_get_messages` (read)

**Purpose:** retrieve messages from one specific, already-resolved chat,
paging automatically (deterministically, backend-side) through as much
history as the retrieval ceiling allows.

```text
Input:
  chatId: string          // required — must be a chatId previously returned
                            // by teams_list_chats or otherwise already known;
                            // this tool never accepts a free-text chat name
  maxMessages?: number     // optional safety ceiling on total messages
                             // retrieved across all pages; defaults to 200,
                             // always clamped to a hard cap of 1000 —
                             // this tool never retrieves unlimited history

Output:
  messages: {
    id: string
    author: string          // display name, never a bare directory id
    text: string             // cleaned, readable text — see §4b
    rawContent: string       // original, unmodified Teams content
    contentType?: string     // "html" | "text" | ... as Teams reported it
    sentAt: string            // ISO 8601
  }[]                          // de-duplicated by id, ordered oldest -> newest
  retrievedCount: number
  oldestRetrievedAt?: string
  newestRetrievedAt?: string
  truncated: boolean          // true only when the ceiling was reached while
                                // older messages may still exist
  nextBefore?: string          // cursor to resume from, when truncated
```

- No confirmation required.
- **This is the sole source of truth for any summarization, Q&A, or
  action-item extraction `incident_manager` performs for this chat in this
  turn** (`docs/AGENT_CONTRACT.md` §7, §10). `incident_manager` must not
  answer a question about chat content without first calling this tool for
  the relevant `chatId` in the current delegation chain — it must not rely on
  content it recalls from an earlier, separate call.
- Empty `messages` is a valid, non-error result (`outcome: "no_result"`
  upstream) — e.g., a genuinely empty or newly created chat.
- **This is a bounded retrieval, never a claim of complete chat history.**
  `truncated`/`nextBefore` tell `incident_manager` (and, through it,
  `team_manager`) whether older messages beyond what was fetched may exist —
  a summary/answer must not be presented as covering "the whole chat" when
  `truncated` is `true`.

### 4a. Pagination (proven against the live gateway)

The live Power Automate `teams.getMessages` flow is configured with
`Top=50`, `OrderBy=createdDateTime desc`, its own paging toggle off, and
accepts an optional `before` (ISO-8601) request field: omitted → newest up
to 50 messages; supplied → `createdDateTime lt before`, i.e. the next older
page. `teams_get_messages` walks this cursor itself, in a plain deterministic
loop inside the tool implementation — **`incident_manager` never sees or
drives an individual page request; pagination is not something the model
decides turn-by-turn** (`docs/AGENT_CONTRACT.md` §5 and §11's "not by agent
reasoning" principle, applied here to retrieval the same way it already
applies to write-tool approval).

Stopping conditions (checked after each page, in this order): a page
returned fewer than 50 messages (natural end of history) → not truncated; a
page returned zero messages → not truncated; the retrieval ceiling
(`maxMessages`, default 200, hard cap 1000) was reached while a full
50-message page had just come back → `truncated: true`; the next cursor
would not be strictly older than the current one (a stalled/misbehaving
response) → stop safely, not truncated. Messages are de-duplicated by `id`
across pages before being returned in chronological (oldest → newest) order.

**Known v1 limitation:** the pagination cursor is `createdDateTime`, a
timestamp, not an opaque server-issued token. If more messages than fit in
one page ever share the exact same `createdDateTime` at a page boundary, the
strict `lt` filter could in principle skip same-timestamp messages beyond
what a single page returned at that instant. This is a tradeoff of
timestamp-based paging, not a defect in the stopping logic above.

### 4b. Normalization

Teams message content is HTML for `contentType: "html"` messages (e.g.
`<p>text&nbsp;</p>`, `<emoji alt="...">`, `<attachment ...>`), confirmed
against the live gateway. `text` is that content run through a deterministic
(no-LLM) normalizer: block tags (`<p>`, `<div>`) and `<br>` become line
breaks, HTML entities are decoded, `<emoji>` becomes its `alt` value (or a
`"[emoji]"` fallback), and `<attachment>` becomes `"[Attachment]"` rather
than being silently dropped. `rawContent`/`contentType` always retain the
original, unmodified content, so nothing is lost by cleaning `text`.

---

## 5. `teams_get_members` (read)

**Purpose:** retrieve the member list of one specific, already-resolved chat.

```text
Input:
  chatId: string           // required, same constraint as teams_get_messages

Output:
  members: {
    id: string              // Teams' own member/user id, opaque, never reused
                              // as a product-facing primary key
    displayName: string
  }[]
```

- No confirmation required.

---

## 6. `teams_create_chat` (write)

**Purpose:** create a new Teams chat.

```text
Input:
  chatName?: string        // optional — Teams allows unnamed group chats;
                             // omit for a 1:1 or unnamed group chat
  participants: string[]   // required, at least one — identities (e.g. UPN/
                             // email) as stated by the user; resolved against
                             // the M365 directory by Power Automate as part of
                             // this call — there is no separate identity-lookup
                             // tool or agent (docs/AGENT_CONTRACT.md §5)
  initialMessage?: string  // optional — a first message to send on creation

Output (on execution — see below for the approval-gated path to get here):
  chatId: string
  createdAt: string
```

- **Confirmation required, enforced deterministically.** This tool's
  implementation (`tools/teams/create_chat.py`) must call
  `approval/policy_gate.py` before calling
  `gateway/power_automate_client.py`, on every invocation, with no bypass
  path. The gate:
  1. looks up the `ActionProposal`/`ActionApprovalRecord` for the supplied
     `approvedPayloadReference` (`docs/AGENT_CONTRACT.md` §8, §11);
  2. confirms its status is `approved` (not `pending_confirmation`,
     `cancelled`, `expired`, or already `completed`);
  3. re-derives the payload digest of the exact `chatName` /
     `participants` / `initialMessage` about to be sent and confirms it
     matches the approved digest (`05_STAGE0_ARCHITECTURE_DECISIONS.md` §12);
  4. only on a full pass does the tool proceed to
     `gateway/power_automate_client.py`. Any failure raises an
     `action_failure` `SafeError` (§8) and no call to Power Automate is made.
  `incident_manager` invoking this tool is necessary but never sufficient for
  it to execute — the gate is the actual control.
- The proposal shown to the user before approval must display, verbatim:
  `chatName` (if present), the full `participants` list, and
  `initialMessage` (if present) — these are exactly the
  `ActionProposal.displayFields` (Document 02 §15). No field this tool
  actually sends may be hidden from or paraphrased in that display.
- **Idempotency:** a retried call for the same `approvedPayloadReference`
  must not create a second chat. `approval/policy_gate.py` (or a companion
  idempotency check colocated with it) is responsible for this — e.g. by
  keying on `(actionProposalId, payload digest)` and returning the original
  `chatId` on a duplicate call, not by trusting Power Automate/Teams to
  deduplicate on its own.

---

## 7. `teams_send_message` (write)

**Purpose:** send a message into an existing Teams chat.

```text
Input:
  chatId: string            // required — must already exist; this tool never
                              // creates a chat as a side effect
  body: string               // required — the exact message text to send

Output (on execution):
  messageId: string
  sentAt: string
```

- **Confirmation required, enforced deterministically.** Same rule as
  `teams_create_chat` (§6): `tools/teams/send_message.py` calls
  `approval/policy_gate.py` first, on every invocation, with the same
  four-step check (approval record exists and is `approved`; payload digest
  re-derived and matched; only then does the call reach
  `gateway/power_automate_client.py`).
- The proposal shown to the user before approval must display, verbatim,
  which chat (resolved to a human-readable title via `teams_list_chats`, not
  a bare `chatId`) and the full `body` text — no summarization or truncation
  of the body in the confirmation UI.
- **Idempotency:** a retried call for the same `approvedPayloadReference`
  must not send a duplicate message — same mechanism as §6.
- **`incident_manager` must not alter `body` between preparation and
  execution.** The payload-binding invariant (Document 02 §15,
  `05_STAGE0_ARCHITECTURE_DECISIONS.md` §12) requires that if the message
  text were to change for any reason between proposal and execution, that is
  a *new* payload requiring a *new* proposal and a *new* approval — never a
  silent substitution at send time. `approval/policy_gate.py`'s digest
  re-check (§6) is what actually catches this, independent of
  `incident_manager`'s behavior.

---

## 8. Error handling

Every tool call that fails at the Power Automate layer, the underlying M365
call, the approval/policy gate, or an authorization re-check must surface to
`incident_manager` as a `SafeError` (Document 02 §23) — never a raw Power
Automate/Graph error message, HTTP body, or stack trace. Suggested mapping
onto the existing `ErrorCode` taxonomy:

| Situation | ErrorCode |
|---|---|
| Chat/message/member not found (e.g. stale `chatId`) | `not_found` |
| Teams connector not connected / not enabled for the Project | `connector_unavailable` (routed via the existing `ConnectorUnavailableInfo` pattern, not this table, when detected before a tool call is attempted — see `docs/AGENT_CONTRACT.md` §14) |
| Power Automate flow itself errors or times out | `run_failure` |
| Power Automate/Teams rate limiting | `rate_limited` |
| Malformed tool input (should not happen if `incident_manager` validates first, but the boundary must not trust that) | `validation_error` |
| `approval/policy_gate.py` check fails (missing/unapproved record, or payload digest mismatch at execute-time — §6, §7) | `action_failure` — deliberately not `validation_error`; the proposal was valid when shown, the mismatch/rejection is a late-breaking integrity failure, matching the distinction already drawn in `05_STAGE0_ARCHITECTURE_DECISIONS.md` §9 |
| Anything else unexpected from the gateway | `internal_error` |

`incident_manager` returns these as `IncidentManagerResponse.safeError`
(`docs/AGENT_CONTRACT.md` §8); `team_manager` renders them using the same
calm, pre-written copy pattern already established for `SafeError` elsewhere
in the product (Document 02 §23) — never a raw error surfaced to the user.

---

## 9. Anti-hallucination rule (restated for this contract specifically)

None of these five tools may be "simulated" by the model — i.e.
`incident_manager` must never produce a plausible-looking `chats` /
`messages` / `members` / `executionResult` payload without an actual,
current-turn call to the corresponding tool having returned it. This is what
makes `docs/AGENT_CONTRACT.md` §7's "must not invent Teams content" rule and
§10's grounding rule enforceable rather than aspirational: the *only* way
`data` can appear in an `IncidentManagerResponse` is by copying it from a
real tool result.

---

## 10. Non-goals of this pass

- No additional Teams tools (e.g. reactions, threaded replies, channel
  operations as distinct from chats, adaptive cards) are defined here — only
  the five named in the task.
- No Power Automate flow implementation, no flow URLs, no authentication
  mechanism for the `gateway/power_automate_client.py` → Power Automate hop
  is decided here — see `docs/AGENT_CONTRACT.md` §16 for the module-boundary
  proposal this contract assumes.
- No `approval/policy_gate.py` implementation — its required behavior is
  specified (§6, §7, `docs/AGENT_CONTRACT.md` §11) but not built in this
  pass.
- No frontend change. `Connector` (`connector-teams`) and its existing
  availability model (Document 02 §12–§13) are used as-is; this document does
  not add a UI-facing concept.
