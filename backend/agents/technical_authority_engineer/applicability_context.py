"""Server-owned applicability context continuity for the Technical Authority Engineer.

Applicability facts proposed by the calling model (`known_applicability_facts`) are
NOT trusted as-is. Each proposed (dimension, value) pair is reconciled
deterministically before it can reach `ApplicabilityContext`:

1. Literal statement: the value must appear literally in the current user message,
   or already be a confirmed fact from an earlier turn of this session. A value the
   model inferred (e.g. from a tool name) is never asserted -- it is reported as
   unconfirmed so the user can be asked.
2. Governed vocabulary: the dimension/value pair is checked against the applicability
   metadata of APPROVED governed knowledge. A value governed metadata only knows under
   exactly one OTHER dimension is re-keyed to that dimension (e.g. an account name the
   model labelled as a vendor). A value that governed metadata does not know for a
   governed dimension is not asserted (it could only produce a spurious
   NOT_APPLICABLE exclusion); a value known under several dimensions is ambiguous.

Confirmed facts accumulate across turns in session state (per-dimension union), so a
clarification turn never discards facts supplied earlier. Nothing here evaluates
applicability: MATCH/UNKNOWN/PARTIAL_MATCH always come from `evaluate_applicability`
during retrieval. No dimension names or values are hardcoded.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Any, Mapping, Optional

from backend.knowledge.domain.applicability import ApplicabilityContext
from backend.knowledge.domain.enums import LifecycleStatus
from backend.knowledge.domain.models import normalize_dimension_key, normalize_dimension_value

logger = logging.getLogger(__name__)

CONFIRMED_APPLICABILITY_FACTS_STATE_KEY = "confirmed_applicability_facts"

GovernedVocabulary = dict[str, set[str]]
"""normalized dimension key -> normalized values used by APPROVED governed knowledge."""


@dataclass(frozen=True)
class UnconfirmedApplicabilityFact:
    dimension: str
    value: str
    reason: str


def _clean_facts(raw: Any) -> dict[str, list[str]]:
    if not isinstance(raw, Mapping):
        return {}
    cleaned: dict[str, list[str]] = {}
    for key, values in raw.items():
        if not isinstance(key, str):
            continue
        if isinstance(values, str):
            values = [values]
        if not isinstance(values, (list, tuple)):
            continue
        vals = [v.strip() for v in values if isinstance(v, str) and v.strip()]
        if normalize_dimension_key(key) and vals:
            cleaned.setdefault(key, []).extend(vals)
    try:
        return ApplicabilityContext(dimensions=cleaned).dimensions
    except Exception:
        return {}


def read_confirmed_applicability_facts(state: Any) -> dict[str, list[str]]:
    try:
        raw = state.get(CONFIRMED_APPLICABILITY_FACTS_STATE_KEY) if state is not None else None
    except Exception:
        raw = None
    return _clean_facts(raw)


def merge_applicability_facts(*fact_maps: Mapping[str, list[str]]) -> dict[str, list[str]]:
    """Per-dimension union; earlier confirmed values are never dropped."""
    merged: dict[str, list[str]] = {}
    for facts in fact_maps:
        for key, values in _clean_facts(facts).items():
            merged.setdefault(key, []).extend(values)
    return _clean_facts(merged)


def build_governed_vocabulary(knowledge_objects: list[Any]) -> GovernedVocabulary:
    vocabulary: GovernedVocabulary = {}
    for obj in knowledge_objects:
        if getattr(obj, "lifecycle_status", None) != LifecycleStatus.APPROVED:
            continue
        dims = getattr(getattr(obj, "applicability", None), "dimensions", None) or {}
        for key, values in dims.items():
            bucket = vocabulary.setdefault(normalize_dimension_key(key), set())
            bucket.update(normalize_dimension_value(v) for v in values)
    return vocabulary


async def load_governed_vocabulary() -> Optional[GovernedVocabulary]:
    """Returns None when governed metadata cannot be read (callers fail closed)."""
    try:
        from backend.tools.knowledge.runtime import get_knowledge_repository

        return build_governed_vocabulary(await get_knowledge_repository().list_all())
    except Exception as exc:
        logger.warning("Governed applicability vocabulary unavailable: %s", type(exc).__name__)
        return None


def _value_stated(value: str, text: str) -> bool:
    if not value or not text:
        return False
    pattern = rf"(?<![0-9A-Za-z]){re.escape(value.strip())}(?![0-9A-Za-z])"
    return re.search(pattern, text, re.IGNORECASE) is not None


def reconcile_applicability_facts(
    proposed: Any,
    user_text: str,
    confirmed: Mapping[str, list[str]],
    vocabulary: Optional[GovernedVocabulary],
) -> tuple[dict[str, list[str]], list[UnconfirmedApplicabilityFact]]:
    """Returns (accepted facts, unconfirmed facts). See module docstring for the rules."""
    confirmed_norm = {
        key: {normalize_dimension_value(v) for v in values} for key, values in _clean_facts(confirmed).items()
    }
    confirmed_values = set().union(*confirmed_norm.values()) if confirmed_norm else set()
    accepted: dict[str, list[str]] = {}
    unconfirmed: list[UnconfirmedApplicabilityFact] = []

    for dim, values in _clean_facts(proposed).items():
        for value in values:
            norm_value = normalize_dimension_value(value)
            if norm_value in confirmed_norm.get(dim, set()):
                continue  # already confirmed under this dimension
            if not (_value_stated(value, user_text) or norm_value in confirmed_values):
                unconfirmed.append(UnconfirmedApplicabilityFact(dim, value, "not stated by the user"))
                continue
            if vocabulary is None:
                unconfirmed.append(UnconfirmedApplicabilityFact(dim, value, "governed applicability metadata unavailable"))
                continue
            if norm_value in vocabulary.get(dim, set()):
                accepted.setdefault(dim, []).append(value)
                continue
            owners = sorted(key for key, known in vocabulary.items() if norm_value in known)
            if len(owners) == 1:
                accepted.setdefault(owners[0], []).append(value)
            elif len(owners) > 1:
                unconfirmed.append(UnconfirmedApplicabilityFact(dim, value, "ambiguous across governed dimensions"))
            elif dim in vocabulary:
                unconfirmed.append(UnconfirmedApplicabilityFact(dim, value, f"not a recognized {dim} value in governed procedures"))
            else:
                accepted.setdefault(dim, []).append(value)  # dimension no governed procedure constrains
    return _clean_facts(accepted), unconfirmed


def current_user_text(tool_context: Any) -> str:
    content = getattr(tool_context, "user_content", None)
    parts = getattr(content, "parts", None) if content is not None else None
    if not parts:
        return ""
    return " ".join(p.text for p in parts if getattr(p, "text", None))


def build_applicability_clarification(
    confirmed: Mapping[str, list[str]],
    missing_dimensions: list[str],
    unconfirmed: list[UnconfirmedApplicabilityFact],
) -> Optional[dict[str, Any]]:
    """Targeted clarification from the deterministic applicability result. Only the
    dimensions the server evaluation reported as unresolved are requested."""
    missing = [d for d in dict.fromkeys(missing_dimensions) if d]
    if not missing:
        return None
    known = "; ".join(f"{dim} = {', '.join(vals)}" for dim, vals in confirmed.items())
    lines = []
    if known:
        lines.append(f"Confirmed context so far: {known}.")
    lines.append(
        "To validate the applicable governed procedure I still need: " + ", ".join(missing) + "."
    )
    for fact in unconfirmed:
        if fact.dimension in missing:
            lines.append(f"'{fact.value}' was not accepted as {fact.dimension} ({fact.reason}); please confirm the {fact.dimension}.")
    lines.append(
        "Once the procedure is confirmed applicable, the governed command for the next diagnostic step "
        "can be provided if it passes authorization."
    )
    return {
        "confirmed_facts": {k: list(v) for k, v in confirmed.items()},
        "missing_dimensions": missing,
        "unconfirmed_facts": [fact.__dict__ for fact in unconfirmed],
        "text": " ".join(lines),
    }
