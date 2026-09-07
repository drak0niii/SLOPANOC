"""The Generic Knowledge Management Layer (Phase 5.1).

See docs/KNOWLEDGE_CONTRACT.md for the architecture this package
implements, and docs/TROUBLESHOOTING_STRATEGY.md for the non-negotiable
product principle it exists to eventually serve.

  - domain/ -- the pure domain model: typed knowledge objects, sections,
               source/version/metadata/applicability value objects, and
               the provenance/Knowledge-Context contracts a future
               retrieval/reasoning layer will produce and consume.
               Independent of ADK, Gemini, and every individual agent --
               see domain/__init__.py.

Phase 5.1A (this phase) builds only the domain foundation above: no
storage, no ingestion, no retrieval/embeddings, and no agent wiring exist
in this package yet. Those are later 5.1 sub-phases (5.1C-5.1J).
"""
