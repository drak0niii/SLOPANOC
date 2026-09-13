"""Phase 6A.7 (P11-M07): the Skills Framework.

A **Skill** answers "How do I perform this operational method?" -- a
reusable, governed, deterministic, version-controlled operational
METHODOLOGY, never an agent, never Knowledge, never a Tool, never
memory, never a free-form prompt, never a workflow engine, never an
autonomous executor (`docs/AGENT_CONTRACT.md` §3a, `docs/
INTELLIGENCE_ARCHITECTURE.md` §11 -- both restated, not contradicted,
by this package).

This package implements ONLY: the canonical typed `SkillDefinition`
contract, deterministic semantic versioning, a deterministic content
fingerprint, a strict fail-closed declarative (YAML) loader, a
deterministic registry, deterministic readiness evaluation against an
already-assembled 6A.6 `ContextPackage`, and deterministic, typed-only
applicability evaluation. It does NOT select a Skill, execute a Skill,
execute a Tool, call an LLM, perform runtime capability discovery, or
implement any workflow/reasoning runtime -- see `docs/KNOWLEDGE_
CONTRACT.md` §29 for the full, as-built contract.
"""
