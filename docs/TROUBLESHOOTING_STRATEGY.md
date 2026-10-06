# SLOPANOC Troubleshooting Strategy

## Status

**NON-NEGOTIABLE PRODUCT PRINCIPLE**

This document defines the core troubleshooting experience SLOPANOC must ultimately deliver.

All architecture, product, agent, knowledge-management, context-engineering, integration, security, and UX decisions must be evaluated against this strategy.

If a future implementation makes this troubleshooting experience harder, less grounded, less iterative, or less useful to an engineer, the implementation should be reconsidered.

---

# 1. Core Product Principle

SLOPANOC must not behave like a traditional chatbot that responds to an operational problem with a long generic troubleshooting checklist.

The core experience must be:

> **Iterative, evidence-driven, conversational troubleshooting — one useful diagnostic step at a time — until the fault is resolved, sufficiently narrowed, or clearly escalated.**

The interaction should feel like working with an expert engineer who:

1. understands the current incident context,
2. determines what information is still missing,
3. tells the engineer exactly what to check next,
4. explains how to check it,
5. asks for the resulting evidence,
6. interprets that evidence,
7. updates the fault hypothesis,
8. retrieves additional context when required,
9. determines the next-best diagnostic step,
10. repeats until resolution, escalation, or dispatch.

The central troubleshooting question is:

> **What should I check next, and why?**

---

# 2. Primary Troubleshooting Experience

```text
User identifies the bridge / incident
        ↓
SLOPANOC retrieves current bridge context
        ↓
User describes the problem
        ↓
SLOPANOC understands the current fault state
        ↓
Context discovery
  Teams + Case + KM + later ITSM / alarms / KPIs / topology
        ↓
SLOPANOC determines the BEST NEXT CHECK
        ↓
"Run this command:"
        ↓
User runs it
        ↓
User pastes the output
        ↓
SLOPANOC interprets the new evidence
        ↓
Context is updated
        ↓
Additional context is retrieved if required
        ↓
Hypotheses are narrowed
        ↓
NEXT CHECK
        ↓
repeat
        ↓
RESOLUTION
or
clear escalation / dispatch / next owner
```

This interaction pattern is fundamental.

SLOPANOC should generally avoid overwhelming the engineer with 15–25 speculative steps at once when one discriminating diagnostic step can reduce uncertainty first.

---

# 3. Example Experience

The user might say:

> Get the context from the Vodafone bridge. Site ABC went down after tonight's upgrade. What should I check?

SLOPANOC should first establish relevant context.

```text
Bridge:
- last 30 minutes of discussion
- actions already attempted
- current symptoms

Case:
- affected site
- start time
- software version
- current state

Knowledge:
- applicable Approved upgrade MOP
- current validation procedure
- rollback SOP
- related RCA
```

SLOPANOC should then determine the most useful next diagnostic action instead of producing a generic checklist.

Example response:

> The outage started immediately after the software upgrade, and no successful post-upgrade connectivity validation is recorded in the bridge.
>
> **First check:** verify the process status on the affected node.
>
> Run:
>
> `show process-status ...`
>
> Paste the output here.

The engineer provides the result.

SLOPANOC then:

```text
interprets result
        ↓
updates current fault understanding
        ↓
eliminates or strengthens hypotheses
        ↓
retrieves additional context if needed
        ↓
selects the next-best check
        ↓
asks for the next specific piece of evidence
```

This loop continues until the problem is resolved or a clear escalation path is identified.

---

# 4. Continuous Context Re-Evaluation

Knowledge and operational context must not be retrieved only once at the beginning of a troubleshooting session.

Every new piece of evidence can change what context is relevant.

For example:

```text
Initial problem:
Site down after upgrade
        ↓
retrieve upgrade MOP + rollback SOP

Command output:
process is running
        ↓
process-failure hypothesis decreases
        ↓
retrieve connectivity-validation section

Next output:
interface down
        ↓
retrieve interface troubleshooting SOP
        ↓
possibly retrieve matching RCA

Next output:
specific error code
        ↓
retrieve knowledge relevant to that error
```

The troubleshooting loop is therefore:

```text
Evidenceₙ
   ↓
Update fault understanding
   ↓
Update hypotheses
   ↓
Determine missing information
   ↓
Search additional context if needed
   ↓
Determine next-best diagnostic action
   ↓
Ask user for evidence
   ↓
Evidenceₙ₊₁
```

**Continuous context discovery is mandatory.**

---

# 5. Troubleshooting State

SLOPANOC must not treat each user message as an isolated question.

The future troubleshooting capability should maintain a persistent troubleshooting state conceptually containing:

```text
Troubleshooting State
──────────────────────────
Problem
Symptoms
Known facts
Unknowns
Hypotheses
Hypotheses eliminated
Actions already performed
Evidence collected
Relevant knowledge
Current MOP / SOP
Current diagnostic step
Expected result
Actual result
Next recommended action
Resolution state
```

Example:

```text
Hypothesis 1: software process failure
Status: ELIMINATED

Hypothesis 2: post-upgrade interface issue
Status: ACTIVE

Hypothesis 3: configuration mismatch
Status: POSSIBLE
```

The system should progressively narrow the fault rather than repeatedly generating generic advice.

---

# 6. Next-Best-Diagnostic-Action Principle

The most important output during troubleshooting is not necessarily a final root cause.

It is often:

> **What should I check next, and why?**

The agent should generally be able to communicate:

```text
WHAT I THINK
Current interpretation of the fault

WHY
Evidence supporting that interpretation

NEXT CHECK
The most useful next diagnostic step

HOW TO CHECK
Exact UI path / query / command / procedure

WHAT I NEED BACK
Specific output/evidence the engineer should provide

SOURCE
MOP / SOP / RCA / bridge evidence supporting the recommendation
```

These do not need to appear as rigid headings in every response.

The conversation should remain natural and concise.

---

# 7. Commands Must Be Grounded

If SLOPANOC tells an engineer to execute a command, there must be a defensible operational basis for it.

Preferred path:

```text
Approved MOP / SOP
        ↓
applicable procedure section
        ↓
command / diagnostic action
        ↓
Agent adapts explanation to current problem
```

Avoid:

```text
Model recalls a plausible command from general knowledge
        ↓
Model instructs engineer to execute it
```

The model may reason about an operational instruction and explain it, but operational commands should preferably be grounded in approved, current, applicable knowledge.

Where no approved knowledge supports a specific command, SLOPANOC should make that limitation visible rather than fabricate authority.

---

# 8. Read / Check First, Change Later

Troubleshooting should prefer diagnostic and observational actions first.

Examples:

```text
show
get
query
inspect
verify
compare
read
```

State-changing actions require stronger control.

Examples:

```text
restart
reset
reconfigure
rollback
delete
change
redeploy
```

The intended evolution is:

```text
"Run this diagnostic command."
        ↓
user provides result
        ↓
"Based on the result, rollback is indicated."
        ↓
"Would you like me to prepare the rollback action?"
        ↓
ApprovalCard
        ↓
trusted deterministic approval
        ↓
approved action
```

The existing approval architecture is therefore a foundational part of future troubleshooting.

---

# 9. Context Engineering Target

The troubleshooting experience maps to the target architecture as follows:

```text
                    User
                     │
                     ▼
                Team Manager
                     │
                     ▼
          Troubleshooting Manager
                     │
                     ▼
          Context Engineering Layer
                     │
       ┌─────────────┼─────────────┐
       ▼             ▼             ▼
 Operational     Knowledge       Case
   Context        Context        Context
       │             │             │
 Teams/...       Generic KM       Cases
                     │
              MOP / SOP / RCA / KB

                     ↓
            Troubleshooting State
                     ↓
            Next-Best-Action Loop
                     ↓
              Ask user to check
                     ↓
               User evidence
                     └──────────────↺
```

Current implementation status must remain clearly separated from future architecture.
See `docs/BUILD_SEQUENCE.md` for the full, detailed phase-by-phase build
sequence and topology evolution behind this summary.

**Locked execution order (replaces the previous A5 → Phase 4H → 5.2–5.7 →
Phase 6 order — see `docs/BUILD_SEQUENCE.md` §2a for the full
rationale):** CURRENT → 5.X (COMPLETE / FROZEN) → Phase 6A (← NEXT) →
Phase 4H → 5.2–5.7 → Phase 6B → Phase 7 (product target).

### Current

```text
Team Manager
Incident Manager
Teams text
Cases
Generic KM / RAG (Phase 5.1, complete)
A5 — real TELCO/RAN Knowledge Island capability (complete, including real
  live-runtime validation)
Current-turn SLOPANOC image multimodality, combined with Teams and/or
  Knowledge Context in one specialist turn (POST-5.1 B, B0-B7, complete)
5.X — Teams Rich Content / Media Retrieval (complete, frozen, canonical
  P10 -- Teams-originated rich visual evidence (images), retrieved with
  full provenance binding and delivered deterministically into the same
  multimodal path as the direct-upload capability above; see
  `docs/MASTER_ROADMAP.md`)
```

### Next

```text
Phase 6A — Intelligence Architecture Foundation (<- NEXT, NOT STARTED,
  after 5.X)
  Bounded Context Engineering foundation, Troubleshooting Manager,
  Skills framework, Experience Memory foundation -- built against the
  context sources that exist now that A5 and 5.X are both complete, not
  the full future Operational Context surface.
```

### Then

```text
Phase 4H — Security Hardening (FUTURE, after Phase 6A)
  Not cancelled -- rescheduled to evaluate the richer, more stable
  architecture Phase 6A produces.
```

### Later

```text
5.2-5.7 — Operational Context integrations (ITSM, Alarms, Topology, KPIs,
  Change, Handover) (FUTURE, after Phase 4H)
Phase 6B — Context Engineering Expansion (FUTURE, after 5.2-5.7)
  Expands the SAME Phase 6A foundation against the complete Operational
  Context surface.
```

### Product target

```text
Phase 7 — persistent Troubleshooting State + next-best-diagnostic-action
  loop (the product target this document defines), consuming the
  architecture Phase 6A establishes and Phase 6B expands
Head of Automated Operations (optional future supervisory layer, never a
  mandatory hop -- not automatically part of Phase 6A)
```

---

# 10. Operational Context Evolution

Operational Context will expand over time.

```text
Operational Context
├── Teams
├── ITSM
├── Alarms
├── Topology
├── KPIs
├── Logs
├── Change
└── Handover
```

The user should initially provide some evidence manually where no direct integration exists.

Example:

```text
EARLY STAGE

"Run this command and paste the result."
```

As integrations mature:

```text
LATER

"I checked the current KPI and alarm state.
Interface X has been down since 02:14.
The next useful check is..."
```

The product should reduce manual evidence collection over time without changing the conversational troubleshooting model.

---

# 11. Context Selection Principle

SLOPANOC must **not simply dump all available documents or context into Gemini**.

The intended reasoning pipeline is:

```text
Problem
  ↓
Context Discovery
  ↓
Applicability Filtering
  ↓
Authority / Lifecycle / Version Validation
  ↓
Ranking
  ↓
Context Assembly
  ↓
Agent Reasoning
  ↓
Grounded Response
```

Across domains:

```text
                         PROBLEM
                            │
                            ▼
                  Context Discovery
                            │
          ┌─────────────────┼─────────────────┐
          ▼                 ▼                 ▼
   Operational Context  Knowledge Context   Case Context
          │                 │                 │
        Teams          Generic KM Layer      Cases
                            │
                   MOP / SOP / RCA / KB
          │                 │                 │
          └─────────────────┼─────────────────┘
                            ▼
                 Applicability Filtering
                            ↓
                    Authority / Version
                            ↓
                         Ranking
                            ↓
                    Context Assembly
                            ↓
                     Agent Reasoning
                            ↓
                    Grounded Response
```

The agent should receive only the most relevant, valid, authoritative, and bounded context required for the current diagnostic step.

---

# 12. Generic Knowledge Management Role

Phase 5.1 is critical because it provides the future troubleshooting loop with a trustworthy answer to:

> **What approved operational knowledge should influence my next diagnostic step?**

Phase 5.1 provides:

```text
Generic KM
    ↓
Approved + applicable + current knowledge
    ↓
relevant procedural sections
    ↓
trusted provenance
```

Then the future troubleshooting architecture combines:

```text
Troubleshooting Manager
+
Context Engineering
+
Troubleshooting State
+
Generic KM
        ↓
"What should I check next?"
```

Phase 5.1 is **not** the troubleshooting loop itself.

It builds the governed Knowledge Context required by the future loop.

---

# 12a. Skills — Behavioral Orchestration Layer (Future)

A **Skill** is a FUTURE, not-yet-built reusable unit of behavior — "how
should this kind of work be performed?" — distinct from an agent, a
document, a tool, and memory (full definition: `docs/AGENT_CONTRACT.md`
§3a; Knowledge-vs-Skill distinction: `docs/KNOWLEDGE_CONTRACT.md` §22.5).
It is documented here only to state its strategic relationship to this
document's existing troubleshooting loop — it does not change that loop.

A Skill does not contain or duplicate the knowledge/context it needs. It
**orchestrates retrieval** of what already exists elsewhere, then hands
the result to the SAME reasoning/next-best-action loop §2–§8 already
define:

```text
Skill (e.g. "Troubleshoot VSWR") — FUTURE
        ↓ retrieves, does not own
   ┌────┴──────────┬──────────────┬─────────────────┐
   ▼                ▼              ▼                 ▼
Knowledge/RAG    Experience     Case Context      live Tools
(§12, CURRENT)   Memory         (CURRENT,          (Teams CURRENT;
 applicable       (FUTURE)      backend/cases/)    ITSM/alarms/KPIs/
 MOP/SOP                        current fault      topology FUTURE)
   │                │              │                 │
   └────────────────┴──────────────┴─────────────────┘
                        ↓
        the UNCHANGED loop this document already defines
                 (§2 Primary Troubleshooting Experience,
                  §4 Continuous Context Re-Evaluation,
                  §6 Next-Best-Diagnostic-Action)
```

A future Troubleshooting Manager (§16's Phase 6A/7 alignment) selecting
and executing Skills is expected to give behavioral specialization
without one agent per fault type — "Troubleshoot VSWR," "Troubleshoot
Cell Down," and similar are examples of Skills, never of new agents (see
`docs/AGENT_CONTRACT.md` §12's "avoid agent proliferation" principle).
None of this is implemented today; §2–§11 above remain the authoritative
description of the current and near-term troubleshooting experience.

---

# 13. Generic KM Architectural Invariants

The Generic Knowledge Management Layer must remain:

- independent of Incident Manager,
- independent of Troubleshooting Manager,
- usable without Gemini/ADK for its core domain behavior,
- generic across knowledge types,
- lifecycle-aware,
- version-aware,
- applicability-aware,
- provenance-aware,
- independently testable,
- reusable by any future specialist.

MOP is a document type, not an architecture.

The KM layer must support:

```text
MOP
SOP
RCA
KB Article
Troubleshooting Guide
Operational Procedure
Technical Instruction
other governed operational knowledge
```

The key invariant is:

> **Can the Knowledge layer still work without knowing Incident Manager exists?**

If the answer is no, the architecture is too coupled.

---

# 13A. AI vs Server Responsibility Boundary

**Architectural contract for every agent and developer.**

```text
AI optimizes.
Server constrains only when a hard invariant would be violated.
```

AI agents optimize for the operational goal within their assigned scope. The deterministic server
does not replace agent technical reasoning.

```text
AI:     "What is the best thing to do next?"
SERVER: "Does doing that violate a hard invariant?"
```

If no hard invariant is violated, the server lets the agent's proposed direction continue through
the normal authority controls. The server never asks "would I have selected this diagnostic?", "is
this my preferred troubleshooting sequence?" or "is another check technically better?" — those are
agent responsibilities.

**AI agents own** (each within its domain — Technical Authority Engineer: troubleshooting, fault
isolation and recovery reasoning; Incident Manager: incident handling and information quality;
Problem Manager: recurring-problem / RCA reasoning; Automated Operations Engineer: safe automation
opportunities; knowledge tools: retrieval and evidence relevance):

- technical interpretation
- hypothesis generation
- next-best-action reasoning
- diagnostic prioritization
- alternative path selection
- technical remediation proposals
- technical escalation proposals

**The server guarantees:**

- trusted evidence (only VALIDATED observations progress a step or bind parameters)
- progression consistency (no accidental loss of a pending step, no loop on a known result)
- governed command authority (SELECTED evidence → ProcedureAction → deterministic resolution →
  single-invocation validation → Command Authority)
- mandatory dependency enforcement (explicit governed MANDATORY prerequisites only)
- policy / approval enforcement (target confirmation, policy, HITL, escalation policy)
- validated observations
- verified resolution (action executed ≠ incident resolved)
- durable / auditable state (the Case-owned TroubleshootingProgression)

**Ordering is guidance, not law:**

```text
document order != mandatory dependency
recommended procedure sequence != server-enforced sequence
```

unless the governed procedure explicitly declares a MANDATORY relationship. A procedure listing
steps A, B, C does not make the server enforce A → B → C: after A, the agent may choose C when live
evidence makes it the most useful check. The server blocks C only if governed text explicitly makes
B a mandatory prerequisite of C, or another hard trust / consistency / safety invariant fails.

Every server-side rejection is one of TRUST, CONSISTENCY or SAFETY:

| Rejection | Class |
|---|---|
| `rejected_multiple_actions` (one action per step) | SAFETY |
| `rejected_pending_awaits_result` (continuity until a validated result or explicit switch) | CONSISTENCY |
| `rejected_repeated_step` / `rejected_known_result` / `rejected_failed_unchanged` | CONSISTENCY |
| `rejected_mandatory_prerequisite` (explicit governed MANDATORY prerequisite incomplete) | SAFETY |
| `rejected_premature_remediation` (no trusted diagnostic evidence, target not isolated, no governed MATCH remediation, mandatory prerequisite incomplete, earlier remediation awaiting verification) | TRUST / SAFETY / CONSISTENCY |
| `rejected_fault_closed` (resolved / escalated until the operator reopens) | CONSISTENCY |
| escalation proposal without recorded evidence or not permitted by policy | TRUST / policy |
| unvalidated result, composed or ungrounded command | TRUST / SAFETY |

Removed as TECHNICAL_STRATEGY (server-owned technical choices): document-position `out-of-order`
rejection, non-mandatory / unknown "prerequisites" blocking remediation, a weakened hypothesis
blocking remediation, "every governed diagnostic must be tried before escalation", and server-side
reinterpretation of hypotheses after a failed verification.

# 14. Troubleshooting UX Guardrails

Any future troubleshooting UX should preserve the following rules.

## 14.1 One useful step at a time

Prefer a discriminating next check over a large generic checklist.

## 14.2 Explain why

The engineer should understand why the next check is useful.

## 14.3 Tell the engineer exactly how

Where possible provide the exact:

- command,
- query,
- UI path,
- procedure,
- evidence request.

## 14.4 Ask for the result

Make clear what evidence should be returned to SLOPANOC.

## 14.5 Interpret before continuing

Do not immediately issue another step without interpreting the evidence just provided.

## 14.6 Re-search context when evidence changes

New evidence may require new knowledge, bridge history, RCA, alarm, KPI, topology, or case retrieval.

## 14.7 Preserve troubleshooting continuity

Do not behave as if every message starts a new investigation.

Implemented as the server-owned **Progression Controller**
(`backend/agents/technical_authority_engineer/progression_controller.py`, over the authoritative
`TroubleshootingProgression` in `backend/cases/troubleshooting_progression.py`). The TAE proposes
progression; the server owns its consistency. Everything below is a hard invariant (trust,
consistency, safety) — never a technical preference; see "AI vs Server Responsibility Boundary"
below for the contract that governs what the server may and may not decide.

- **One step → result → reassess → next step.** Per fault thread, at most one step is pending
  (PROPOSED/VALIDATED/PRESENTED). A new proposal is accepted only when no step is pending.
- **No result = no progression; only a VALIDATED result progresses.** Operator message → candidate
  observation → deterministic result validation (`result_validation.py`) → trusted result → step
  completion (phases RESULT_RECEIVED → RESULT_VALIDATED; step OBSERVED → VERIFIED → COMPLETED, or
  FAILED on an explicit command error). Validation uses only server-known facts: questions are never
  results; the pending command (or rendered ProcedureAction) echoed with its output validates —
  at the start of a line ("$ st ru", "NODE> st ru", "st ru output:") or after a conversational
  lead-in on the same line ("this is the output: $ st ru", "the output of st ru:") when
  output-structured lines follow it; output-structured lines without an echo validate only when
  attributable to the step (its target shown, or ≥ 2 distinctive words of its objective / expected
  evidence / command); output of another known step's command (any fault thread) is FOREIGN. An
  ambiguous or foreign candidate is recorded on the step (audit only, never evidence or a parameter
  source), the step stays pending and its output is requested — stating that the supplied output
  could not be matched to the step. Output that re-submits an already-recorded result (KNOWN_RESULT)
  is neither a candidate nor a second result and reopens nothing. Controlled execution output binds
  only to the exact check whose SUCCEEDED execution is on the control record with a matching output
  hash. A different proposed step is rejected with the pending step presented again (no command
  unless re-authorized in that turn).
- **Result consumed exactly once.** Binding happens before the specialist runs (bind → record →
  transition → leave pending eligibility → specialist on the updated progression). A step holding a
  recorded result never awaits its result again (`awaits_result`; the progression refuses the status
  transition) — a new execution of the same check is a new step. The completeness boundary never
  renders a step whose result the current turn bound as "still awaiting its result" (not from a
  stale pending snapshot, not from a re-presented proposal); it projects the post-binding progression
  (the new validated step, clarification, gap, escalation or resolution) or fails closed.
- **Blocked / skipped.** When the operator says the step cannot be performed, it is recorded SKIPPED
  with the operator's reason (never a result) and an alternate step may be proposed. Only the
  operator's own words count — text inside pasted output ("unable to provide service") never does.
- **Pending-step continuity.** Follow-ups such as "what's next cmd?", "what command?", "how do I
  check that?" set the turn objective to the pending step and pass it to the TAE as
  `pending_step`; a proposal is accepted only as that step's resolution (same ProcedureAction, or
  matching wording when the step has no action yet), and it updates the step in place.
- **Applicability-blocked governed action: identity kept, authority not.** When Command Authority
  refuses a command ONLY because the selected section's applicability is UNKNOWN / PARTIAL_MATCH and
  an applicability clarification is open, the server matches the refused command (read from the
  specialist's raw proposal, since its own integrity callback may already have stripped it from the
  final output) against the
  deterministic ProcedureActions of that exact SELECTED, approved section
  (`procedure_actions.applicability_blocked_action`; diagnostic reads only; fabricated, ambiguous or
  state-changing commands yield nothing). The step is recorded `BLOCKED_BY_CLARIFICATION` with a
  `blocked_candidate` (ProcedureAction id, canonical source, normalized template, blocking reason,
  clarification id) and NO command, command source, catalog entry or control. It is continued — not
  duplicated, not rejected — only by structure: the same ProcedureAction id + canonical source +
  template resolved this turn on MATCH evidence (or, on the legacy command path, the same normalized
  command authorized from the same canonical source with the linked clarification). The command is
  whatever Command Authority authorized in that turn; wording never resolves a blocked step and a
  different action or source never replaces it. A rejected proposal's envelope withdraws its
  command everywhere (rendered/authorized resolution view, interpretation sentences naming it;
  audit stays in the run trace), and the final-answer boundary drops references to "this command"
  when the authoritative step carries none.
- **One operational action per step.** A step whose command chains several actions (`;`, `&&`,
  `||`, `|`, newline) is rejected.
- **Duplicate / loop prevention.** Every candidate gets a server-owned canonical identity
  (`step_identity.py`): `sha256(fault thread, action_key, target)`, where `action_key` is
  `pa:<knowledge_id>:<template>` for a governed ProcedureAction (version-independent), otherwise
  `cmd:<normalized command>` or `obs:<objective tokens>|<expected tokens>`, and `target` is the
  sorted verified parameter bindings. `result_key` (the normalized rendered command) identifies the
  observation. Structured and lexical only; no model similarity judgment is authoritative. Within
  the same fault thread the candidate is rejected when the same step is completed
  (`rejected_repeated_step`), its observation is already known through another source
  (`rejected_known_result`), or it failed or could not be performed (`rejected_failed_unchanged`);
  a duplicate of the pending step is folded into it. A different diagnostic, a different target, an
  alternative branch or a changed state is never a duplicate. A repeat is accepted (`recheck_permitted`, with
  `recheck_of` and `recheck_reason` recorded on the step) only when the operator's explicit re-check
  request resolves to that ONE earlier step (its words uniquely match the step's objective / command /
  template / target; a bare pronoun — "run it again" — means the most recent step; a generic
  "recheck", no match or a tie grants nothing and asks which check), a state-changing step completed
  after the earlier result (governed post-action verification when the action declares a
  POST_ACTION_VERIFICATION relationship to the check, otherwise the earlier result is stale), the operator
  reopened the fault, or the governed source version / applicability changed. A different target or
  a different fault thread is a different step.
- **Explicit cancel / supersede.** A pending step never silently disappears. Only the operator's own
  explicit words ("forget/cancel/drop that check", "don't run that restart", "never mind") move it
  PRESENTED → CANCELLED, or → SUPERSEDED when a new objective is stated ("…, investigate the sync
  alarm instead"), with the reason recorded; a pending remediation becomes NOT_PERFORMED. The target
  is the pending step of the fault that was active before the turn; other fault threads keep theirs.
  An imperative redirect ("investigate the transport fault instead") is also an explicit switch.
  Questions and vague follow-ups ("what next?") never cancel anything; the text is never a result.
  Once the pending step's result is validated (or the operator replaces it), the agent may choose
  ANY technically appropriate next step — the server never forces the document sequence.
- **Diagnosis → remediation gate** (`resolution_gates.py`). A state-changing candidate becomes a
  REMEDIATION_CANDIDATE only when server state establishes: a completed diagnostic observation with
  a trusted result on this fault thread (and no earlier remediation awaiting verification); every
  target parameter bound to a verified value; a state-changing ProcedureAction from SELECTED,
  approved, applicability-MATCH evidence; and every MANDATORY_PREREQUISITE relationship of that
  action completed for the same target. Whether the evidence makes the remediation technically
  appropriate is the agent's judgement, not a gate condition. Otherwise
  `rejected_premature_remediation` with the unmet conditions. The existing controls then still run:
  resolver → Command Authority → target confirmation → policy → HITL.
- **Structured action graph** (`procedure_actions.py`). Relationships are derived deterministically
  when a governed section is extracted — never by re-reading prose at runtime — each with provenance
  (rule, 1-based line, exact line text, source locator), typed as:
  `MANDATORY_PREREQUISITE` (unhedged obligation — "must", "required", "mandatory", "prerequisite",
  "only after", "do not … before" — with an identifiable target: "before any/all …", one later
  state change, or "`A` must … before `B`" / "`B` requires `A`" on one line), `RECOMMENDED_BEFORE`
  (plain temporal or advisory ordering: "before X, check Y", "should", "may", "if required"),
  `POST_ACTION_VERIFICATION` (a read listed after a state change with verification language) and
  `UNKNOWN` (obligation whose target cannot be identified, three or more actions on one dependency
  line, no identifiable direction). ONLY `MANDATORY_PREREQUISITE` is enforced, for any proposed
  action (`rejected_mandatory_prerequisite` / the remediation gate); RECOMMENDED and UNKNOWN are
  advisory context exposed to the agent in the action catalog. `sequence` records document order
  and is never enforced.
- **Action succeeded ≠ incident resolved.** The remediation's result (operator report or controlled
  execution) moves the fault to ACTION_EXECUTED → POST_ACTION_VERIFICATION_REQUIRED; its own output
  never verifies it. Verification criteria are proposed with the remediation and kept only when
  grounded (`must_exclude` observed before the action, `must_include` stated by the governed
  procedure, check = a governed read, optional scope); they freeze at execution. Later checks are
  VERIFICATION steps: all criteria met → RESOLVED; any not met → REASSESS (ESCALATION_REQUIRED once
  the policy's `max_failed_verifications` is reached); otherwise unresolved. Without criteria a fault
  is never resolved automatically. A RESOLVED/ESCALATED fault accepts no new step until the operator
  reports a recurrence or asks to re-check (recorded reopen, if the policy allows it; earlier results
  become stale).
- **Escalation is a server transition; whether to escalate is the agent's call.** A TAE
  `escalation_required` outcome is a proposal. The gate checks hard requirements only — the policy
  permits agent proposals, the fault is unresolved, and recorded evidence exists (a validated
  observation or failed verification on this fault: `evidence_backed_proposal`; or an explicit
  knowledge gap — governed search performed, nothing applicable selected:
  `no_applicable_governed_procedure`) — never whether escalating beats another diagnostic. The
  policy's `max_failed_verifications` also escalates on its own (`failed_verifications_reached`).
  A pending step is SUPERSEDED (audited) by an accepted escalation. Each escalation records rule,
  reason, evidence step/result ids, policy id/version/rule, proposer and timestamp. A rejected
  proposal is returned as `insufficient_evidence` with the gate's reasons; the fault is not escalated.
- **Progression policy** (`backend/cases/progression_policy.py`). One immutable, versioned
  server-owned object replaces magic numbers. System default `slopanoc-system-default` v2:
  `max_failed_verifications=2` (None disables policy escalation), `allow_operator_requested_recheck=true`,
  `allow_reopen_after_resolution=true`, `result_staleness=state_change_or_reopen`,
  `allow_escalation_proposals=true`, `escalation_requires_recorded_evidence=true`. Every
  policy-driven decision records `{policy_id, version, rule}`. No customer-specific policy exists yet.
- **Hypotheses.** SUPPORTED / WEAKENED / REJECTED need a trusted observation bound in the same turn;
  a proposed CONFIRMED is recorded as SUPPORTED. Only met verification criteria of the remediation
  that tested a hypothesis confirm it. Interpreting a failed verification (e.g. WEAKENED) is the
  agent's proposal, with that verification result as evidence; the server never reinterprets.
- **Controlled reads.** Output recorded by the controlled read-execution path is bound to its step
  in the progression immediately (not only on the next specialist turn).
- **Single source of truth, owned by the Case.** The TroubleshootingProgression (fault threads,
  steps, validated results and rejected candidates, hypotheses, remediation records, phase,
  resolution, audit events) is persisted per Case in `slopanoc_case_troubleshooting_progressions`
  (Alembic `7c4e9a2d1f05`) as one versioned aggregate. Every write is a compare-and-swap
  (`read version N → persist N+1 WHERE version = N`); a stale writer gets `ProgressionConflict` and
  nothing is overwritten (the TAE turn fails closed and asks to repeat; the idempotent controlled-read
  binding reloads and re-applies). Case scope is resolved only from the session→case link table
  (the `active_case_id` session hint merely triggers the lookup). A session linked to no Case keeps
  its own session-scope document. `troubleshooting_state` / `troubleshooting_threads` in session
  state are read projections re-derived on every save and never read back; only the progression
  controller (and the control plane through it) mutates troubleshooting state. Older sessions are
  migrated one way: their legacy thread state or pre-link session progression is imported into the
  Case once (new faults only), then the session copy is removed.

The controller controls sequencing, not authority: evidence selection, ProcedureAction resolution,
Command Authority, policy, target confirmation and HITL still run for every accepted step, and a
rejected proposal never reaches the operational control plane.

## 14.7a Clarification Continuity

Operational progression and clarification progression are different: a pending **step** awaits an
observation; a pending **clarification** awaits information the system asked for. Implemented in
`backend/agents/technical_authority_engineer/clarification_continuity.py`.

- **One server-owned record.** A clarification requested by the system is persisted as trusted
  Case/fault state: the fault's OPEN `OpenQuestion` on the Case-owned `TroubleshootingProgression`
  (`reason` applicability / target_identity / missing_parameter / missing_context /
  policy_clarification / other, `requested_fields`, `resolved_values`, `status` open / resolved /
  cancelled / superseded, `originating_step_id`). `TroubleshootingState.pending_clarification` is
  only its read projection. Fields come from server evaluation (unresolved applicability dimensions
  of the evaluated governed evidence, unresolved ProcedureAction parameters) — never from assistant
  prose. Each later evaluation refreshes the same record (no duplicates); a turn that evaluated
  nothing leaves it unchanged, and a non-operational result restates it instead of failing closed.
- **Follow-ups resolve against that state.** "What details?", "What is missing?", "What should I
  provide?" (a message made only of meta vocabulary about requested information) is classified
  `clarification_follow_up` against the active fault's pending clarification: the turn keeps
  `focus=continue`, starts no new troubleshooting decision, performs no governed retrieval and calls
  no specialist; the response lists exactly the unresolved fields from the record. With nothing
  outstanding the system says so and invents no fields.
- **Answers bind deterministically.** A value binds to a requested applicability field only when
  the operator literally stated it and approved governed metadata knows it for that dimension
  (ambiguous values bind nothing). A partial answer resolves only those fields and asks only for the
  rest; once every field is resolved the clarification is RESOLVED, applicability is recomputed by
  retrieval and normal troubleshooting continues. A clarification answer is never an observation for
  the pending step.
- **Resolved applicability resumes governed retrieval server-side.** When a clarification answer
  makes an APPLICABILITY clarification RESOLVED on the active fault, the TAE tool runs one fresh
  `knowledge_search` in the CURRENT run before the specialist runs (query from server-owned state:
  the fault's symptom summary and the step the clarification blocked; applicability evaluated under
  the confirmed context) and hands the AVAILABLE results to the specialist as
  `resumed_governed_retrieval`. Nothing is selected for it and no earlier run's evidence is reused:
  selection stays explicit (`knowledge_select_evidence`), the selection contract and its bounded
  remediation apply, and the completion boundary still fails closed on the resulting state. Partial
  answers, meta follow-ups and other faults never trigger it. The trace records
  `retrieval_resumption` (query, context, available identities + applicability, status) and
  `retrieval_resumption_outcome` (search performed, selected sources + applicability).
- **Clarification continuity != pending-command continuity.** The APPLICABILITY `OpenQuestion` is
  the fault's resumable dependency on its own: it also records the open `requirement_id`, the
  structured `source_identities` whose applicability it must settle, the `known_facts`, the
  `created_run_id`, and after the answer the `resolved_run_id` and `resulting_applicability`
  (identity / audit only — never a selection, an action or authority). It is recorded from the
  server's applicability evaluation whether or not a step is pending and whatever outcome the
  specialist chose (`recommended` with a refused command, or `insufficient_evidence`); a fault with
  no step is then `blocked_missing_information`. When an answer RESOLVES it, the continuation rule
  `applicability_clarification_resolved` applies with or without a pending step
  (`acquisition_continuity.pre_run_rule`): fresh discovery, explicit current-run selection, fresh
  applicability, fresh ProcedureAction derivation / issuance and Command Authority — nothing from an
  earlier run carries over. With no pending step the specialist is told which clarification was
  answered (`resumed_governed_retrieval.resumes`); if it then proposes nothing actionable while the
  evidence it SELECTED offers valid governed diagnostic actions, the server asks it ONCE to choose
  one, or say why none applies, or escalate (`resume_completeness`; never auto-chosen). On such a
  turn a step written as legacy command text that is exactly one diagnostic-read ProcedureAction of
  this run's SELECTED evidence is re-expressed as that action, so it takes the same resolver path as
  a known blocked action (`resume_procedure_action`). A pending step recorded while applicability
  was unresolved that carries nothing executable (no command, no action id, no blocked identity) is
  continued, once its applicability clarification is RESOLVED, by the governed diagnostic read
  resolved from that run's selected MATCH evidence — by structure, not wording
  (`unexecutable_step_continued_after_clarification`). A MATCH
  clears stale `applicability_unresolved` blockers from the fault's open requirements
  (`stale_applicability_blockers` must stay empty). Trace: `APPLICABILITY CLARIFICATION`
  (recorded / answered / answered_partially / settled) and `CONTINUATION ... source=`.
- **Fault-scoped.** A clarification belongs to one fault thread: it is never rendered for, or
  answered by, another fault; an explicit switch leaves it unresolved on its original fault.
  (Confirmed applicability facts remain session-scoped context, as before.)
- **Presentation.** Clarification responses are generated directly from trusted server state and do
  not require governed knowledge selection; the final-answer boundary accepts them only from a
  server-built meta record with no diagnostic step, command or governed evidence (or, when the
  specialist was not consulted, from the authoritative progression). While governed applicability is
  unresolved and no command is presented, the operator is asked ONLY for the server-evaluated
  missing dimensions, rendered from the validated record — never a synthesis that adds requests the
  validated state does not need (e.g. a target identity before a target-specific action is
  selected). Operational guidance remains fail-closed. A governed source whose applicability is not
  MATCH is shown as a candidate source pending applicability confirmation, never as authoritative.
- **Final-response completeness.** A turn the server continued (a continuation rule applied) while
  actionable state existed (open fault; SELECTED, approved, MATCH evidence offering valid governed
  diagnostic actions) must end with a next governed step, a required clarification, a governed gap,
  an escalation or a resolved conclusion; otherwise the final answer fails closed with a fixed text
  (`response_completeness` record, `synthesis_boundary.enforce_response_completeness`). A validated
  step whose authorized command the synthesis dropped is projected from the validated record. This
  is a completeness invariant, not an authority mechanism: it never generates a command.

Clarification continuity must never weaken Command Authority, evidence authority, applicability,
policy or HITL: it adds no command, grants no authority and evaluates no applicability itself.

## 14.7a-bis Server-Owned Operational Continuation Routing

Whether an operator's continuation of an ACTIVE investigation reaches the Technical Authority
Engineer is decided by the server, not by the Team Manager model
(`backend/agents/team_manager/operational_routing.py`).

- **Decision (before the Team Manager runs).** From the authoritative progression only: the fault
  in focus exists, is not resolved / escalated, and holds unfinished operational work (pending step,
  open requirement or gap, open clarification, mid-investigation phase). The operator's exact
  message is classified with the controller's own classifiers: `result_provided`,
  `clarification_answer` (only answers the open clarification), `command_follow_up` (a method
  request naming nothing else), `operation_request` (an operational action whose subject is only the
  investigation's own referents -- "the affected unit" -- or a target observed in this fault's
  trusted results), `generic_continuation` (no subject of its own, asks to go on),
  `clarification_follow_up`, `new_objective`, `subject_request`, `non_operational` (social / meta).
  The first five on an active investigation select the `governed_operational` route; everything
  else (including any message naming a subject of its own) keeps normal Team Manager routing, where
  the specialist's governance and the universal egress still apply.
- **Enforcement before the model decides.** On a governed route the Team Manager model is not
  consulted before the specialist: its first model call is replaced by a server-built call to the
  Technical Authority Engineer (arguments from the operator's exact message; the specialist rebuilds
  its request from that text and server state) -- no model routing choice, no model latency. Once
  the specialist ran, function calling is disabled (`NONE`), so the Team Manager can only present the
  validated result. Any executed model response on a forced turn is also governed
  (`restrict_forced_route_response`: calls to non-existent tools dropped, at most one specialist
  call), and a further specialist call in the same turn is suppressed at execution
  (`guard_forced_specialist_call`) -- exactly one governed evaluation per turn. The server declares
  the turn as requiring governed knowledge. A forced turn without a specialist record fails closed
  with an operational limitation, never Team Manager advice; a forced turn whose governed search
  found nothing applicable is rendered from server state (open gaps, escalation option).
- **History is not authority.** Nothing about the route grants authority: the specialist's
  pipeline (discovery, explicit current-run selection, applicability, ProcedureAction, Command
  Authority) and the universal egress decide what is shown. A pending step continues under the
  existing continuity rules (re-derived and re-authorized in the current run) or, when that is not
  possible, its RESULT is requested from the server-recorded step (no command, nothing assumed
  executed). A forced turn ends with a next governed step, a pending-result request, a required
  clarification, a governed gap / no applicable procedure, an escalation or a resolution.
- **Trace.** `ROUTING` (active fault, active investigation, turn kind, route, reason),
  `ROUTE ENFORCEMENT` (function-calling mode) and `SPECIALIST INVOCATION` (forced_by_server).

## 14.7a-ter Structured-Output Recovery

A structurally unusable FINAL answer from the Technical Authority Engineer is a serialization
failure, not an operational outcome (`backend/agents/technical_authority_engineer/structured_output.py`).

- **Eligible only:** empty / whitespace-only response, no structured payload, invalid or truncated
  JSON (including the integrity callback's malformed-JSON fallback), or a structure the response
  schema rejects. A valid response is never regenerated, whatever its outcome
  (`insufficient_evidence`, `escalation_required`, `error`, a step later rejected by resolution,
  Command Authority or progression).
- **One bounded regeneration per invocation** (max 2 structured-output attempts). The reasoning /
  tool phase is never rerun: the answer is regenerated in the SAME specialist session (same run,
  request contract, tool results, selection and issued catalog) by the same agent with NO tools
  declared, plus a server-built identity-only context (selected evidence, issued ProcedureAction
  ids). The regenerated answer has no authority of its own and passes the same integrity callback,
  ProcedureAction resolution, Command Authority, progression and egress. A second structural failure
  fails closed through the existing error boundary.
- **Trace.** `STRUCTURED OUTPUT` (attempt, phase, status, reason), `STRUCTURED OUTPUT RETRY`
  (tools_enabled=false, context_reused=true), `STRUCTURED OUTPUT RECOVERY` (outcome=fail_closed);
  the execution record carries `structured_output` (attempts, retry_count, retry_reason, outcome).

## 14.7b Evidence Requirements, Acquisition and Authority

WHAT evidence is required != WHERE knowledge comes from != HOW it can be acquired != WHETHER that
is authorized now != WHETHER it was obtained. Implemented in `backend/cases/evidence_model.py`,
`technical_authority_engineer/evidence_sources.py` and `evidence_acquisition.py`, persisted on the
Case-owned progression (`evidence_requirements`, `acquisition_gaps`; optional fields, old payloads
load unchanged).

- **EvidenceRequirement** (`kind`: operator_fact / diagnostic_result / observation /
  live_operational_context; description; status unsatisfied / satisfied / withdrawn;
  `satisfied_by` provenance). A command, procedure, tool or approval is never evidence and never an
  operator fact: such free-text "missing information" is withheld from the operator and treated as
  the system's acquisition problem.
- **Evidence sources** keep provenance (`EvidenceSourceRef`: type, id, authority class, document
  type, applicability, freshness). Authority classes: PROCEDURAL_AUTHORITY (approved MOP / SOP /
  runbook / operational procedure / technical instruction) is the ONLY class that can ground an
  operational action — through ProcedureAction + Command Authority; DIAGNOSTIC_KNOWLEDGE (RCA, KB),
  LIVE_OPERATIONAL_CONTEXT (ITSM, alarm management, monitoring, topology), OBSERVED_EVIDENCE,
  conversational and case context inform hypotheses and requirements only. Live sources plug in via
  `LiveEvidenceSource` and a capability-keyed registry (none registered: no such integration exists
  yet).
- **AcquisitionCandidate[] + selected acquisition**, decided by the server in this order: existing
  trusted evidence → approved live source query → operator fact → governed action with its CURRENT
  authority → manual observation (only for directly observable evidence). A candidate never grants
  authority.
- **Current authority decision** is evaluated fresh every run by ProcedureAction resolution,
  `build_server_validated_commands`, Command Authority and policy, and mapped per execution mode
  (advisory → AUTHORIZED, operator executes; HITL → APPROVAL_REQUIRED; closed loop →
  AUTHORIZED_BY_CURRENT_POLICY, current action only; blocked applicability / parameters; not
  authorized). It is recorded as an `AuthorityDecisionRecord` AUDIT and never read back as authority.
- **Governed acquisition gap.** When discovery completed successfully and no legitimate method exists
  (NO_APPROVED_ACQUISITION_ACTION / NO_APPLICABLE_PROCEDURE / NO_RELEVANT_EVIDENCE_FOUND), the step
  goes PROPOSED → VALIDATED → BLOCKED_GOVERNED_ACQUISITION_GAP (never PRESENTED, not pending), an
  `AcquisitionGap` audit records fault, step, requirement, searches, consulted / selected evidence,
  candidates and reason (for knowledge onboarding, SME escalation, tool / automation backlog), and the
  response is rendered from server state — it never asks the operator for a command. Applicability
  unresolved, missing parameters, governed commands refused by authority, retrieval failure, data
  source / tool unavailability and discovery not performed are distinct reasons and are NOT gaps. A
  model-written diagnostic command that names no governed action of THIS run (not the rendering of a
  ProcedureAction of the SELECTED evidence, not an applicability-blocked governed read — e.g. text seen
  only in sample output or an image transcription) is no acquisition method at all: its need takes the
  normal path below (governed alternatives, gap), never a command-less step presented as an operator
  task. Composed commands and state changes keep their own gates (one action per step, remediation).
- **Results.** A conversational request for how to obtain evidence ("what command do I run", "how do
  I check that") is never bound as a diagnostic result unless the pending command is echoed with its
  output.
- **Source provenance.** A visible Source is evidence that materially grounds what is presented (the
  presented command's source, an applicability-blocked governed action's source, validated
  citations, the procedures a presented step relied on). Other selected evidence is `consulted`:
  persisted for audit, never shown as an authoritative Source.
- **Requirement continuity (server-owned identity).** Before a requirement is created, the server
  looks for an OPEN (unsatisfied) requirement of the SAME fault with a compatible kind and continues
  it: a specialist `requirement_ref` (accepted only if it exists, belongs to the fault, is open and
  is semantically compatible), the same normalized capability, or enough shared content tokens
  (`semantic_tokens`, derived by the server from the description with mechanism wording, command
  names and generic words removed). A requirement derived only from "no method to obtain X" may
  continue the single outstanding acquirable need. Rewording refines the description (kept in
  `description_history`) but never the identity. Another fault's requirement is never reused; a
  legacy requirement without semantic identity is matched by tokens derived on the fly.
- **Mechanism vs evidence.** "Output of the X check command, including the exact command used" is
  stored as the evidence it names ("X"); a command belongs to an acquisition candidate or hint, never
  to the evidence description or identity.
- **Gap continuity.** A requirement has at most one OPEN acquisition gap; every further discovery pass
  is a `discovery_attempts` entry (run, searches, sources consulted, selected evidence, candidates,
  hints considered, outcome) on that gap. A gap is RESOLVED when an available acquisition method
  appears or the evidence is obtained, SUPERSEDED when an identified governed method only awaits
  applicability / a parameter; a refused or unauthorized command does not close it. Gaps are never
  deleted.
- **Operator acquisition hints.** "I normally use X", "the command is X", "check X instead" become an
  `AcquisitionHint` (authority `none`) on the fault's open requirement. The specialist sees hints only
  as untrusted search terms (`investigation_state.open_evidence_requirements`); a hint never enters
  the approved catalog, never becomes a command, a ProcedureAction or authority. Only a governed
  action found independently and authorized by Command Authority in the current run can acquire the
  evidence.
- **Acquisition continuity (structural, never authority).** Evidence continuity keeps the
  requirement; acquisition continuity keeps HOW it is obtained. The pending step's known governed
  acquisition -- its applicability-blocked action, its own ProcedureAction, or its requirement's
  governed candidate (`procedure_action_id`, canonical source, normalized template) -- is owned by
  the server (`acquisition_continuity.py`). It is continued when (a) the clarification that blocked
  it is answered, (b) the operator asks how to obtain the pending evidence (the existing
  `COMMAND_FOLLOW_UP` turn, bound to the pending requirement -- or to the open requirement whose
  content the message names, never simply the most recent one), or (c) the specialist restates the
  pending need without proposing any method. The specialist's proposal is reconciled with that state:
  it may propose the action itself or a different method, but omitting the method never removes it.
  Continuity is a LOOKUP KEY only: the action must be re-derived from THIS run's SELECTED, approved,
  applicability-MATCH governed evidence (same deterministic id, source and template, a diagnostic
  read); the normal resolver and Command Authority then decide. When the specialist itself chooses
  the known id without listing `procedure_action_catalog`, the server issues exactly that re-derived
  action into the run's catalog (never the specialist's text, never another id, never a state
  change). A command authorized on the legacy free-text path that is exactly one diagnostic-read
  ProcedureAction of THIS run's selected evidence keeps that `procedure_action_id` on its step and
  acquisition candidate (continuity metadata only, no authority). Escalation outcomes are never
  pre-empted. An acquisition still awaiting an OPEN applicability clarification is answered by that
  clarification.
- **Structured evidence identity.** A governed section is the exact tuple
  (`knowledge_id`, `version_label`, `section_id`) (`backend/cases/evidence_identity.py`); any part
  may contain `:`, so `knowledge_id:version_label:section_id` is a display / log string only and is
  never parsed back. Steps, blocked actions and acquisition candidates persist the structured
  identity; the specialist receives exact selection keys (`pending_step.selected_evidence`,
  `governed_acquisition.selection_key`), never a source-id string to re-select. Continuity compares
  tuples (or the ProcedureAction id, a digest of the tuple). Records persisted before this carry no
  structured section and are never re-derived from their string. Two different selected tuples that
  render the same display string are withheld from trusted evidence (fail closed).
- **Mandatory discovery before a governed acquisition.** For (b) the server runs one fresh
  `knowledge_search` from server state before the specialist; if a continued acquisition still has no
  governed search in the run, the server runs it and asks the specialist once for an explicit
  selection decision (also when the known source is AVAILABLE with MATCH but undecided). Nothing is
  selected on the specialist's behalf; if no authority can be established the final boundary still
  fails closed.
- **Server-driven continuation.** Every turn is classified from server state (`ContinuationKind`:
  RESULT_PROVIDED, NEW_OBJECTIVE, CLARIFICATION_ANSWER, COMMAND_FOLLOW_UP, GENERIC_CONTINUATION, NONE).
  A follow-up that adds nothing of its own ("what next?", "continue") is a GENERIC_CONTINUATION; it is
  acted on only when a pending step awaits its result, its governed acquisition is known and not
  waiting for an open clarification, and that source is not AVAILABLE in the current run (an earlier
  run's selection never counts). The server then runs a fresh governed search before the specialist
  (AVAILABLE only); selection, reconstruction, resolution and Command Authority follow as normal. A
  result, a new objective, a clarification answer or a method request keeps its own existing path;
  with no known acquisition nothing is searched or invented. When a governed recommendation arrives
  with nothing selected and nothing AVAILABLE in the run, the selection remediation first runs one
  governed search, then still requires the specialist's explicit selection.
- **Gap recovery: a gapped branch is not an exhausted investigation.** A governed acquisition gap
  means THIS evidence requirement has no usable governed method now. With no step pending, the server
  forecasts the acquisition decision of the specialist's proposal (on a copy of the progression); if
  it is a gap, the server determines the fault's remaining VALID governed branches
  (`gap_recovery.py`): diagnostic-read ProcedureActions from THIS run's governed evidence that are
  approved + applicability MATCH, not performed / known for the fault, and not blocked by an incomplete
  mandatory prerequisite (one fresh governed search when none is found). The specialist is asked once
  to choose among them (or escalate); the chosen one is re-derived from THIS run's SELECTED evidence and
  passes the normal resolver, target gate, Command Authority and egress. The gap is always recorded;
  it is rendered as the terminal limitation only when no valid branch exists. The same gapped
  requirement re-proposed with nothing materially new (no new trusted result, operator hint, consulted
  source or applicability outcome) reuses the gap without another discovery attempt (at most one
  attempt per run).
- **Acquisition-gap continuation.** When the specialist's final proposal still ends in a gap after that
  bounded path (current catalog choice, governed alternatives, one server search), the server records
  the gap and asks the specialist ONCE, tools enabled, to continue reasoning from the evidence already
  collected: the next hypothesis or evidence requirement, without inventing a command or repeating the
  unavailable need (`gap_recovery.gap_continuation_instruction`). Its next proposal passes the normal
  chain. The final answer is prefixed with a server-rendered notice (`render_acquisition_gap_notice`,
  from the execution record only — never shown to the Team Manager as input): the need for which no
  governed method could be found or cross-checked, that no command is provided for it, the model's
  hypothesis labelled as model-generated, and a knowledge status that calls only an authorized command
  of the next step governed. A gap the final decision itself addresses (same requirement) is rendered
  by its own path, never twice; the trace records `GAP CONTINUATION`.
- **Contentless continuation.** A message that introduces no technical subject ("what do you
  suggest?", "and now?", "go ahead") continues the active fault: neither word overlap with the active
  objective nor a caller's switch claim turns it into a new objective.
- **Candidate lifecycle.** A governed candidate records `validation` for the run in
  `validated_run_id`: `blocked_applicability`, `available_current_run`, `authorized_current_run`,
  `rejected_current_run`; read in any other run it is `known_structural` (identity only). Candidates
  are merged, never replaced: a known method this run did not re-evaluate is kept (re-validated
  methods keep their `acquisition_id`). A requirement with a known, non-rejected method is BLOCKED,
  not a gap; a method this run's selected evidence positively no longer supports is
  `rejected_current_run`, after which normal gap classification applies.

## 14.8 Avoid hallucinated operational authority

Commands, procedures, and remediation instructions should be traceable to approved context wherever possible.

Implementation (troubleshooting tranche 2 — `backend/agents/technical_authority_engineer/procedure_actions.py`):

```text
SELECTED governed evidence (approved, current, applicability MATCH)
  -> ProcedureAction            derived deterministically from the section text
  -> server-issued catalog      `procedure_action_catalog`; the TAE chooses an action_id only
  -> trusted parameter binding  VERIFIED / MISSING / AMBIGUOUS / CONFLICTING
  -> ResolvedCommandCandidate   strict template render, no partial substitution
  -> AuthorizedCommand          ONLY via the existing Command Authority (build_server_validated_commands)
```

ProcedureAction != authorization, and ResolvedCommandCandidate != authorization. The LLM decides
WHAT to check; the governed ProcedureAction determines HOW; deterministic code renders the command;
the existing Command Authority remains the final boundary (grounding, operation classification,
placeholder check, trusted target confirmation for state-changing operations).

- Actions are re-derived from the currently SELECTED evidence on every use and never persisted, so
  an action cannot outlive its source: new version -> new action_id; unselected, non-approved or
  non-MATCH source -> no usable action.
- Extraction is structural only (code spans, fenced blocks, lines of an explicit "...command(s):"
  block). Prose never becomes a command; prohibited ("Do not run ...") commands are never actions.
- An action_id the server did not issue this turn fails closed; the model's own command text is
  ignored whenever it chose an action.
- Parameter values are accepted only when the operator literally typed them (case-sensitive,
  command-safe charset) or they were verified earlier in the session
  (`confirmed_procedure_parameters`); the model can only indicate which placeholder a value fills.
  Missing/ambiguous values produce a deterministic clarification; a conflict with a previously
  confirmed value fails closed and clears it.
- Legacy model-written commands (no action_id) are still grounded exactly as before; this path is
  deprecated and gains no new authority.
- **One command = one invocation** (`command_syntax.py`, one syntactic boundary). A candidate that
  composes invocations or lets the CLI evaluate embedded text — `;`, `&&`, `||`, `|`, newline,
  backtick / `$(` / `${` substitution, `&`, `<`/`>` redirection outside a `<placeholder>` — is never
  eligible for authorization, even when that exact string is in approved, SELECTED, MATCH evidence.
  It is rejected by Command Authority before classification (every path: legacy command, resolver
  candidate, attested candidate, control-plane re-authorization), never classified read-only,
  never extracted as a ProcedureAction, and refused by the resolver. The Progression Controller
  independently rejects a multi-action step (including the model's legacy command text).
- Procedure-step command lines (a lowercase command verb with argument syntax, e.g. a step title
  followed by its command) and a document's keyed placeholder notation (`key=xxxx`, parameter named
  by the key) are extracted verbatim. Command Authority grounds a plain-line parameterized template
  only as a strict charset-valid rendering of the template the same deterministic extractor
  re-derives from the authorized section; prose-only recovery guidance yields no action
  (knowledge gap, fail closed).
- **Text semantics** (`procedure_semantics.py`, explicit evidence only). Every command-looking
  candidate is a PARAMETERIZED_TEMPLATE (explicit `<name>` / `{name}` / `Key=xxxx`), a
  FIXED_INSTANCE (literal; a literal `Key=Value` is a FIXED target the target gate must find in the
  case), an EXPLICIT_EXAMPLE (example/sample/"e.g."/"such as"/"illustrative" label or marker, or
  "replace <literal> with ..."), SAMPLE_OUTPUT (output / printout / transcript / expected-result
  block, prompt-prefixed CLI transcript, output-typed fence), SCREENSHOT_TRANSCRIPTION
  (screenshot / figure / image label, or a section derived from an artifact whose text is a model
  interpretation) or UNKNOWN. Only the first two become actions; a literal is never turned into a
  slot and generic prose never makes an instance an example. Command Authority grounds against
  the section's instruction text only, so a command that appears only in an example, output or
  transcription is never authority.
- Operation classification recognizes the action-invocation structure generically (lowercase verb,
  managed-object argument(s), lowercase action word beginning with a state-changing verb, plain
  arguments): such commands become state changes and therefore pass the target gate,
  confirmation and approval; camelCase attribute reads are unaffected.
- **Document structure** (numbered outline / section headings of the same governed version,
  loaded server-side per selected section): top-level precondition / post-action blocks and the
  verbatim text of an action's own condition-specific block are bound to state-changing actions
  (restrictions, confirmation/approval binding; a change denies approval with
  `SOURCE_CONDITIONS_CHANGED`). An action inside a `<qualifier>-specific` block applies only when a
  trusted, validated result of the same fault literally states that block's condition
  (`CONDITION_NOT_ESTABLISHED` otherwise; re-checked at approval). If the version's structure
  cannot be established, state changes fail closed (`GOVERNED_SCOPE_UNAVAILABLE`). Cross
  references ("same as ...") are not followed. Time windows, rate limits and post-action duties are
  bound to the human approval, not machine-verified.

## 14.9 Keep state-changing actions controlled

Diagnostics and reads are different from production writes or remediation.

Implementation (troubleshooting tranche 3 — `backend/operations/`). Separate trust boundaries,
evaluated in order and never merged:

```text
Command Authority (unchanged)  ->  Action Policy  ->  Target Confirmation  ->  Human Approval  ->  Execution eligibility
```

- **Policy** (`policy.py`) runs only on an `AuthorityAttestation` minted from a result the existing
  Command Authority returned (HMAC-signed, process-scoped key). A hand-built/forged/edited
  attestation is PROHIBITED / `COMMAND_NOT_AUTHORIZED`. Policy only restricts: decisions
  `allowed | approval_required | clarification_required | prohibited` with stable reason codes.
  Default policies are keyed by the existing `CommandOperationType`: read-only diagnostics may be
  executed (only through a registered adapter); mutating/configuration actions require target
  confirmation and human approval.
- **Target confirmation** (`targets.py`) is created only when an operator confirms the target on
  the action card (trusted `/approve` endpoint, identity from `UserContext`). It is bound to the
  action's control/check, procedure action, target, command and governed source (binding hash)
  and expires. Parameter verified != target confirmed. Only an existing confirmation id feeds the
  Command Authority's `trusted_context.target_confirmed`.
- **Approval** reuses the existing proposal lifecycle (`backend/approval`) with a separate closed
  `OperationalOperation` enum. Approving re-validates the source against the repository (current
  version, approved, identical section text, applicability MATCH), the binding hash, the target
  confirmation, Command Authority and policy. Any change invalidates it. The model has no path to
  confirm or approve.
- **Execution**: state-changing actions end at `READY_FOR_EXECUTION`; `/execute` refuses
  operational proposals and no state-changing adapter exists. Diagnostic reads can be executed
  only on explicit operator request (`POST /api/sessions/{id}/diagnostic-checks/{check_id}/execute`)
  through an adapter registered for the explicitly configured `SLOPANOC_DIAGNOSTIC_EXECUTION_CONTEXT`
  (none by default: `EXECUTION_UNAVAILABLE`, never a local/shell fallback). Adapters accept only a
  signed `AuthorizedReadAction`. Output is recorded on the same check as observed evidence
  (`observed_metric`), shown to the next TAE evaluation, and can never ground or authorize a
  command. The next step is never executed automatically.

## 14.10 End clearly

A troubleshooting session should terminate in one of the following:

```text
RESOLVED
SUFFICIENTLY NARROWED
ESCALATE
DISPATCH
HAND OVER TO NEXT OWNER
```

Not in an indefinite sequence of generic suggestions.

---

# 15. Architecture Decision Filter

Every major architecture decision should be checked against this strategy.

Before implementing a capability, ask:

1. Does this help SLOPANOC determine the next-best diagnostic step?
2. Does it improve the quality, authority, or relevance of context?
3. Does it help maintain troubleshooting continuity?
4. Does it help narrow hypotheses?
5. Does it preserve evidence and provenance?
6. Does it help the engineer know what to check and how?
7. Does it avoid overwhelming the engineer with unnecessary context?
8. Does it preserve deterministic control over sensitive actions?
9. Does it remain reusable across future operational domains?
10. Does it support the iterative troubleshooting loop rather than bypass it?

If not, the capability may be useful elsewhere, but it must not distort the core troubleshooting architecture.

---

# 16. Phase Alignment Rules

**Locked execution order (see `docs/BUILD_SEQUENCE.md` §2a for the full
realignment rationale — this replaces the previous 5.1 → 4H → 5.2+ → 6
order):** 5.1 (COMPLETE, includes A5) → **5.X (COMPLETE / FROZEN,
canonical P10)** → **Phase 6A (← NEXT, NOT STARTED)** → Phase 4H → 5.2+
→ **Phase 6B** → Phase 7. The locked
roadmap ends at Phase 7 — Phase 8 (below) is pre-existing content
describing what lies beyond the current roadmap, not part of this
locked order.

## Phase 5.1 — Generic Knowledge Management (COMPLETE)

Must support the future troubleshooting experience by delivering:

- governed knowledge objects,
- metadata,
- applicability,
- lifecycle,
- versioning,
- structured procedural sections,
- retrieval,
- ranking,
- provenance,
- generic agent-facing knowledge contracts.

It must not attempt to build the full troubleshooting loop. Complete,
including A5's real TELCO/RAN compound Knowledge Island validation.

## 5.X — Teams Rich Content / Media Retrieval (COMPLETE / FROZEN, canonical P10)

Added Teams-originated rich visual evidence (images) to the
troubleshooting experience's evidence sources, on top of Teams message
text — now CURRENT, alongside the pre-existing capability of a user
uploading an image directly into a SLOPANOC chat. Preserves the same
`chat → message → media` deterministic-binding and provenance discipline
already governing Teams text evidence, extended to the full
`(chat, message, hosted-content)` triple — see `docs/BUILD_SEQUENCE.md`
§2b for the as-built contract and design constraints, and
`docs/MASTER_ROADMAP.md`/`docs/DEFECT_REGISTER.md` for the full
implementation and defect history. FROZEN: do not casually rework this
surface without a real, observed defect or a new, explicitly approved
milestone.

## Phase 6A — Intelligence Architecture Foundation (← NEXT, NOT STARTED, after 5.X)

Builds a BOUNDED intelligence/orchestration foundation against the
context sources that already exist now that 5.1/A5/5.X are all complete
— not the full future Operational Context surface (5.2+ do not exist
yet). New agents
should correspond to real bounded operational responsibilities. Do not
add agents merely to make the architecture appear more agentic.

A reusable **Skills** layer (§12a) and an **Experience Memory**
foundation (§9, `docs/BUILD_SEQUENCE.md` §2a target architecture) belong
to this phase's scope alongside a future Troubleshooting Manager —
neither is a reason to add a new agent per fault type; a Skill is
behavioral orchestration executed BY an agent, not an agent itself
(§12a, `docs/AGENT_CONTRACT.md` §3a). Does not implement Phase 7's
mature troubleshooting loop (persistent Troubleshooting State, hypothesis
lifecycle, next-best-diagnostic-action loop, end states) — 6A is a
foundation, not the finished troubleshooting experience.

## Phase 4H — Security (FUTURE, after Phase 6A)

Must protect the troubleshooting experience against:

- prompt injection,
- poisoned knowledge,
- malicious bridge content,
- tool misuse,
- approval bypass,
- source spoofing,
- secret leakage,
- cross-user leakage.

Security controls must preserve the iterative UX rather than turning it
into an unusable approval sequence for normal diagnostic reads. Not
cancelled or reduced in importance — rescheduled after Phase 6A so it
evaluates the richer, more stable architecture 6A produces (including the
Troubleshooting Manager boundary, Skills framework boundary, and Context
Engineering foundation boundary), including Teams rich media from 5.X.

## Phase 5.2+ — Operational Integrations (FUTURE, after Phase 4H)

Each new integration should increase the system's ability to gather evidence automatically.

Examples:

```text
ITSM      → incident/work-note/status evidence
Alarms    → live fault evidence
Topology  → dependency and impact evidence
KPIs      → service-health evidence
Change    → deployment/change evidence
Handover  → operational continuity evidence
```

## Phase 6B — Context Engineering Expansion (FUTURE, after 5.2+)

Expands the SAME Phase 6A foundation (never a second, competing
architecture) against the complete Operational Context surface once
5.2+ exist: multisource context assembly, relevance/authority/freshness
ranking, context budgeting, conflict handling, cross-source correlation,
Experience Memory refinement, specialist context policy refinement.

## Phase 7 — Advanced Troubleshooting / JOC

This phase brings together:

```text
Operational Context
+
Knowledge Context
+
Case Context
+
Experience Memory
+
Troubleshooting State
+
Skill selection/execution
+
Next-Best-Diagnostic-Action reasoning
```

This is where the full troubleshooting experience defined in this document becomes a first-class runtime capability. This is the end of the current locked execution roadmap (5.X → 6A → 4H → 5.2–5.7 → 6B → 7) — see §16.

## Beyond the current locked roadmap — Phase 8, Controlled Autonomy

Phase 8 is preserved here as a pre-existing long-term product-strategy
concept, not as a scheduled next implementation step. It is explicitly
**beyond the current locked execution roadmap**: it has no assigned
dependency position after Phase 7 and is **not currently scheduled for
implementation**. It is retained only as long-term direction the product
may eventually pursue, contingent on its own future roadmap decision.

The progression remains:

```text
Advisory
   ↓
Human-in-the-loop
   ↓
Scenario-approved closed loop
```

Closed-loop execution must not remove grounding, evidence, approval, or governance controls.

---

# 17. Product Success Criterion

The ultimate measure of SLOPANOC troubleshooting is not:

- how many documents it can retrieve,
- how many agents it contains,
- how much context it can place in a prompt,
- how many integrations exist,
- or how sophisticated the orchestration graph looks.

The measure is:

> **Can SLOPANOC help an engineer move from an ambiguous operational problem to the next correct diagnostic action, interpret the resulting evidence, continuously narrow the fault, and reach resolution or a clear escalation path with grounded, authoritative support?**

That is the product.

---

# 18. Non-Negotiable Summary

The following principles are mandatory:

1. **Troubleshooting is iterative, not checklist-driven.**
2. **The primary question is: "What should I check next, and why?"**
3. **Every new piece of evidence must be interpreted before the next step is selected.**
4. **Context must be continuously re-evaluated.**
5. **The system must maintain troubleshooting state.**
6. **Only relevant, valid, authoritative context should reach the reasoning layer.**
7. **Operational commands should be grounded in approved knowledge wherever possible.**
8. **Diagnostic reads/checks and production-changing actions are different trust classes.**
9. **State-changing actions remain behind deterministic approval/control.**
10. **Generic KM exists to provide governed Knowledge Context, not to become a MOP reader.**
11. **Context Engineering eventually combines Operational, Knowledge, and Case context.**
12. **The experience must remain conversational and one-step-at-a-time.**
13. **The loop ends in resolution, sufficient narrowing, escalation, dispatch, or handover.**
14. **All future architecture and product decisions must be evaluated against this strategy.**

---

## Locked Product Principle

> **SLOPANOC must deliver an iterative, evidence-driven and conversational troubleshooting experience — one useful diagnostic step at a time — continuously interpreting new evidence, retrieving additional authoritative context when necessary, narrowing hypotheses, and guiding the engineer until the fault is resolved, sufficiently narrowed, or clearly escalated.**
