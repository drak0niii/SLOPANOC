"""Phase 5.1E: versioning + lifecycle governance.

Answers: "when does structured knowledge become governed knowledge, what
lifecycle state is it in, and which version is authoritative/current at
a given time?" See docs/KNOWLEDGE_CONTRACT.md's Phase 5.1E section for
the full architecture.

Three explicit, separated responsibilities (never blurred into one):

  - contracts.py  -- domain error types (raised on structurally invalid
                      input -- a mixed-knowledge_id family, duplicate
                      version labels, an unknown supersession reference,
                      a supersession cycle, or an invalid lifecycle
                      transition) and the typed
                      `CurrentVersionResolution` result (RESOLVED /
                      NOT_FOUND / AMBIGUOUS -- an expected, non-error
                      outcome of legitimate governance data, never an
                      exception).
  - service.py    -- (A) GOVERNED MATERIALIZATION: `materialize_candidate`
                      turns one `StructuredKnowledgeDocument` into a
                      `KnowledgeObject` in `CANDIDATE` state, given
                      EXPLICITLY supplied final knowledge_id/document_type/
                      version -- ingestion hints are never silently
                      promoted to governance authority. (B) LIFECYCLE
                      TRANSITIONS: `transition_lifecycle`/
                      `approve_version`/`archive_version` enforce the one
                      authoritative CANDIDATE -> APPROVED -> ARCHIVE state
                      machine, as pure functions returning a new
                      `KnowledgeObject` (never mutating the original,
                      never persisting, never auto-archiving anything
                      else).
  - versioning.py -- (C) VERSION GOVERNANCE: `is_effective`,
                      `resolve_current_version`, and
                      `resolve_supersession_chain` build a deterministic,
                      explicit supersession graph from
                      `KnowledgeVersion.supersedes`/`superseded_by`
                      declarations only -- version LABELS are opaque
                      identifiers, never compared, sorted, or parsed for
                      ordering.

CORE ARCHITECTURAL INVARIANTS (same as domain/, ingestion/, and
processing/, extended to this package): no dependency on any individual
agent, ADK, Gemini, storage/SQLAlchemy, or concrete cloud/vendor SDK (see
backend/tests/knowledge/test_dependency_boundary.py, which scans this
package too). No `datetime.now()`/`datetime.utcnow()`/`time.time()` call
anywhere -- every timestamp this package could ever use is an explicit
caller-supplied input, so the same inputs always produce the same output.
Document type (MOP/SOP/RCA/KB/...) never alters lifecycle or version
semantics -- there is exactly one governance implementation, never a
per-type variant. Applicability evaluation (5.1B) is never invoked here;
"CURRENT" and "APPLICABLE" remain distinct concerns.
"""
