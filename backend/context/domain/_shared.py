"""Tiny validation helper, deliberately duplicated (not imported) from
`backend/knowledge/domain/_shared.py`. TELCO Context and Generic Knowledge
are peer context domains (docs/INTELLIGENCE_ARCHITECTURE.md #4) -- this
package must not depend on `backend.knowledge` (see this package's own
`__init__.py`), so this one-function helper is copied rather than
cross-imported, the same pattern this codebase already uses elsewhere
(e.g. `multimodal_turn_context.py`'s `trusted_image_parts_from_content`
deliberately duplicates `MultimodalAgentTool`'s own filter predicate
rather than cross-importing it).
"""
from __future__ import annotations


def require_non_blank(value: str, field_name: str) -> str:
    if not value or not value.strip():
        raise ValueError(f"{field_name} must not be blank")
    return value
