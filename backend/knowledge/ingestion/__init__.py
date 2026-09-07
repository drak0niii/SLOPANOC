"""Phase 5.1C: the Generic Ingestion Boundary.

Answers: "how can knowledge from ANY future source enter the Generic KM
pipeline through one stable, source-agnostic boundary?" -- never "how do
we read from SharePoint/GCS/Drive/..." (a future, separate concern
outside this package entirely). See docs/KNOWLEDGE_CONTRACT.md's Phase
5.1C section for the full architecture.

  - contracts.py -- `IngestedKnowledgeDocument`: the normalized,
                     UNSEGMENTED output every future source adapter must
                     produce, regardless of where it came from. Reuses
                     `KnowledgeSource`/`KnowledgeMetadata`/
                     `Applicability`/`KnowledgeVersion`/
                     `KnowledgeDocumentType` from `domain/` rather than
                     forking equivalents.
  - adapters.py  -- `KnowledgeSourceAdapter`: the minimal structural
                     Protocol a future source adapter implements. No
                     concrete adapter (SharePoint/GCS/Drive/local file/
                     PDF/DOCX/...) exists in this package -- those are
                     later, source-specific work outside Generic KM.

CORE ARCHITECTURAL INVARIANTS (same as domain/, extended to this
package): no dependency on any individual agent, ADK, Gemini, Power
Automate, or Teams tools (see
backend/tests/knowledge/test_dependency_boundary.py, which scans this
package too). No hardcoded source-system vocabulary anywhere here --
`KnowledgeSource.source_system` remains an arbitrary non-empty string;
there is no `SUPPORTED_SOURCES` set, `SourceSystemEnum`, or
`if source_system == "..."` branch in this package, and there never
should be. INGESTED != APPROVED: nothing in this package assigns
`LifecycleStatus` to a document, and ingested content is DATA, not
INSTRUCTION -- it never becomes executable/model instruction merely
because it entered the system (formal untrusted-content controls are
Phase 4H, not this phase).
"""
