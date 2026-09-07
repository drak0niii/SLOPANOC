"""Phase 5.1H: knowledge provenance -- the deterministic VALIDATION
BOUNDARY between a 5.1G `KnowledgeRetrievalResult` and trusted Knowledge
evidence.

Answers exactly one question: "is this claimed evidence real, and
exactly what governed source does it correspond to?" See
docs/KNOWLEDGE_CONTRACT.md's Phase 5.1H section for the full
architecture.

  - contracts.py -- `KnowledgeEvidenceItem` (one validated piece of
                     governed knowledge, built from the authoritative
                     repository object -- never from a retrieval/model
                     claim), `KnowledgeEvidenceSet` (the bounded,
                     duplicate-free, order-preserving aggregate of
                     validated evidence for one retrieval turn),
                     `KnowledgeEvidenceSelectionKey` (the minimal
                     identity a future caller/model may use to SELECT
                     already-validated evidence -- deliberately unable to
                     carry content/source/title/locator), and the
                     `KnowledgeProvenanceError` hierarchy.
  - service.py   -- `KnowledgeProvenanceService.build_evidence_set`
                     (exact repository revalidation of a retrieval
                     result) and `validate_evidence_selection` (a pure
                     function validating a requested selection against an
                     already-bounded `KnowledgeEvidenceSet`, never the
                     wider repository).

CENTRAL TRUST PRINCIPLE (see docs/KNOWLEDGE_CONTRACT.md): THE BACKEND
ESTABLISHES EVIDENCE. THE MODEL MAY LATER SELECT FROM IT. THE MODEL NEVER
CREATES IT. No caller/model can invent `knowledge_id`, `version_label`,
`section_id`, `source_system`, `source_id`, `source_locator`, content,
title, or document type and have that invention accepted as provenance --
every field on a `KnowledgeEvidenceItem` is reconstructed from a
freshly-fetched, exactly-identified `KnowledgeRepository.get(...)` result,
never copied blindly from a `KnowledgeRetrievalItem`.

PROVENANCE IS NOT RETRIEVAL: this package never re-runs relevance
scoring, ranking, applicability evaluation, or current-version
resolution -- those are exclusively 5.1G/5.1B/5.1E's job, called nowhere
in this package. It answers only whether claimed evidence is real and
exactly what it corresponds to.

CORE ARCHITECTURAL INVARIANTS (same as domain/, ingestion/, processing/,
governance/, repository/, and retrieval/, extended to this package): no
dependency on any individual agent, ADK, Gemini, or concrete cloud/
vendor/embedding/vector SDK anywhere in this package (see
backend/tests/knowledge/test_dependency_boundary.py, which scans this
package too). Depends only on the `KnowledgeRepository` Protocol -- never
`SQLiteKnowledgeRepository` or another storage detail. READ-ONLY: never
calls `KnowledgeRepository.add`/`replace`, never mutates a
`KnowledgeObject`, never persists a `KnowledgeEvidenceSet` (no table, no
cache, no global registry, no run-scoped mailbox). The bounded evidence
universe for one retrieval turn is exactly the `KnowledgeRetrievalResult`
supplied to `build_evidence_set` -- this package never queries the
repository beyond the exact `(knowledge_id, version_label)` pairs that
result names, and selection validation never reaches back into the
repository at all. No content is rewritten, summarized, truncated, or
hashed -- exact governed section content is the authoritative evidence
text, always.
"""
