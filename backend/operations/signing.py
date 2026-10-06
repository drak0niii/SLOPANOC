"""Process-scoped HMAC signing for in-memory trust tokens (authority attestations and
authorized read actions).

These tokens are never persisted and never leave the process: persisted records carry only
non-secret binding hashes. A token not minted by this process (hand-built, deserialized, or
edited after issue) fails verification, which makes "hand a policy engine or an execution
adapter an unauthorized command" structurally fail closed.
"""
from __future__ import annotations

import hashlib
import hmac
import secrets
from typing import Any, Mapping

from backend.approval.canonical import canonical_json

_SIGNING_KEY = secrets.token_bytes(32)


def sign(kind: str, fields: Mapping[str, Any]) -> str:
    payload = canonical_json({"kind": kind, "fields": dict(fields)}).encode("utf-8")
    return hmac.new(_SIGNING_KEY, payload, hashlib.sha256).hexdigest()


def verify(kind: str, fields: Mapping[str, Any], signature: str) -> bool:
    if not isinstance(signature, str) or not signature:
        return False
    return hmac.compare_digest(sign(kind, fields), signature)


def stable_hash(value: Mapping[str, Any]) -> str:
    """Non-secret SHA-256 over canonical JSON (for persisted binding hashes)."""
    return hashlib.sha256(canonical_json(dict(value)).encode("utf-8")).hexdigest()


def text_hash(text: str) -> str:
    return hashlib.sha256((text or "").encode("utf-8")).hexdigest()
