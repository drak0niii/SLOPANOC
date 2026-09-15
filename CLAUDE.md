CLAUDE.md

1. Purpose

SLOPANOC is an enterprise AI assistant for Microsoft Teams-based incident and operational collaboration.

It is a real application, not a UI-only prototype:

React + TypeScript frontend (Vite/Tailwind)

FastAPI backend

Gemini via Google ADK for reasoning/orchestration

Microsoft Teams/M365 access through Power Automate

Persistent sessions, Case Context, governed Knowledge, multimodal evidence, streaming/cancellation, provenance, approval-controlled writes, and Phase 6A intelligence foundations

The core product objective is trusted operational reasoning: assemble only relevant, validated context; explain what should be checked next and why; and keep every sensitive operational boundary deterministic.

2. Source-of-truth hierarchy

Before any non-trivial architectural change, read:

README.md

docs/AGENT_CONTRACT.md

docs/TEAMS_TOOL_CONTRACT.md

docs/BUILD_SEQUENCE.md

docs/MASTER_ROADMAP.md

docs/DEFECT_REGISTER.md

Also read when relevant:

docs/KNOWLEDGE_CONTRACT.md for Generic KM / governed Knowledge

docs/TROUBLESHOOTING_STRATEGY.md for troubleshooting behavior

docs/INTELLIGENCE_ARCHITECTURE.md for Phase 6A / POST-6A intelligence contracts

docs/GCP_INTELLIGENCE_RUNTIME.md for GCP physical/runtime decisions

docs/PRODUCT.md and docs/UX_SPEC.md for original product/UI intent only; they do not prove backend capability

Rules:

Implementation + current tests are runtime truth.

The canonical roadmap/status lives in MASTER_ROADMAP.md / BUILD_SEQUENCE.md, not in historical prose here.

Defect status and root-cause history live in DEFECT_REGISTER.md.

If this file disagrees with current implementation or canonical docs, investigate and correct this file; do not force code to match stale memory.

3. Current runtime architecture

User
  ↓
React UI
  ↓
FastAPI backend
  ↓
Gemini ADK Team Manager          ← sole user-facing agent
  ├─ Incident Manager            ← specialist
  └─ Troubleshooting Manager     ← specialist
       ↓
Context / Skills / governed evidence
       ↓
Controlled typed tools/services
       ↓
Power Automate gateway
       ↓
Microsoft Teams / M365

Current architectural invariants:

Team Manager is the only user-facing agent.

Incident Manager and Troubleshooting Manager are specialist reasoning boundaries; do not expose them directly to the user.

Specialists are invoked through bounded ADK tool/in-process orchestration, not unconstrained agent hand-off.

Power Automate is the current M365 gateway.

Do not introduce direct Microsoft Graph integration unless explicitly requested as a deliberate architecture change.

Do not create a new agent for each fault/use case when the behavior belongs in a Skill or existing specialist.

No Router Agent unless a future approved architecture explicitly introduces one.

4. Canonical intelligence model

Keep these concepts separate:

Agents

Reasoning boundaries. Current specialists: Incident Manager and Troubleshooting Manager.

Skills

Reusable behavioral methodology: how a specialist should perform a class of work.
A Skill is not an agent, MOP, SOP, tool, or memory record.

Tools / connectors

Controlled capabilities used by agents. Current major integration: Teams through typed tools → Power Automate.
MCP is optional future transport, not a mandatory rewrite of typed integrations.

Knowledge

Governed organizational content. MOP, SOP, RCA, KB, Troubleshooting Guide, Operational Procedure, etc. are document/content types inside Generic KM.
MOP ≠ Skill. SOP ≠ Skill. RCA ≠ Experience Memory.

RAG

A retrieval mechanism over the governed Knowledge repository, not a second repository or source of truth.

Session state

Conversation/runtime continuity. It is not Approved Knowledge.

Case Context

Persistent fault/investigation context, distinct from ordinary session state. It is not Approved Knowledge.

Experience Memory

Bounded, non-authoritative prior-case/pattern information. It may inform reasoning but never becomes organizational truth merely because it was observed repeatedly.

Context Engineering

Deterministically assembles bounded operational, Knowledge, Case, session, Skill, and Experience inputs for reasoning. Only relevant, validated context should reach a model.

Authority invariant:

Approved procedural prohibitions and governed facts outrank Experience Memory or inferred patterns.

Model inference never becomes trusted operational fact merely because the model states it confidently.

5. Trust and control boundaries

The model MAY:

interpret and summarize retrieved content

classify a request

choose among already-permitted reasoning paths

select from evidence exposed through approved contracts

propose an operational/Teams action

The model MAY NOT:

approve or authorize its own write/action

bypass approval state

invent or replace a trusted destination

fabricate evidence/provenance/source identity

promote ingested content to Approved Knowledge

grant itself permissions

access unrestricted HTTP, SQL, shell, databases, or storage directly

turn model-generated parameter values into trusted live target identifiers

expose secrets/credentials/gateway URLs

Sensitive decisions must be deterministic application behavior, not prompt-only rules.

Always ask: where is this safety/trust decision enforced in code? If the answer is only “the prompt tells the model,” the design is insufficient.

6. Troubleshooting product strategy

docs/TROUBLESHOOTING_STRATEGY.md is binding.

Core behavior:

Troubleshooting is iterative, evidence-driven, and context-sensitive.

The primary question is: “What should I check next, and why?”

Interpret each new evidence item before selecting the next action.

Re-evaluate relevant context after every meaningful observation.

Preserve investigation continuity across turns.

Do not dump all available context/documents into the model.

Prefer governed operational procedures for commands/instructions.

Diagnostic reads/checks and state-changing actions are different trust classes.

State-changing actions remain behind deterministic approval.

Generic KM provides Knowledge Context; it is not the troubleshooting loop or next-best-action reasoner.

7. Teams behavior

Current capabilities include:

deterministic conversation targeting: current_thread, selected external conversation, or explicit external conversation

chat discovery and deterministic name resolution

ambiguity handling through SelectionCard

paginated/time-scoped message retrieval

extraction of decisions/actions/proposals/open questions/risks

validated Teams provenance

contributor/member enrichment

proposed write → trusted approval/rejection → deterministic re-authorized execution

Teams-originated text and rich image/media retrieval

multimodal reasoning over Teams images

Important boundaries:

Selection and approval are separate state machines.

Once a destination is trusted for a turn, the model cannot substitute another destination.

A Teams write always goes through the approval boundary.

Evidence shown to the user must trace to actually retrieved Teams content.

SourceReference is message/turn-owned, not a global session artifact.

Do not give the model arbitrary URL-fetch capability.

8. Multimodal / attachments

Current user-uploaded image path:

frontend image attach/paste

backend upload API

Cloud SQL attachment metadata

private GCS binary storage

secure authenticated retrieval

persisted attachment rehydration

Gemini/ADK multimodal reasoning

Current Teams-media path:

deterministic Teams message/media discovery

backend-owned retrieval of all relevant hosted content

full (chat, message, hosted-content) provenance binding

multi-image delivery in document order

visual evidence in source/provenance UI

Gemini multimodal reasoning

Do not create a second vision architecture for Teams media; reuse the established multimodal path.

Image-bearing persisted user turns are intentionally not editable unless multimodal edit semantics are explicitly designed.

9. Persistence and runtime policy

Normal runtime

Normal live/manual/E2E/integration runtime MUST use PostgreSQL/Cloud SQL for all persistent SLOPANOC domains.

Required properties:

SLOPANOC_SESSION_BACKEND == "database"

general persistence resolves to PostgreSQL

governed Knowledge persistence independently resolves to PostgreSQL

no silent runtime fallback to SQLite/in-memory

The general DB and Knowledge DB are logically separate configuration concerns even when they currently point to the same physical database.

Automated tests

Disposable SQLite/in-memory persistence is allowed for isolated automated tests only.
The test suite bypasses the normal runtime DB policy through Python-level test substitution, not a production environment-variable escape hatch.

Current DEV Cloud SQL

project: pr-msn-dev-gl-slopai-01

region: europe-west4

instance: sloc-anoc-sandbox01

database: slopanoc

local dev auth path: Cloud SQL Auth Proxy v2 + IAM DB authentication, no DB password

Alembic manages SLOPANOC-owned Case/Knowledge/application schema only. ADK owns its session schema separately; do not make Alembic manage ADK tables.

Least-privilege role model:

slopanoc_migrator: schema/migration authority

slopanoc_runtime: application DML/runtime authority

Do not infer production readiness from successful DEV Cloud SQL validation.

10. Generic Knowledge Management

Generic KM is CURRENT and must remain independent of any specialist agent.

Core principles:

one governed KnowledgeRepository abstraction

lifecycle/version/applicability are deterministic

ingestion produces CANDIDATE, never APPROVED

only the human-gated governance transition can create Approved Knowledge

retrieval combines deterministic applicability narrowing with lexical/semantic retrieval/ranking

semantic similarity alone is insufficient for operational use

approved/current/applicable content is preferred for operational reasoning

provenance is revalidated against real repository content before becoming trusted evidence

vector/embedding indexes are retrieval aids, never the system of record

agents access Knowledge through typed tools/services, never raw DB/storage

Knowledge object types all use the same generic architecture. Do not create one-off MOP/SOP pipelines.

11. Phase 6A intelligence foundation — current

Phase 6A / P11 (6A.0–6A.11) is COMPLETE AND FROZEN.

Current foundation includes:

canonical intelligence architecture/contracts

GCP runtime/tooling decision record

TELCO Context & Applicability model

multimodal Knowledge ingestion/provenance

deterministic applicability + Knowledge narrowing

hybrid exact/lexical/semantic retrieval with Vertex embeddings + pgvector

Context Engineering / evidence-package foundation

Skills framework

Experience Memory foundation

Troubleshooting Manager

dual-specialist Team Manager orchestration

integrated TELCO validation/freeze

Phase 6A does NOT mean SLOPANOC is production-ready and does NOT include:

live ITSM integration

live alarm/fault integration

topology/inventory integration

KPI/observability integration

change/handover integration

autonomous remediation

mature Phase 7 iterative JOC troubleshooting loop

Do not reopen frozen Phase 6A architecture casually. Post-6A corrective/foundation work may improve boundaries without rewriting P11 history.

12. Request Contract and deterministic execution

A validated RequestContract is the canonical structured statement of what the current user turn asks for.

It includes concepts such as:

intent

subject

requested output

whether governed Knowledge / operational context is required

continuation state

provided context + provenance

missing context

ambiguity

action/approval requirements

Critical rules:

model proposes contract fields; deterministic code validates safety-relevant claims

user-provenance values must be verified against real current-turn or valid same-subject prior-turn user context

example identifiers from governed procedures are not live target identifiers

grounded in Knowledge ≠ correctly parameterized for the live target

explicit subject changes do not inherit old subject parameters

ACTION always remains approval-controlled

stale prior-turn contracts cannot authorize current-turn operational output

Deterministic execution policy controls whether a turn may emit commands/operational steps or must request more information.

Both layers must hold before operational content reaches the user:

procedure/evidence grounding

request/target/execution authorization

13. Operational-command safety

Current command-safety invariants:

operational commands must be grounded in the current turn's selected governed evidence

active procedure selection is deterministic

paraphrased/composite commands are not accepted merely by token similarity

free-text operational content cannot bypass structured grounding controls

request response mode must be compatible with the validated requested output

missing target information fails closed where required

governed example values must never be silently substituted as user-supplied live identifiers

DEF-0024/0026/0027 and subsequent 6A.13/6A.14 corrections are safety layers, not optional formatting behavior. Do not weaken them to improve convenience.

14. Canonical Turn Result and streaming

A turn has one canonical final user-visible result.

Current rules:

all deterministic corrections happen before canonical persistence

canonical result persists before message.completed

history/reload projects the same canonical result instead of reconstructing conflicting raw ADK text

failed canonical persistence fails closed

rewind removes discarded turn-owned canonical state through existing session-state behavior

Streaming safety:

status/progress events may stream

raw assistant/specialist text must NOT stream via message.delta

validated canonical assistant text is delivered once through message.completed

Do not reintroduce raw progressive token streaming without an explicit design proving deterministic corrections cannot be bypassed.

15. Current POST-6A status

Use docs/MASTER_ROADMAP.md §7a for the canonical 6A.12→6A.28 closure sequence and current status.

Current high-level state from the latest implemented work:

Phase 6A / P11: COMPLETE AND FROZEN

6A.12: IMPLEMENTED; final live closure still open

6A.13 Request Contract Foundation: COMPLETE

6A.14 Deterministic Request Execution: IMPLEMENTED/complete code path; live acceptance work remains part of the closure campaign

6A.14A Canonical Turn Result & Projection: IMPLEMENTED + hardened; live browser acceptance remains open

LIVE-CORR-2: DONE

DEF-0037 FIXED

DEF-0039 FIXED

DEF-0043 FIXED

DEF-0038 PARTIALLY FIXED; later step-aware precision still required

LIVE-CORR-3: DONE

DEF-0040 FIXED

DEF-0044 FIXED

DEF-0041 FIXED (closed — not reproducible after current corrective stack): live re-test against the real backend/Cloud SQL/Gemini confirmed the structured command field is correctly grounded/stripped, twice, independently, in the exact cross-section evidence shape that originally caused it. Found in the same pass: the LIVE-CORR-3 grounding instrumentation was never actually observable in any real run (this codebase never raises logging above Python's default WARNING threshold anywhere) — unfixed, a real gap for a future pass. Also found and recorded (not implemented): DEF-0045 — a verbatim governed command string can still reach the user via a step's free-text `action` field, which no grounding mechanism inspects, even when the same step's structured `command` field is correctly stripped

DEF-0042 remains OPEN

LIVE-CORR-3B — Operational Authority Boundary: DONE

Command permission is now possible only for a validated intent=COMMAND, requested_output=EXACT_COMMAND request — `derive_execution_decision`'s ALLOW branch no longer implicitly sets may_emit_command=True for PROCEDURE_STEPS/TROUBLESHOOTING_NEXT_STEP-shaped output

requires_unstructured_response_backstop now also fires for an operationally-shaped ALLOW decision with no TroubleshootingGuidance (previously only NEEDS_INFORMATION/AMBIGUOUS); INVALID_CONTRACT was audited for the same widening and deliberately left unchanged — see that function's own docstring for the two rejected designs (both measured directly against the real test suite and found unsafe)

The TroubleshootingOperationalEffect.DIAGNOSTIC_READ target-independent bypass (enforce_execution_decision_on_guidance) is removed outright — DEF-0040 is now FIXED without any model-controlled exemption remaining

required_target_parameter_gaps no longer treats an unrecognized unit_type as gap-free — only the verified SUPPORTUNIT allowlist is exempt

TroubleshootingStep/TroubleshootingGuidance gained an additive source_section_id field, verified against the real selected-evidence set (evidence.py's `_verify_source_section_reference`) before a command is trusted

Note: LIVE-CORR-3A (a prior, undocumented pass) had already replaced DEF-0040's original substring-based detector with the typed `TroubleshootingOperationalEffect` mechanism before this pass started — docs/DEFECT_REGISTER.md's DEF-0040 entry describes the LIVE-CORR-3 substring mechanism, which no longer exists in code; treat the current `evidence.py`/`request_execution_policy.py` source as authoritative until DEFECT_REGISTER.md is corrected

6A.15: NOT STARTED

Other known open/planned items remain tracked in DEFECT_REGISTER.md, including:

DEF-0023: evidence-index/specialist-routing alignment gap

DEF-0025: Knowledge inventory/catalog capability gap

DEF-0032: Knowledge narrowing/retrieval identity must preserve version_label

DEF-0033: embedding reconciliation/retry/stale-vector gaps

DEF-0034: negation-blind RequestContract provided-context verification

DEF-0035: Teams write result must distinguish transport success from gateway-declared failure

DEF-0036: Teams write retry/idempotency correlation gap

DEF-0038: only partially fixed

DEF-0045: a verbatim governed command string can reach the user via a TroubleshootingStep's free-text `action` field, which no grounding mechanism inspects, even when the same step's structured `command` field is correctly stripped

DEF-0042: governed source fidelity / prerequisite-step reasoning gap

Do not infer defect status from old milestone prose. Read DEFECT_REGISTER.md before touching any named defect.

16. Roadmap

The canonical detailed sequence is in docs/MASTER_ROADMAP.md and docs/BUILD_SEQUENCE.md.

High-level strategic direction:

finish the POST-6A 6A.12→6A.28 closure sequence

Phase 4H Security Hardening

broader Operational Context integrations: ITSM, alarms/faults, topology/inventory, KPI/observability, Change, Handover

Phase 6B Context Engineering expansion across the full operational context surface

Phase 7 JOC / advanced iterative troubleshooting

Phase 8 controlled autonomy remains a long-term concept only, not an implicitly scheduled next step

Do not reorder roadmap dependencies without an explicit architecture/roadmap decision.

17. Security / production-readiness gaps

SLOPANOC is not yet enterprise-production-ready.

Major remaining concerns include:

Phase 4H threat/trust-boundary hardening

prompt-injection / untrusted-content isolation

enterprise authentication/authorization

per-user Microsoft delegated identity

production service identity and secret configuration

distributed runtime coordination

HA/DR

production observability/alerting/SLOs

load/concurrency validation

final secret-management hardening

broader operational integrations

autonomous remediation controls

Never describe successful DEV or real-stack validation as proof of full production readiness.

18. Frontend product principles

Frontend should remain:

calm

minimal

premium

desktop-first

spacious

low-noise

content-first

conversational, not dashboard-like

Prefer:

whitespace and subtle hierarchy

progressive disclosure

contextual controls

restrained borders

menus/popovers/modals over permanent panels

calm/obvious behavior over decorative complexity

Avoid:

dense enterprise-dashboard aesthetics

excessive cards/toolbars

unnecessary permanent panels

marketing/onboarding patterns inside the core product

The prompt composer must be immediately usable on launch. First prompt creates the conversation; no mandatory “New Chat” step.

Accessibility fundamentals remain required: keyboard access, visible focus, semantic controls, adequate contrast, closable dialogs, keyboard-navigable menus.

19. Mock vs backend-wired UI

Do not infer backend support from visible UI.

Backend-driven/current:

real chat conversations with backendSessionId

saved chat/session hydration

persistent transcripts

attachments/multimodal chat

Teams operations wired through the real backend

provenance/source UI tied to real retrieved evidence

Historically mock/local-state areas may still include portions of:

Projects

Usage

Settings

Connectors beyond the current Teams path

some Skills/product surfaces

Scheduled Tasks

Audit the implementation before claiming any of these surfaces are backend-wired.

20. Working rules for this repository

For any non-trivial task:

Verify repo/branch/working-tree state.

Read authoritative docs and relevant implementation first.

Audit before changing code.

Identify reusable existing mechanisms.

Define strict scope and non-regression invariants.

Make the minimum correct change.

Add focused tests for the defect/capability.

Run relevant regression; run full regression when the change warrants it.

Perform real-stack/live validation when required by the milestone and available.

Update only current canonical documentation; do not rewrite historical facts to pretend old validation used newer behavior.

Report what changed, what was proven, what remains open, and any new defect found.

Stop at the requested boundary. Do not casually expand scope, redesign frozen architecture, change stack/dependencies, or perform unrelated cleanup.

Additional constraints:

No keyword/regex natural-language routing unless an explicit deterministic protocol requires it.

No direct raw DB/storage access from agents.

No unrestricted generic HTTP/SQL/shell tools to agents.

No hidden fallback that weakens a fail-closed safety boundary.

No new dependency/stack/module restructuring without a task-specific reason.

Preserve unrelated user working-tree changes.

21. Memory hygiene — keep this file small

This file is for durable current truth and high-value guardrails only.

Do NOT add:

full milestone closure reports

exact historical pytest counts

one-off session IDs

verbose live-validation transcripts

root-cause narratives already recorded in DEFECT_REGISTER.md

superseded roadmap wording

repeated copies of contracts already canonical elsewhere

long implementation histories for completed milestones

When a milestone closes:

update canonical status in MASTER_ROADMAP.md / BUILD_SEQUENCE.md

record defects in DEFECT_REGISTER.md

update the relevant contract/architecture doc

add only a short durable current-state delta here if Claude genuinely needs it on every coding session

Goal: CLAUDE.md should orient a fresh Claude session quickly, not act as the project's historical archive.