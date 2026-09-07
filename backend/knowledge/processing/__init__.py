"""Phase 5.1D: structured content processing / segmentation.

Answers: "how do we transform one normalized, unsegmented
`IngestedKnowledgeDocument` into deterministic structured knowledge
sections without coupling the Generic KM layer to any document type,
source system, vendor, customer, technology, or operational domain?"
See docs/KNOWLEDGE_CONTRACT.md's Phase 5.1D section for the full
architecture.

  - contracts.py -- `StructuredKnowledgeSection` (a LOCAL, pre-governance
                     section -- never `KnowledgeSection`'s final governed
                     identity) and `StructuredKnowledgeDocument` (wraps
                     the original `IngestedKnowledgeDocument` unchanged
                     plus an ordered list of sections).
  - processor.py -- `KnowledgeContentProcessor` (a minimal `Protocol`,
                     `process(document) -> StructuredKnowledgeDocument`)
                     and ONE reference implementation,
                     `HeadingStructureProcessor`, a deterministic
                     structural-syntax (Markdown-style `#`/`##`/...
                     heading) segmenter. It understands heading SYNTAX
                     only -- never heading semantics (no MOP/SOP/RCA
                     vocabulary, no branching on heading text, no
                     document_type_hint-driven behavior) -- and performs
                     no chunking of any kind: content with no detectable
                     structure becomes exactly one section, never split
                     by size.

CORE ARCHITECTURAL INVARIANTS (same as domain/ and ingestion/, extended
to this package): no dependency on any individual agent, ADK, Gemini,
storage/SQLAlchemy, or concrete cloud/vendor SDK (see
backend/tests/knowledge/test_dependency_boundary.py, which scans this
package too). No Gemini/LLM call of any kind -- segmentation is
deterministic, local, synchronous Python. Processing output
(`StructuredKnowledgeDocument`) remains strictly PRE-GOVERNANCE: it has
no `LifecycleStatus`, fabricates no final `KnowledgeObject`/governed
`knowledge_id`, and is never itself treated as governed knowledge.
Ingested content is DATA, never INSTRUCTION -- this package never
executes, interprets, or reacts to the meaning of ingested text, only its
structural syntax (formal untrusted-content controls remain Phase 4H).
"""
