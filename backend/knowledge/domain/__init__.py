"""The Generic Knowledge Management domain model (Phase 5.1A).

CORE ARCHITECTURAL INVARIANT: this package must remain importable and
testable on its own, independent of every individual agent. It must
never import `google.adk`, `google.genai`, `backend.agents`, or
`backend.tools.teams` -- see test_dependency_boundary.py
(backend/tests/knowledge/), which enforces this as an automated check,
not merely a comment. The question to keep asking while extending this
package: "Can the Knowledge layer still work without knowing Incident
Manager exists?" -- if the answer becomes no, the architecture is too
coupled.

  - enums.py     -- the closed vocabularies: `KnowledgeDocumentType`
                     (MOP/SOP/RCA/KB article/... -- a document TYPE, never
                     a separate architecture per type) and
                     `LifecycleStatus` (Candidate/Approved/Archive -- the
                     state values only; transition rules are 5.1E).
  - models.py    -- the domain value objects and the central
                     `KnowledgeObject` aggregate: `KnowledgeSource`,
                     `KnowledgeVersion`, `KnowledgeMetadata`,
                     `Applicability`, `KnowledgeSection`,
                     `KnowledgeObject`.
  - contracts.py -- the minimal provenance/evidence identity primitive
                     (`KnowledgeEvidenceReference`) and the generic,
                     agent-agnostic output shape a future
                     retrieval/reasoning layer would consume
                     (`KnowledgeContextItem`) -- not retrieval logic, not
                     an ADK tool result, not the future Context
                     Engineering Layer itself.
  - applicability.py -- Phase 5.1B: deterministic applicability
                     evaluation. `ApplicabilityContext` (known
                     operational facts), `ApplicabilityOutcome`
                     (MATCH/PARTIAL_MATCH/NOT_APPLICABLE/UNKNOWN),
                     `ApplicabilityDimensionEvaluation`/
                     `ApplicabilityEvaluation` (structured per-dimension
                     and overall results), and the pure function
                     `evaluate_applicability`. Answers "does this
                     knowledge apply to these known facts?" only --
                     never retrieval, ranking, lifecycle authority, or
                     agent reasoning (see docs/KNOWLEDGE_CONTRACT.md).

See docs/KNOWLEDGE_CONTRACT.md for the full architecture, relationships,
and explicit non-goals of this phase.
"""
