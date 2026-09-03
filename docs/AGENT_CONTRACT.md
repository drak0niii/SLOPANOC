# Agent Contract — team_manager & incident_manager

Status: **Design only. No runtime code in this pass.** This document defines
the first two agents of the SLOPANOC multi-agent architecture: `team_manager`
(orchestrator) and `incident_manager` (Teams specialist). It is additive to,
and must be read alongside, `docs/implementation-handoff/03_INTEGRATION_BOUNDARIES.md`
(component boundaries) and `docs/implementation-handoff/02_INTERFACE_CONTRACTS.md`
(canonical contracts: `Message`, `ActionProposal`, `SafeError`, etc.). Where
this document introduces a new concept, it says so explicitly rather than
silently overloading an existing one. See §8 for how the two relate.

Tool contracts referenced below (`teams_list_chats`, etc.) are defined in
`docs/TEAMS_TOOL_CONTRACT.md`.

---

## 1. Naming

The Teams specialist agent was originally named `bridge_engineer` ("Bridge
Engineer") in the first pass of this document. It has been **renamed to
`incident_manager` ("Incident Manager")**. The role, responsibilities, and
boundaries are unchanged from the original design — only the name changed.
This is the only place "Bridge Engineer" appears in this document; every
other section uses `incident_manager` / "Incident Manager" throughout.

---

## 2. Scope

This pass defines exactly two agents:

- **`team_manager`** — root/orchestrator agent.
- **`incident_manager`** — Teams specialist / incident-collaboration agent,
  invoked by `team_manager` (§4).

No other agent is designed or implied here. A future agent (e.g. a ticketing
specialist, a SharePoint/knowledge specialist) would attach to `team_manager`
the same way `incident_manager` does — as an additional delegate — but that is
explicitly out of scope for this pass, per instruction.

---

## 3. Agent roster

| Agent | Role | Talks to user? | Talks to tools? | Owns approval? |
|---|---|---|---|---|
| `team_manager` | Orchestrator | Yes — sole agent that produces user-facing text | No — never calls a tool directly | Yes — owns the conversational approval flow |
| `incident_manager` | Teams specialist / incident-collaboration agent | No — never produces text the user sees directly | Yes — sole caller of the Teams tools (`docs/TEAMS_TOOL_CONTRACT.md`) | No — prepares write actions and executes only after approval is verified; does not decide approval itself |

This split mirrors, and is a concrete realization of, the `Agent Runtime`
boundary already established in `03_INTEGRATION_BOUNDARIES.md` §4: together,
`team_manager` and `incident_manager` **are** the Agent Runtime for
Teams-related work. Neither agent is a new trust boundary on its own — both
remain inside the Agent Runtime's existing trust boundary (§3 there): neither
holds connector credentials, neither talks to an external system directly,
and both are subject to the same Authorization Boundary
(`03_INTEGRATION_BOUNDARIES.md` §11) as any other Agent Runtime code.

---

## 4. V1 ADK topology (frozen)

```text
                ┌────────────────────── single ADK application/runtime ──────────────────────┐
                │                                                                              │
   User ───────▶│  team_manager  (ADK root agent, sole user-facing author)                     │
                │        │                                                                     │
                │        │  AgentTool call (same-turn call/return, not a                       │
                │        │  conversational hand-off — see rationale below)                     │
                │        ▼                                                                     │
                │  incident_manager  (ADK specialist agent, wrapped in AgentTool)               │
                │        │                                                                     │
                │        │  typed tool calls                                                   │
                │        ▼                                                                     │
                │  tools/teams/*  (deterministic, non-agent code)                               │
                └──────────────────────────────────────────────────────────────────────────────┘
```

**Frozen for v1:**

- `team_manager` is the ADK root agent and the sole user-facing author.
- `incident_manager` is an ADK specialist agent, invoked by `team_manager` via
  **`AgentTool`** (`team_manager.tools = [AgentTool(agent=incident_manager)]`)
  — **not** registered as a native `sub_agents` transfer target.
- Both run **in the same ADK application/runtime, in-process** — invoking
  `incident_manager` is a same-turn call/return, not a network call and not a
  conversational hand-off.
- **Networked / agent-to-agent (A2A) deployment is not an alternative for v1**
  and must not be presented as one in this document, in prompts, or in any
  implementation built from it.
- **No separate service for `incident_manager`.** `AgentTool` runs it via a
  nested, in-process ADK `Runner`/session, still inside the same application
  process as `team_manager` — this is a composition mechanism, not a
  deployment boundary.

### Why `AgentTool`, not native `sub_agents` delegation

This supersedes an earlier draft of this section, which specified native
`sub_agents=[incident_manager]` delegation. That was revised after verifying
the installed ADK (1.33.0) source directly:

- Setting `sub_agents` on an `LlmAgent` causes the framework to auto-inject a
  `transfer_to_agent` tool (`google.adk.flows.llm_flows.agent_transfer`).
  Invoking it hands the active, end-user-facing turn to the named sub-agent —
  the sub-agent's own reply becomes the visible response for that turn. (ADK's
  own `disallow_transfer_to_parent` docstring confirms this framing directly:
  setting it `True` "prevents this agent from **continuing to reply to the
  end-user**.") This is genuine conversational hand-off, not a same-turn
  call/return.
- `google.adk.tools.AgentTool` is documented as: "allows an agent to be called
  as a tool ... the agent's output is **returned as the tool's result**." It
  runs the wrapped agent via a nested `Runner`/session and returns its result
  to the *caller*, which remains in control of the turn.

Native `sub_agents` transfer therefore cannot guarantee §3's invariant that
`incident_manager` "must not communicate with the user directly, in any form"
or that `team_manager` alone authors `Message.text` (§6, §12) — the LLM could
choose to transfer the turn away instead of calling a tool. `AgentTool` is the
concrete ADK mechanism that satisfies those already-frozen invariants, so it
is used instead of the literal `sub_agents=[...]` field. This is exactly the
"concrete requirement that cannot be satisfied with native sub-agent
delegation" condition under which the runtime-implementation instructions
authorized this deviation.

Everything else about the topology — single runtime, in-process, no A2A, no
separate services, `incident_manager` never replying to the user — is
unchanged from the original intent; only the wiring mechanism changed.

A2A/network separation between `team_manager` and `incident_manager` may be
considered **later**, and only if an actual scaling, ownership, security, or
deployment boundary requires it — not as a default or a "nice to have"
flexibility. Until such a requirement is identified, the single-runtime
topology above is the only supported deployment shape.

---

## 5. Implementation principle (frozen): agent vs. tool

```text
Agent = reasoning boundary
Tool  = deterministic capability
```

An **agent** exists only where genuine reasoning, judgment, or natural-language
understanding is required across a boundary worth naming and governing
separately (as `team_manager` and `incident_manager` are). A **tool** is
deterministic code invoked by an agent with typed input and typed output — it
does not reason, and it is not itself an agent.

**Explicitly not agents, in this or any future pass built on this contract:**
`listChats` / `teams_list_chats`, `getMessages` / `teams_get_messages`,
`getMembers` / `teams_get_members`, `createChat` / `teams_create_chat`,
`sendMessage` / `teams_send_message`, summarization, confirmation, and
identity/participant lookup. Each of these is either:

- a **tool** (the five `teams_*` functions in `docs/TEAMS_TOOL_CONTRACT.md`,
  each deterministic, typed, and stateless); or
- a **capability performed by `incident_manager`'s own reasoning** over a
  tool's output (summarization, Q&A, decision/action extraction — these are
  what `incident_manager` *is for*, not a separate delegate); or
- a **deterministic backend concern with no reasoning step at all**
  (confirmation/approval enforcement — §11; participant/identity resolution,
  which is resolved by Power Automate against the M365 directory inside the
  `teams_create_chat` tool call itself, per `docs/TEAMS_TOOL_CONTRACT.md` §6
  — never a separate lookup agent or a separate exposed tool).

No future addition to this architecture should create a new named agent for a
capability that can be expressed as a tool call or as reasoning already inside
an existing agent's responsibility.

---

## 6. team_manager

### Responsible for

- Owning the user-facing conversation and all conversational state for a
  `Chat` (Document 02 §4, §6) — the only agent that reads chat history and
  produces the assistant-visible `Message` text.
- Understanding user intent well enough to decide: (a) can this be answered
  without any Teams-domain work, (b) does this need `incident_manager`, or
  (c) is information missing before either is possible.
- **Asking only for missing information.** `team_manager` must not ask the
  user to restate or confirm anything it can already resolve from chat
  history, `Project` context, or an `incident_manager` response. A clarifying
  question is only ever asked when a required parameter (see per-tool
  "Required inputs" in `docs/TEAMS_TOOL_CONTRACT.md`) has no resolvable value.
- Delegating all Teams-domain work to `incident_manager` (§7) — `team_manager`
  never reasons about chat discovery, message content, or member lists itself;
  it only reasons about *when* to ask `incident_manager` and *what* to do with
  the answer.
- **Owning the conversational approval flow.** `team_manager` is the agent
  that presents an `ActionProposal` to the user and collects the user's
  approve/cancel decision in the conversation. This is conversational
  ownership only — the actual enforcement of that approval before a write
  executes is a deterministic backend control, not something `team_manager`'s
  prompt-following is trusted for. See §11.
- Presenting the final response to the user — synthesizing `incident_manager`'s
  structured output (§8) into the actual `Message` text, citations-equivalent
  provenance (§10), and any `ActionProposal` the turn produced.
- Owning conversational state (§9) — the durable, per-chat record of what has
  been asked, resolved, and proposed so far.

### Must NOT do

- Must not call any `teams_*` tool directly, under any circumstance, even if
  the required parameters are already known. Every Teams-domain read or write
  goes through `incident_manager`. This keeps exactly one caller of the Teams
  tool contract, matching the "exactly one write path per entity" principle in
  `05_STAGE0_ARCHITECTURE_DECISIONS.md` §6.
- Must not fabricate, paraphrase-as-fact, or "fill in" Teams content
  (messages, member names, chat titles) that `incident_manager` did not
  actually return. See §10.
- Must not treat its own conversational approval flow as sufficient
  authorization for a write to execute. Prompt instructions alone are not a
  sufficient approval control (§11) — `team_manager` recording an approval is
  necessary but not sufficient; the deterministic backend re-check is what
  actually gates execution.
- Must not expose `incident_manager`'s internal reasoning, raw tool
  arguments/results, or any Power Automate implementation detail to the user
  — this is the multi-agent-specific instance of Document 02 §7's "no private
  chain-of-thought" invariant, extended to inter-agent traffic.

---

## 7. incident_manager

### Responsible for

- **Teams-domain reasoning** — the only agent that understands how to turn a
  `team_manager` request into one or more Teams tool calls, and how to turn
  tool results back into a structured, factual answer.
- **Discovering and identifying chats** — resolving "which Teams chat(s)" a
  request refers to via `teams_list_chats`, when `team_manager` hands it an
  ambiguous or name-based reference instead of a resolved chat id.
- **Retrieving messages and members** — via `teams_get_messages` and
  `teams_get_members`.
- **Summarizing Teams discussions, answering questions grounded in Teams
  content, and extracting decisions/actions** — performed only over messages
  actually returned by `teams_get_messages` for the request at hand. Never
  over assumed or remembered content from a prior, unrelated call.
- **Preparing Teams write operations** — building the exact payload for
  `teams_create_chat` / `teams_send_message` and returning it to
  `team_manager` as a proposal-ready structure (§8), but never invoking the
  write tool itself until told to.
- **Executing approved Teams tools only** — invoking `teams_create_chat` /
  `teams_send_message` only after `team_manager` signals that the user has
  approved that exact payload, and only after the deterministic backend
  approval check (§11) passes. `incident_manager` cannot make a write tool
  execute by itself — the tool implementation enforces this independently of
  what `incident_manager` "believes" was approved.

### Must NOT do

- Must not communicate with the user directly, in any form. Every output from
  `incident_manager` is an internal, structured response to `team_manager`
  (§8) — never a `Message` the user sees.
- Must not decide whether a write action is approved. `incident_manager` can
  refuse to *prepare* an action it judges malformed or unsupported, but it
  never treats an approval as already granted, and it never executes a write
  tool without an explicit execute instruction from `team_manager` carrying
  the approved payload reference (§11) — and even then, execution is subject
  to the deterministic backend check, not `incident_manager`'s own judgment.
- Must not invent Teams content. If `teams_get_messages` returns nothing
  relevant, `incident_manager` reports that plainly (structured "no result"
  response) rather than answering from general knowledge — this is the
  Teams-domain instance of the strict-grounding invariant already locked for
  Knowledge in Document 01 §4 / Document 04 (Invariant Register item 9),
  applied here to connector-sourced fact rather than document-sourced fact.
- Must not call Microsoft Graph, or any Microsoft 365 API, directly. Every
  Teams operation goes through the Power Automate execution gateway via the
  tool contract in `docs/TEAMS_TOOL_CONTRACT.md`. `incident_manager` never
  holds, sees, or logs a Power Automate URL or secret (§13).
- Must not retain state across turns beyond what `team_manager` explicitly
  hands it on each delegation (§8, §9). `incident_manager` is functionally
  stateless request-in/response-out from the orchestration's point of view;
  conversational state belongs to `team_manager`.
- Must not stand up a new agent for any sub-capability listed in §5 as a tool
  or as its own reasoning.

---

## 8. Delegation protocol

`team_manager` → `incident_manager` is a single, structured, in-process
`AgentTool` call per delegation (§4) — not a new user-facing `Message`, not a
new `Chat` turn, and not a network request. Multiple delegations may occur
within one user turn (e.g. discover, then retrieve, then propose a write) —
each is a separate call.

### Request (`team_manager` → `incident_manager`)

```text
IncidentManagerRequest {
  intent: "discover_chats" | "get_messages" | "get_members"
        | "summarize" | "answer_question" | "extract_actions"
        | "prepare_create_chat" | "prepare_send_message"
        | "execute_write"
  chatReference?: { chatId?: string; nameHint?: string }
  question?: string              // present for answer_question
  timeRange?: string              // optional scoping hint, e.g. "last 24h"
  writeDraft?: {                  // present for prepare_create_chat / prepare_send_message
    actionType: "create_chat" | "send_message"
    fields: Record<string, string>   // e.g. { chatName, participants } or { chatId, body }
  }
  approvedPayloadReference?: string  // present ONLY for execute_write — see §11
  requestingUserId: string        // pass-through for authorization re-checks, never for content
}
```

### Response (`incident_manager` → `team_manager`)

```text
IncidentManagerResponse {
  outcome: "ok" | "no_result" | "ambiguous" | "needs_input" | "unavailable" | "error"
  // "ok": data or a prepared/executed action is present, below.
  // "no_result": tool(s) ran successfully but returned nothing relevant — never
  //   treated as an error, matches the strict-grounding "insufficient" pattern.
  // "ambiguous": e.g. discover_chats matched multiple chats — team_manager must
  //   ask the user to disambiguate, it must not guess.
  // "needs_input": incident_manager cannot proceed without a specific missing
  //   field (name, and which field) — team_manager relays this as its one
  //   clarifying question (§6).
  // "unavailable": Teams connector is not currently usable — see §14.
  // "error": tool call failed at the gateway — carries a SafeError (Document 02 §23).

  missingField?: string           // present for "needs_input"
  candidates?: { chatId: string; title: string }[]  // present for "ambiguous"

  data?: {
    chats?: { chatId: string; title: string; participantCount: number }[]
    messages?: { id: string; author: string; text: string; sentAt: string }[]
    members?: { id: string; displayName: string }[]
    summaryText?: string          // only ever derived from `messages` in this same response chain
  }

  proposal?: {                    // present for prepare_create_chat / prepare_send_message
    actionType: "create_chat" | "send_message"
    displayFields: { label: string; value: string }[]  // exact user-facing payload — Document 02 §15
    payloadReference: string       // content-addressed digest, per 05_STAGE0 §12
  }

  executionResult?: {             // present only for execute_write, outcome "ok"
    status: "completed"
    externalRef?: string          // e.g. the Teams message id created — reference only, never a new primary key (05_STAGE0 §1)
  }

  safeError?: SafeError            // present for outcome "error"; never a raw provider error
}
```

**Invariant:** every fact in `data` (chats, messages, members) traces to a
specific tool call `incident_manager` actually made in servicing this
request. `team_manager` may safely treat everything in `data` as ground truth
for the current turn precisely because `incident_manager` is contractually
forbidden from inventing it (§7). This is the mechanism, not just the policy,
behind "the agents must not invent Teams content."

---

## 9. Conversational state (ADK session/state)

**Decision:** conversational state for a Teams-related conversation uses
**ADK's session/state primitives as the primary runtime mechanism.** This
document does not design a competing custom agent-memory system, store, or
protocol — state that ADK's session/state model can hold is held there, full
stop.

The conceptual state needed, independent of ADK's concrete API surface:

| State element | Meaning | Owner |
|---|---|---|
| `activeIntent` | The intent currently being pursued (mirrors `IncidentManagerRequest.intent`) | `team_manager` |
| `selectedChat` | The resolved `{ chatId, title }` the conversation is currently working with, once disambiguated | `team_manager` |
| `unresolvedChatCandidates` | Candidate chats awaiting user disambiguation (from an `"ambiguous"` outcome, §8) | `team_manager` |
| `collectedChatTitle` | A draft chat name/title collected so far for an in-progress `prepare_create_chat` | `team_manager` |
| `requestedParticipants` | Participant references as the user stated them (names/emails), before resolution | `team_manager` |
| `resolvedParticipants` | Participants as actually resolved — resolution itself happens inside the `teams_create_chat` tool call via Power Automate (§5), not as a separate state-mutating step; this field records the resolved result once known | `team_manager` |
| `missingFields` | Fields still required before a request or proposal can proceed (drives the one-clarifying-question rule, §6) | `team_manager` |
| `pendingActionProposal` | The current unapproved `{ actionType, displayFields, payloadReference }` awaiting user decision | `team_manager` |
| `approvalStatus` | `pending_confirmation \| approved \| processing \| completed \| cancelled \| failed` — mirrors `ActionProposalStatus` (Document 02 §15) | `team_manager` (conversational reflection) / backend approval record (§11, authoritative) |

**Ownership rule:** `team_manager` is the sole owner/writer of this state,
consistent with §6 ("owns conversational state") and §7 ("`incident_manager`
must not retain state across turns"). `incident_manager` only ever sees the
slice of state relevant to a given delegation, passed explicitly via the
`IncidentManagerRequest` fields (§8) — it does not read or write session
state directly. `approvalStatus` as reflected in session state is a
convenience read for the conversation; the **authoritative** approval status
is the backend `ActionApprovalRecord` / policy-gate check (§11), and the two
must never be allowed to diverge in a way that lets stale session state
authorize an execution the backend record does not confirm.

---

## 10. Grounding rule (Teams-domain)

Every user-visible claim about Teams content (a message, a member, a chat's
existence, a summary, an extracted decision or action item) that
`team_manager` presents must be traceable to an `incident_manager` response's
`data` field from the current turn's delegation chain. `team_manager` must
never supplement an `incident_manager` "no_result" outcome with a guess, and
must never carry `data` forward from an earlier, unrelated delegation and
present it as current. This is the same non-negotiable pattern already locked
for Knowledge grounding (`01_IMPLEMENTATION_HANDOFF.md` §4) — applied here to
a connector instead of a document store, per the instruction that "the agents
must not invent Teams content."

---

## 11. Explicit approval for write actions (enforced outside the LLM)

**Approval enforcement must exist outside the LLM.** `team_manager` owning the
conversational approval flow (§6) is necessary but not sufficient — prompt
instructions alone are not a sufficient approval control. `teams_create_chat`
and `teams_send_message` must not execute unless a corresponding pending
`ActionProposal`/approval record has been explicitly approved **and** its
payload still matches the approved payload, and this check is performed by
**deterministic backend code**, independent of what any agent's reasoning
concluded.

Sequence for `teams_create_chat` / `teams_send_message` (mirrors Document 03
§16, with the two named agents filling the previously-generic "Agent Runtime"
role, and with the enforcement point made explicit):

```text
1. team_manager determines a write is needed, delegates
   intent: "prepare_create_chat" | "prepare_send_message" to incident_manager,
   with the drafted fields.
2. incident_manager validates the draft against the tool's schema
   (docs/TEAMS_TOOL_CONTRACT.md) and returns a `proposal` — NOT executed.
   incident_manager does not call teams_create_chat / teams_send_message here.
3. team_manager renders the proposal to the user as the exact payload
   (ActionProposalCard equivalent — Document 02 §15), using proposal.displayFields
   verbatim. team_manager must not paraphrase or summarize the payload in the
   confirmation UI text; the fields shown must be the literal fields approved.
4. User approves. team_manager (via the Application/API layer,
   Document 03 §7) records the ActionApprovalRecord and confirms the payload
   has not changed since it was shown (payloadReference match).
5. team_manager delegates intent: "execute_write" to incident_manager,
   passing approvedPayloadReference = the exact reference from step 2.
6. incident_manager invokes the teams_create_chat / teams_send_message tool
   function with that reference.
7. THE TOOL IMPLEMENTATION ITSELF — not incident_manager's reasoning, not
   team_manager's prompt — calls approval/policy_gate.py before touching
   gateway/power_automate_client.py:
     - looks up the ActionProposal/ActionApprovalRecord for the reference
     - confirms status is "approved" (not pending, cancelled, expired, or
       already executed)
     - re-derives the payload digest of the exact call about to be made and
       confirms it matches the approved digest (05_STAGE0 §12)
     - only on a full pass does the tool proceed to
       gateway/power_automate_client.py; any failure raises an `action_failure`
       SafeError and the call to Power Automate is never made.
8. incident_manager returns executionResult (or the safeError from step 7);
   team_manager presents the completed/failed outcome to the user.
```

**Hard rule, restated for this pair of agents specifically:** step 7 is a
deterministic, non-bypassable code path inside the tool implementation
(`tools/teams/create_chat.py`, `tools/teams/send_message.py`, via
`approval/policy_gate.py`). No agent — `team_manager`, `incident_manager`, or
any future agent — can cause a write to execute by "believing" it was
approved. There is no "just do it" path; the only way `teams_create_chat` /
`teams_send_message` actually reaches Power Automate is through this gate.
Tool calls must never be executed based only on inferred approval (per
instruction) — this is what makes that concrete rather than aspirational.

Read operations (`discover_chats`, `get_messages`, `get_members`,
`summarize`, `answer_question`, `extract_actions`) require no confirmation
and may be delegated and executed by `incident_manager` immediately, subject
only to the standard authorization re-check (Document 03 §11) — matching the
existing read/write split in `01_IMPLEMENTATION_HANDOFF.md` §5.

---

## 12. Presenting the final response

`team_manager` is the only agent that writes to `Message.text` (Document 02
§6). When a turn involved `incident_manager`, `team_manager`'s response must:

- Be built only from `incident_manager`'s returned `data` / `summaryText` /
  `executionResult` for that turn (§10).
- Never surface `incident_manager`'s internal `IncidentManagerRequest`/
  `Response` structure, tool names, or Power Automate details — those are
  internal orchestration plumbing, not different in kind from the "internal
  system/tool messages" `02_INTERFACE_CONTRACTS.md` §6 already says must not
  leak into the user-facing `role` union.
- Use the existing `ActionProposalId` / `connectorUnavailable` fields on
  `Message` (Document 02 §6) exactly as already specified — a Teams write
  proposal is still just an `ActionProposal` from the frontend's point of
  view; the two-agent split behind it is invisible to the Experience Layer,
  consistent with Document 03 §2's Experience Layer boundary (the UI is not
  changed by this document, per instruction).

---

## 13. Secrets and execution boundary

- **Power Automate is the permanent Microsoft 365 execution gateway.**
  Neither `team_manager` nor `incident_manager` calls Microsoft Graph or any
  other Microsoft 365 API directly, now or later in this architecture — every
  Teams operation is a call to a Power Automate flow via the tool contract
  (`docs/TEAMS_TOOL_CONTRACT.md`), routed through the single shared
  `gateway/power_automate_client.py` abstraction (§16). No direct Microsoft
  Graph implementation exists anywhere in this stack.
- **Gemini/ADK performs reasoning and orchestration only.** Model calls (both
  agents) never receive a Power Automate URL, key, or any other connector
  credential as part of their context/prompt, agent instructions, or tool
  descriptions. The gateway URL/SAS is resolved from Secret Manager/config by
  deterministic backend code (`gateway/power_automate_client.py`,
  `config/settings.py`) at call time — matching the existing "Agent Runtime
  never holds credentials" rule (`03_INTEGRATION_BOUNDARIES.md` §6,
  `05_STAGE0_ARCHITECTURE_DECISIONS.md` §11, Security invariant #1/#3).
- **Power Automate URLs/secrets must never be exposed** to the frontend, tool
  results, agent-to-agent messages (§8), logs visible outside the backend, or
  any response that could reach the Application/API layer's frontend-facing
  surface. This is stricter than, but consistent with, the existing rule that
  the browser never talks to the Agent Runtime, Knowledge Service, or
  Connector Gateway directly (`03_INTEGRATION_BOUNDARIES.md` §3) — it applies
  transitively to every layer beneath the Application/API layer, including
  the Teams tool layer specifically.

---

## 14. Connector availability

Before delegating any Teams-domain intent, `team_manager` must resolve Teams
connector availability using the existing two-dimensional model (Document 02
§13: `connector-teams` global `state === "connected"`, and, if in a Project,
`connector-teams` ∈ `Project.enabledConnectorIds`). If unavailable,
`team_manager` does not delegate to `incident_manager` at all — it responds
using the existing `ConnectorUnavailableInfo` pattern (Document 02 §6),
reason-routed exactly as already specified (`global_unavailable` vs.
`project_disabled`, Document 01 §5). `incident_manager`'s own
`outcome: "unavailable"` response (§8) exists only to cover the edge case
where availability changes between `team_manager`'s check and
`incident_manager`'s tool call (a live re-check, per
`05_STAGE0_ARCHITECTURE_DECISIONS.md`'s run-snapshot table: availability is
always revalidated at the point of the operation, never trusted from an
earlier snapshot).

---

## 15. Non-goals of this pass

- No other agents are implemented or contractually defined here (e.g. no
  ticketing specialist, no SharePoint/knowledge specialist).
- No runtime code, no ADK agent classes, no Power Automate flows are built in
  this pass — see §16 and `docs/TEAMS_TOOL_CONTRACT.md` for what a later
  implementation pass would build against.
- No networked/A2A deployment for `team_manager`/`incident_manager` — frozen
  out for v1 per §4.
- No new agent-memory system — conversational state uses ADK session/state
  per §9.
- No change to the existing frozen UI or to any Document 01–05 contract. This
  document is a refinement of "Agent Runtime" (previously described only as a
  single logical component) into two named agents for the Teams domain — it
  does not redefine any existing contract, and any place it references one
  (`ActionProposal`, `SafeError`, connector availability) uses it unchanged.

---

## 16. Proposed backend structure (module boundaries, no code)

```text
backend/
  agents/
    team_manager/
      agent.py
      prompts.py
      schemas.py
    incident_manager/
      agent.py
      prompts.py
      schemas.py

  tools/
    teams/
      list_chats.py
      get_messages.py
      get_members.py
      create_chat.py         # calls approval/policy_gate.py before gateway/power_automate_client.py
      send_message.py        # calls approval/policy_gate.py before gateway/power_automate_client.py
      schemas.py

  gateway/
    power_automate_client.py  # the ONLY Power Automate HTTP client abstraction in the system
    safe_error.py

  approval/
    policy_gate.py            # deterministic approval/payload-digest enforcement — §11
    schemas.py

  api/

  config/
    settings.py                # Secret Manager / env resolution — never imported by agents/ directly in a way that surfaces raw values into model context

  tests/
```

**Single Power Automate client rule:** `gateway/power_automate_client.py` is
the only Power Automate HTTP client abstraction in the system. There is no
`tools/teams/client.py` or any other per-tool HTTP client — every file under
`tools/teams/` is a thin, typed wrapper that (for write tools) calls
`approval/policy_gate.py` first, and then calls the single
`gateway/power_automate_client.py` for the actual Power Automate request.

This structure is a **module-boundary proposal only** — no files under
`backend/` are created in this pass (§15).

---

## 17. Open questions

- **Disambiguation UX.** `outcome: "ambiguous"` (§8) implies `team_manager`
  must ask the user to pick among `candidates` — the exact phrasing/format of
  that clarifying question is a prompt-design detail, not fixed here.
- **Multi-step delegation within one turn.** This document allows multiple
  `IncidentManagerRequest`/`Response` exchanges within a single user turn (§8)
  but does not cap how many; a future pass may want a bounded step limit to
  prevent runaway delegation loops — flagged, not decided.
- **`pending_confirmation` expiry duration** for a Teams `ActionProposal` —
  already flagged as an open parameter at the product level
  (`05_STAGE0_ARCHITECTURE_DECISIONS.md` §12); not re-decided here.

The previous open question about delegation transport (in-process vs.
networked) is **resolved** by §4: in-process `AgentTool`-based delegation
within a single ADK runtime, frozen for v1.
