"""Phase 6A.2: the canonical Knowledge Applicability bridge.

Answers: "for which TELCO operational contexts is this governed
knowledge valid, and is that applicability EXPLICIT or merely
UNSPECIFIED?" -- a strictly ADDITIVE extension of the existing, frozen
5.1B `Applicability`/`ApplicabilityContext`/`evaluate_applicability`
(`backend/knowledge/domain/applicability.py`, UNCHANGED by this module),
never a second, competing applicability vocabulary (6A.2 instruction
section 20: "the end state should have ONE canonical applicability
vocabulary, not: A5 applicability + Phase 6A applicability + future
retrieval applicability... all behaving differently").

WHY A NEW MODULE, NOT A CHANGE TO `applicability.py` (audited first, per
instruction): 5.1B's own `evaluate_applicability` treats a dimension
ABSENT from `Applicability.dimensions` as unconstrained -- effectively
"matches regardless of this dimension's value". That is the correct,
unchanged behavior for 5.1B's own narrow original question ("does this
known fact conflict with this document's stated constraints?"). Phase
6A's TELCO/customer-isolation requirement is stricter (instruction
sections 16/17): "Vendor = ANY" (a governance decision that this
knowledge intentionally applies regardless of vendor) must be
DISTINGUISHABLE from "vendor applicability was never reviewed for this
document" -- because a future 6A.4 matching engine must fail closed on
the latter (never silently broaden applicability merely because nobody
filled in a field), while correctly honoring the former. 5.1B's own
`Applicability.dimensions` structurally cannot represent this
distinction -- absence means the same thing (unconstrained) either way.
Rather than change 5.1B's own frozen, already-relied-upon semantics
(which would be a real regression for every existing 5.1G/5.1J caller),
this module adds a SEPARATE, purely additive signal, computed ALONGSIDE
the existing `Applicability` object, that a future 6A.4 matching engine
can consult in addition to (never instead of) `evaluate_applicability`.

`evaluate_applicability` itself is not called, wrapped, or modified by
this module. This module's own `dimension_scope` function is not
currently consulted by anything in the live tool-calling path
(`knowledge_search`/`knowledge_select_evidence`) -- it exists so 6A.4 has
this fail-closed signal ready when it needs it, per instruction section
17 ("6A.2 must make fail-closed behavior possible" -- 6A.4 owns the
actual matching algorithm).
"""
from __future__ import annotations

from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field, model_validator

from backend.knowledge.domain._shared import require_non_blank
from backend.knowledge.domain.models import Applicability, normalize_dimension_key

__all__ = [
    "ApplicabilityScopeKind",
    "KnowledgeApplicabilityProfile",
    "dimension_scope",
    "from_knowledge_object",
]


class ApplicabilityScopeKind(str, Enum):
    """The three-way distinction 5.1B's own `Applicability` cannot
    express on its own (see this module's docstring):

    CONSTRAINED   -- the dimension is present in `Applicability
                     .dimensions` with one or more required values
                     (5.1B's existing, unchanged representation).
    EXPLICIT_ANY  -- governance explicitly decided this knowledge applies
                     regardless of this dimension's value. NEVER the
                     default -- only set when a caller (ingestion/
                     governance, a human reviewer) explicitly says so.
    UNSPECIFIED   -- applicability for this dimension has not been
                     established at all. The SAFE DEFAULT for every
                     dimension not explicitly marked CONSTRAINED or
                     EXPLICIT_ANY -- a future 6A.4 matching engine must
                     treat this as "do not broaden", never as ANY.
    """

    CONSTRAINED = "constrained"
    EXPLICIT_ANY = "explicit_any"
    UNSPECIFIED = "unspecified"


class KnowledgeApplicabilityProfile(BaseModel):
    """Identity + the existing `Applicability` + the new, additive
    EXPLICIT_ANY registry, for one governed knowledge version.

    `knowledge_id`/`version_label` mirror `KnowledgeObject`'s own logical
    identity (`docs/KNOWLEDGE_CONTRACT.md` #15.3 -- `(knowledge_id,
    version_label)`) so a profile always traces back to a real governed
    object, never a free-floating record. `applicability` is the EXACT,
    unmodified `Applicability` object already on that `KnowledgeObject`
    -- never copied field-by-field or reinterpreted. `explicit_any_
    dimensions` is new: a set of NORMALIZED dimension keys (same
    `normalize_dimension_key` 5.1B already uses, so a key here and a key
    in `applicability.dimensions` are always comparable) that governance
    has explicitly declared this knowledge applies to regardless of
    value. A key must not appear in both `applicability.dimensions` and
    `explicit_any_dimensions` -- CONSTRAINED and EXPLICIT_ANY are
    mutually exclusive per dimension, by construction.
    """

    knowledge_id: str
    version_label: str
    applicability: Applicability = Field(default_factory=Applicability)
    explicit_any_dimensions: frozenset[str] = Field(default_factory=frozenset)

    @model_validator(mode="after")
    def _validate(self) -> "KnowledgeApplicabilityProfile":
        require_non_blank(self.knowledge_id, "knowledge_id")
        require_non_blank(self.version_label, "version_label")

        normalized_any: set[str] = set()
        for raw_key in self.explicit_any_dimensions:
            key = normalize_dimension_key(raw_key)
            if not key:
                raise ValueError(f"explicit_any_dimensions entry {raw_key!r} is blank or invalid")
            normalized_any.add(key)
        self.explicit_any_dimensions = frozenset(normalized_any)

        overlap = normalized_any & set(self.applicability.dimensions)
        if overlap:
            raise ValueError(
                f"dimension(s) {sorted(overlap)} cannot be both CONSTRAINED "
                "(present in applicability.dimensions) and EXPLICIT_ANY at the same time"
            )
        return self


def dimension_scope(profile: KnowledgeApplicabilityProfile, dimension: str) -> ApplicabilityScopeKind:
    """The fail-closed-capable scope of one dimension for `profile` --
    pure, deterministic, no database/model call. See `ApplicabilityScopeKind`
    for what each value means and MUST/must never be treated as by a
    future caller.
    """
    key = normalize_dimension_key(dimension)
    if key in profile.applicability.dimensions:
        return ApplicabilityScopeKind.CONSTRAINED
    if key in profile.explicit_any_dimensions:
        return ApplicabilityScopeKind.EXPLICIT_ANY
    return ApplicabilityScopeKind.UNSPECIFIED


def from_knowledge_object(
    *,
    knowledge_id: str,
    version_label: str,
    applicability: Applicability,
    explicit_any_dimensions: Optional[frozenset[str]] = None,
) -> KnowledgeApplicabilityProfile:
    """Bridge constructor from an EXISTING, already-governed
    `KnowledgeObject`'s own identity/`applicability` fields -- takes them
    as explicit keyword arguments (never a live `KnowledgeObject`
    instance) so this module needs no import of `KnowledgeObject` itself,
    keeping the dependency direction exactly as narrow as the data it
    actually needs.

    BACKWARD COMPATIBILITY WITH A5 (instruction section 29, verified):
    every already-ingested A5 `KnowledgeObject` has a real, valid
    `Applicability` (possibly with `dimensions={}` for an unconstrained
    document) and no concept of EXPLICIT_ANY at all -- calling this with
    `explicit_any_dimensions=None` (the default, meaning "none declared")
    against ANY existing A5 `KnowledgeObject.applicability` succeeds
    unconditionally: there is no possible overlap to reject, because the
    explicit-any set is empty. No migration, no re-ingestion, and no
    change to any persisted `KnowledgeObject` row is required for this
    bridge to work against the real, already-persisted TELCO/RAN corpus.
    """
    return KnowledgeApplicabilityProfile(
        knowledge_id=knowledge_id,
        version_label=version_label,
        applicability=applicability,
        explicit_any_dimensions=explicit_any_dimensions or frozenset(),
    )
