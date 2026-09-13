"""Phase 6A.9: Troubleshooting Intelligence Assembly.

A deterministic, in-process, pure platform capability -- mirroring
`backend/context_engineering/`'s own established shape exactly -- that
combines an already-assembled 6A.6 `ContextPackage`, an already-resolved
6A.7 `SkillDefinition` (at most one, deterministically selected by a
caller), and already-queried 6A.8 `ExperienceRecord`s into one versioned,
fingerprinted `TroubleshootingIntelligencePackage` for the Troubleshooting
Manager specialist reasoning boundary (`backend/agents/
troubleshooting_manager/`).

This package performs NO reasoning, NO LLM call, and NO database access
of any kind -- Skill resolution (registry lookup + readiness/
applicability evaluation) and Experience Memory retrieval both happen
in the impure coordinator layer (`backend/agents/troubleshooting_manager/
skill_resolution.py`/`experience_support.py`), before
`assemble_troubleshooting_intelligence` is ever called.
"""
