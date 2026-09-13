"""Phase 6A.6: deterministic Context Package content fingerprinting
(§26/§27).

Given identical LOGICAL content, `compute_content_fingerprint` always
returns the identical SHA-256 hex digest -- proven by a dedicated
repeated-assembly test, not merely asserted. `created_at` (a real
wall-clock value) and `content_fingerprint` itself are the ONLY two
`ContextPackage` fields ever excluded -- everything else that affects
what a future reasoning layer would actually see is included.

No model call anywhere in this module.
"""
from __future__ import annotations

import hashlib
import json
from typing import Any

from backend.context_engineering.contracts import ContextPackage

__all__ = ["compute_content_fingerprint"]

_EXCLUDED_FIELDS = frozenset({"created_at", "content_fingerprint"})


def _canonical_json(package: ContextPackage) -> str:
    """`mode="json"` gives a plain, JSON-safe structure (datetimes/enums
    already stringified); `sort_keys=True` makes key ORDER irrelevant;
    a compact separator avoids incidental whitespace differences. List
    ORDER within the dump is preserved exactly as `ContextPackage`
    itself carries it -- `assembly.py` is responsible for building every
    list in a deterministic order (dimensions sorted by enum value,
    evidence items kept in 6A.5's own already-deterministic selected
    order) BEFORE this function ever sees it; this function performs no
    sorting of list contents itself, only of dict keys.
    """
    data: dict[str, Any] = package.model_dump(mode="json", exclude=_EXCLUDED_FIELDS)
    return json.dumps(data, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def compute_content_fingerprint(package: ContextPackage) -> str:
    canonical = _canonical_json(package)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()
