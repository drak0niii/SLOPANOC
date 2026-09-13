"""Phase 6A.6 (P11-M06): Context Engineering & Evidence Package.

A deterministic, in-process, shared PLATFORM CAPABILITY -- never an
agent, never an LLM call, never a second Knowledge/Case/TELCO-Context
authority. Converts already-trusted, already-computed inputs (6A.2
TELCO Context state, Case context, session/request context, operational
observations, and 6A.5's own `EvidenceSelectionResult`) into one
versioned, deterministic, serializable `ContextPackage` for a FUTURE
specialist reasoning layer to consume -- this package performs no
reasoning of its own (docs/INTELLIGENCE_ARCHITECTURE.md, "Context
Engineering platform-layer boundary").

See `docs/KNOWLEDGE_CONTRACT.md` §28 and `docs/INTELLIGENCE_ARCHITECTURE
.md` §9/§15 for the full, as-built contract.
"""
