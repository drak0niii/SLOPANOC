"""Phase 5.1I: the generic AGENT-FACING TOOL BOUNDARY -- the layer that
composes the already-frozen 5.1G retrieval and 5.1H provenance
capabilities into one safe, bounded operation a future agent can call
without knowing anything about repository, governance, retrieval, or
provenance implementation details.

Answers exactly one question: "how can any future agent safely query
governed knowledge through one generic tool boundary and receive only
bounded, provenance-validated knowledge?" See docs/KNOWLEDGE_CONTRACT.md's
Phase 5.1I section for the full architecture.

  - contracts.py -- `KnowledgeSearchToolRequest` (the ONLY model-
                     controlled input: `query_text` + `limit`, a CLOSED
                     contract -- unknown fields rejected),
                     `KnowledgeToolExecutionContext` (the trusted,
                     backend-supplied `as_of` + `ApplicabilityContext` --
                     never model input, also closed), `KnowledgeToolEvidenceItem`
                     (one model-safe, provenance-backed evidence item),
                     `KnowledgeSearchDiagnostic` (a safe, generic family-
                     exclusion diagnostic), `KnowledgeSearchAgentPayload`
                     (the ONLY object intended to reach a model),
                     `KnowledgeSearchExecutionResult` (the internal,
                     Python-only bridge holding both the agent payload
                     AND the trusted `KnowledgeEvidenceSet`), and the
                     `KnowledgeToolError`/`KnowledgeToolConsistencyError`
                     hierarchy.
  - service.py   -- `KnowledgeToolService`, which orchestrates
                     `KnowledgeRetrievalService.retrieve` (5.1G) and
                     `KnowledgeProvenanceService.build_evidence_set`
                     (5.1H) unchanged, correlates their outputs by
                     deterministic `(knowledge_id, version_label,
                     section_id)` identity (failing closed on any
                     mismatch), and exposes `validate_selection` as a
                     thin pass-through to 5.1H's own
                     `validate_evidence_selection`.

INITIAL CAPABILITY, DELIBERATELY SINGULAR: this phase implements exactly
one agent-facing operation, `KnowledgeToolService.search`. No
`knowledge_get`/`knowledge_get_current`/`knowledge_get_section`/
`knowledge_browse`/`knowledge_latest` exists -- `knowledge_search`
already provides the correct bounded path through currentness,
applicability, relevance, and provenance; additional capabilities are
introduced later only when a concrete consumer (5.1J+) actually needs
them, never speculatively.

THE CRITICAL TRUST SPLIT this package exists to enforce:

    KnowledgeSearchExecutionResult
        agent_payload   -- may be serialized and shown to a model
        evidence_set    -- trusted Python state, NOT model input,
                           NOT model authority, constructed by 5.1H

A model may later select evidence by minimal identity
(`KnowledgeEvidenceSelectionKey`, 5.1H) via `KnowledgeToolService.validate_selection`,
which validates strictly against `execution_result.evidence_set` -- the
model can never resubmit or recreate an `EvidenceSet` as authority, and a
real repository section that was simply never part of this execution's
bounded evidence is rejected identically to a fabricated one.

NO GLOBAL EVIDENCE REGISTRY: evidence is threaded through this package's
own explicit `KnowledgeSearchExecutionResult` return value -- no global
dict, module singleton, `ContextVar` mailbox, run-id registry, or
session/database evidence store exists anywhere in this package. 5.1J
decides how a concrete agent turn carries that execution result forward.

NO ADK/GEMINI WIRING: this phase defines the generic tool CONTRACT and
SERVICE only -- it is agent-READY without being agent-SPECIFIC. No
`google.adk`/`google.genai` import, no `AgentTool`, no
`backend/agents/**` change, and no prompt/instruction change exists
anywhere in this package; that is exclusively 5.1J's job.

CORE ARCHITECTURAL INVARIANTS (same as every other KM package, extended
here): no dependency on any individual agent, ADK, Gemini, concrete
cloud/vendor/embedding/vector SDK, or the frontend (see
backend/tests/knowledge/test_dependency_boundary.py, which scans this
package too). No `SQLiteKnowledgeRepository`/SQLAlchemy/sqlite3 coupling
-- this package only ever consumes already-constructed
`KnowledgeRetrievalService`/`KnowledgeProvenanceService` instances. No
currentness (`resolve_current_version`), applicability
(`evaluate_applicability`), or relevance-scoring
(`KnowledgeRelevanceScorer`/`TokenOverlapRelevanceScorer`) logic is
duplicated or re-invoked here -- 5.1G/5.1B/5.1E already own those
decisions, called nowhere in this package. No provenance construction
(`KnowledgeEvidenceReference`/`KnowledgeEvidenceItem`/`KnowledgeEvidenceSet`)
happens here either -- `KnowledgeProvenanceService.build_evidence_set`
(5.1H) is the only source of trusted evidence. Read-only: never calls
`repository.add`/`replace`, directly or indirectly. No content
summarization, rewriting, normalization, or truncation of any kind --
exact provenance-validated section content is always what reaches the
agent payload; bounding the NUMBER of returned sections (via
`request.limit`, capped at a small technical maximum) is the only
control this package exercises.
"""
