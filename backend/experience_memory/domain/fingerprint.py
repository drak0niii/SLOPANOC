"""Phase 6A.8: deterministic Experience identity and content
fingerprinting (§16/§17/§43).

CORRECTED TWICE -- final source-namespace corrective pass (this
docstring reflects the CURRENT, final basis; see that pass's own closure
report for the full audit trail): identity now includes
`source_namespace` as the stable source NAMESPACE component --
`source_origin` (used by an EARLIER corrective pass as a namespace
stand-in) was found, on further audit, to conflate two genuinely
different concerns (§2 of the final corrective pass):

- `source_namespace` answers "which PRODUCER/SYSTEM namespace owns this
  `source_event_id`?" -- identity/dedup/replay/provenance.
- `source_origin` answers "what TRUST/ADMISSION class does this
  candidate come from?" -- consumed ONLY by `admission.py`'s
  `evaluate_admission`, never part of identity.

Two DISTINCT deterministic hashes, never conflated:

- `compute_experience_id` -- the narrow IDENTITY hash
  (`owner_id`+`experience_type`+`source_namespace`+`source_event_id`)
  that makes admission idempotent (§17): the same producer event,
  observed twice, always produces the same `experience_id`, so a
  repeated write can be detected and short-circuited (see `sqlalchemy/
  service.py::record_experience`) BEFORE any duplicate row could ever
  be created. `source_origin` is deliberately EXCLUDED from this basis:
  two submissions of the SAME producer event asserting a DIFFERENT
  `source_origin` resolve to the SAME `experience_id` -- identity
  follows the producer EVENT, not whichever trust classification a
  given submission happened to carry (audited; no proven reason found
  to instead make admission classification part of identity). Source
  TIMESTAMPS are deliberately NEVER part of this basis -- not a
  collision-safe identity input.
- `compute_experience_content_fingerprint` -- a broader CONTENT hash
  over the entire logical `ExperienceCandidate` (§43, including
  `source_namespace` automatically, since it is now a normal candidate
  field): useful for audit/dedup-support/change-detection, deliberately
  never used as an authority signal and never used as the primary key.
"""
from __future__ import annotations

import hashlib
import json

from backend.experience_memory.domain.models import ExperienceCandidate

__all__ = ["compute_experience_id", "compute_experience_content_fingerprint"]


def compute_experience_id(owner_id: str, experience_type: str, source_namespace: str, source_event_id: str) -> str:
    """Deterministic SHA-256 hex digest -- the SAME four inputs always
    produce the SAME id, regardless of any other candidate field
    (including `source_origin`, which is deliberately NOT an input
    here). Two candidates sharing `owner_id`+`experience_type`+
    `source_event_id` but differing in `source_namespace` deliberately
    produce DIFFERENT ids -- a same-looking event id from a different
    producer namespace is never treated as the same Experience."""
    basis = f"{owner_id}\n{experience_type}\n{source_namespace}\n{source_event_id}"
    return hashlib.sha256(basis.encode("utf-8")).hexdigest()


def compute_experience_content_fingerprint(candidate: ExperienceCandidate) -> str:
    """SHA-256 over the candidate's own canonical JSON (`sort_keys=True`,
    compact separators) -- excludes nothing beyond what `ExperienceCandidate`
    already excludes (no `recorded_at`/`experience_id`/`lifecycle` exist
    on a candidate at all, since those are record-only, persistence-time
    concerns, §43's own "exclude record insertion timestamp, random IDs,
    processing latency"). `source_namespace` IS included here (it is a
    normal, real candidate field, not a persistence-time concern)."""
    payload = candidate.model_dump(mode="json")
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()
