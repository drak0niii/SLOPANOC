"""Deterministic canonical serialization and hashing for approval
payloads.

This is the exact-payload-binding mechanism (instruction section 6): an
approval authorizes one specific `(operation, payload)` pair, and this
module is the only place that decides what "the same payload" means.
`json.dumps(..., sort_keys=True)` sorts keys recursively at every nesting
level, so key order -- at the top level or nested -- never affects the
hash, while any actual value change (added field, removed field, changed
field, changed nested field) always does.
"""
from __future__ import annotations

import hashlib
import json
from typing import Any, Mapping


def canonical_json(value: Mapping[str, Any]) -> str:
    """Stable JSON serialization: sorted keys (recursively), deterministic
    separators, no incidental whitespace. Not meant to be human-displayed
    -- only hashed.
    """
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def compute_payload_hash(operation: str, payload: Mapping[str, Any]) -> str:
    """SHA-256 hex digest binding `operation` and the exact `payload`
    together -- changing either changes the hash. Encoded as UTF-8 before
    hashing, per instruction.
    """
    canonical = canonical_json({"operation": operation, "payload": payload})
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()
