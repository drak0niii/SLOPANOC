"""Governance domain errors and the current-version resolution result.

Two different failure/outcome shapes, deliberately kept distinct:

  - A `KnowledgeGovernanceError` subclass is raised for structurally
    INVALID input -- a version family mixing more than one knowledge_id,
    duplicate version labels, a supersession reference to a version not
    present in the supplied family, a supersession cycle, or an illegal
    lifecycle transition. These are caller/data errors, never a
    legitimate governance outcome -- fail closed, never silently
    recovered from.
  - `CurrentVersionResolution` is a plain, typed VALUE RESULT (the same
    "expected outcome, not an exception" pattern already used elsewhere
    in this backend, e.g. `backend/approval/service.py`'s
    `ProposalResult`) -- NOT_FOUND and AMBIGUOUS are both legitimate,
    common results of a perfectly valid version family, never errors.
"""
from __future__ import annotations

from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field

from backend.knowledge.domain.models import KnowledgeObject


class KnowledgeGovernanceError(ValueError):
    """Base class for every deterministic governance domain error this
    package raises. Never constructed from a raw exception message or
    any value that could leak internal implementation detail.
    """


class InvalidLifecycleTransitionError(KnowledgeGovernanceError):
    """Raised by `transition_lifecycle` (service.py) when the requested
    target status is not reachable from the object's current
    `lifecycle_status` under the one authoritative CANDIDATE -> APPROVED
    -> ARCHIVE state machine (§13/§14) -- includes same-state,
    backwards, and skipped transitions.
    """


class InvalidVersionFamilyError(KnowledgeGovernanceError):
    """Base class for a structurally invalid `list[KnowledgeObject]`
    version family supplied to `versioning.py`'s operations -- see the
    more specific subclasses below for the exact reason.
    """


class MixedKnowledgeIdError(InvalidVersionFamilyError):
    """The supplied version family contains more than one distinct
    `knowledge_id` -- every operation in versioning.py requires the
    caller to supply versions of exactly one logical knowledge object.
    """


class DuplicateVersionLabelError(InvalidVersionFamilyError):
    """Two or more `KnowledgeObject`s in the supplied family share the
    same `version.label` -- within one knowledge_id/version family, a
    label must uniquely identify a version.
    """


class UnknownSupersessionReferenceError(InvalidVersionFamilyError):
    """A `supersedes`/`superseded_by` declaration names a version label
    that is not present anywhere in the supplied family -- fail closed
    rather than assume what the missing version means; a future
    repository (5.1F) is responsible for supplying the complete family.
    """


class SupersessionCycleError(InvalidVersionFamilyError):
    """The declared supersession relationships form a cycle (direct,
    e.g. A supersedes B and B supersedes A, or transitive, e.g.
    A -> B -> C -> A) -- currentness can never be resolved from a cyclic
    graph, so this is rejected outright rather than attempted.
    """


class CurrentVersionResolutionStatus(str, Enum):
    """The three possible outcomes of `resolve_current_version` -- a
    closed set standing in deliberately for a bare
    `Optional[KnowledgeObject]`, which would be unable to distinguish
    "nothing is currently authoritative" from "more than one candidate
    is currently authoritative and governance data does not say which."
    """

    RESOLVED = "resolved"
    NOT_FOUND = "not_found"
    AMBIGUOUS = "ambiguous"


class CurrentVersionResolution(BaseModel):
    """The result of `resolve_current_version` for one version family at
    one `as_of` instant.

    `current` is set only when `status` is `RESOLVED`. `candidates`
    lists the competing version labels for `AMBIGUOUS` (more than one),
    the single resolved label for `RESOLVED`, or is empty for
    `NOT_FOUND` -- always sorted for deterministic, order-independent
    output regardless of the input list's own order.
    """

    status: CurrentVersionResolutionStatus
    current: Optional[KnowledgeObject] = None
    candidates: list[str] = Field(default_factory=list)
