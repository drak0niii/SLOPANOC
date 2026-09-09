"""Tiny validation helper shared across `backend/knowledge/domain/`
submodules, split out from `models.py` so `artifacts.py` (which
`models.py` itself now imports, for `KnowledgeObject.artifacts`) can use
it without creating an import cycle. Not part of this package's public
surface -- `models.py` re-exports `require_non_blank` for every existing
external importer, unchanged.
"""
from __future__ import annotations


def require_non_blank(value: str, field_name: str) -> str:
    if not value or not value.strip():
        raise ValueError(f"{field_name} must not be blank")
    return value
