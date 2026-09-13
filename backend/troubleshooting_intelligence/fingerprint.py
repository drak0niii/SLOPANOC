"""Phase 6A.9: deterministic Troubleshooting Intelligence Package content
fingerprinting -- mirrors `backend/context_engineering/fingerprint.py`'s
own discipline exactly.

Given identical LOGICAL content, `compute_intelligence_fingerprint`
always returns the identical SHA-256 hex digest -- proven by a dedicated
repeated-assembly test. `created_at` (a real wall-clock value) and
`content_fingerprint` itself are the ONLY two fields ever excluded.

No model call anywhere in this module.
"""
from __future__ import annotations

import hashlib
import json
from typing import Any

from backend.troubleshooting_intelligence.contracts import TroubleshootingIntelligencePackage

__all__ = ["compute_intelligence_fingerprint"]

_EXCLUDED_FIELDS = frozenset({"created_at", "content_fingerprint"})


def _canonical_json(package: TroubleshootingIntelligencePackage) -> str:
    """`mode="json"` gives a plain, JSON-safe structure; `sort_keys=True`
    makes dict key order irrelevant. List order is preserved exactly as
    the package carries it -- `assembly.py` is responsible for building
    every list in a deterministic order BEFORE this function ever sees
    it (the nested `ContextPackage`'s own fingerprint-relevant ordering
    is already guaranteed by 6A.6's own `assembly.py`; `experience` is
    built in the caller-supplied `experience_records` order, which the
    coordinator populates from `ExperienceMemoryService.query`'s own
    deterministic `recorded_at DESC, experience_id ASC` ordering)."""
    data: dict[str, Any] = package.model_dump(mode="json", exclude=_EXCLUDED_FIELDS)
    return json.dumps(data, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def compute_intelligence_fingerprint(package: TroubleshootingIntelligencePackage) -> str:
    canonical = _canonical_json(package)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()
