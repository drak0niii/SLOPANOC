"""Phase 5.1G: retrieval + ranking -- a CONTEXT REDUCTION boundary.

Answers: "out of all governed knowledge in the repository, which small
set of sections is actually useful to consider for this query, while
respecting currentness and applicability?" See
docs/KNOWLEDGE_CONTRACT.md's Phase 5.1G section for the full
architecture.

  - contracts.py -- `KnowledgeRetrievalQuery` (input), `KnowledgeRetrievalItem`
                     (one bounded, self-describing retrieval result unit),
                     `KnowledgeRetrievalDiagnostic`/
                     `KnowledgeRetrievalDiagnosticReason` (safe, generic,
                     family-level exclusion diagnostics), and
                     `KnowledgeRetrievalResult` (the typed, bounded
                     output).
  - scoring.py   -- `KnowledgeRelevanceScorer` (a minimal `Protocol`)
                     and `TokenOverlapRelevanceScorer`, the one
                     deterministic local reference implementation --
                     normalized lexical token-overlap, no network, no
                     model, no embedding, no synonym dictionary.
  - service.py   -- `KnowledgeRetrievalService`, the orchestration that
                     composes `KnowledgeRepository.list_all()` (5.1F),
                     `governance.versioning.resolve_current_version`
                     (5.1E), `domain.applicability.evaluate_applicability`
                     (5.1B), and a `KnowledgeRelevanceScorer` into one
                     bounded, deterministic `KnowledgeRetrievalResult` --
                     never duplicating currentness or applicability logic
                     itself.

NON-NEGOTIABLE SEPARATION preserved throughout this package (see this
document's own module docstring and docs/KNOWLEDGE_CONTRACT.md's Phase
5.1G section for the full rationale):

  REPOSITORY    = what governed knowledge exists            (5.1F, reused)
  GOVERNANCE    = which version is authoritative/current     (5.1E, reused)
  APPLICABILITY = whether knowledge applies to known facts   (5.1B, reused)
  RETRIEVAL     = which eligible knowledge is relevant       (5.1G, this package)
  PROVENANCE    = proof of exactly which evidence was used   (5.1H, later)
  REASONING     = interpretation of that bounded evidence    (later)

RELEVANCE != AUTHORITY: a section's lexical relevance to a query has no
bearing on which version is governed-current -- that decision belongs
exclusively to `governance/versioning.py`, called here unchanged.
APPLICABILITY != RELEVANCE: `MATCH` from `evaluate_applicability` means
only "not excluded by applicability constraints", never "relevant to
this query" -- a MATCH object with zero lexical overlap is still
excluded from the retrieval result.

CORE ARCHITECTURAL INVARIANTS (same as domain/, ingestion/, processing/,
governance/, and repository/, extended to this package): no dependency
on any individual agent, ADK, Gemini, or concrete cloud/vendor/embedding/
vector SDK anywhere in this package (see
backend/tests/knowledge/test_dependency_boundary.py, which scans this
package too). No `datetime.now()`/`datetime.utcnow()`/`time.time()` call
-- `as_of` is always an explicit caller-supplied input. Retrieval is
READ-ONLY: it never calls `KnowledgeRepository.add`/`replace`, never
mutates a `KnowledgeObject`, and never persists retrieval state.
`repository.list_all()` (5.1F) is the sole corpus source -- this package
never imports `SQLiteKnowledgeRepository` or reads a table directly, and
never sends the full corpus to a model. Document type, source system,
and version label are never used to rank or prioritize one governed
object over another -- only currentness (5.1E), applicability (5.1B),
and lexical relevance (this package) participate in eligibility/ranking.
"""
