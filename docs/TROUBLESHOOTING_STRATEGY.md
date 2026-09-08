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

### Current

```text
Team Manager
Incident Manager
Teams
Cases
Generic KM Layer → Knowledge Context (Phase 5.1, complete)
Current-turn multimodal image evidence, combined with Teams and/or
  Knowledge Context in one specialist turn (POST-5.1 B0-B6, complete)
```

### Next

```text
POST-5.1 B7 — attachment lifecycle / real UI / full regression
A5 — real TELCO/RAN MOP ingestion (real knowledge content, same pipeline)
Phase 4H — security hardening
```

### Later

```text
5.2-5.7 — Operational Context integrations (ITSM, Alarms, Topology, KPIs,
  Change, Handover)
Phase 6 — Agent expansion + Context Engineering
```

### Future

```text
Troubleshooting Manager
Context Engineering Layer
Head of Automated Operations (optional future supervisory layer, never a
  mandatory hop)
Persistent Troubleshooting State + next-best-diagnostic-action loop
  (Phase 7 — the product target this document defines)
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

## 14.8 Avoid hallucinated operational authority

Commands, procedures, and remediation instructions should be traceable to approved context wherever possible.

## 14.9 Keep state-changing actions controlled

Diagnostics and reads are different from production writes or remediation.

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

## Phase 5.1 — Generic Knowledge Management

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

It must not attempt to build the full troubleshooting loop.

## Phase 4H — Security

Must protect the troubleshooting experience against:

- prompt injection,
- poisoned knowledge,
- malicious bridge content,
- tool misuse,
- approval bypass,
- source spoofing,
- secret leakage,
- cross-user leakage.

Security controls must preserve the iterative UX rather than turning it into an unusable approval sequence for normal diagnostic reads.

## Phase 5.2+ — Operational Integrations

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

## Phase 6 — Agent Expansion

New agents should correspond to real bounded operational responsibilities.

Do not add agents merely to make the architecture appear more agentic.

## Phase 7 — Advanced Troubleshooting / JOC

This phase brings together:

```text
Operational Context
+
Knowledge Context
+
Case Context
+
Troubleshooting State
+
Next-Best-Diagnostic-Action reasoning
```

This is where the full troubleshooting experience defined in this document becomes a first-class runtime capability.

## Phase 8 — Controlled Autonomy

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
