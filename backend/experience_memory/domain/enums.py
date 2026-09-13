"""Phase 6A.8 closed enums: Experience type, lifecycle, source origin
(the deterministic admission-trust classification), and admission
outcome.

Deliberately a MINIMAL ontology (§18: "do not create a large
ontology... actual types must be justified by existing source data"):
`ExperienceType` has exactly the four categories the milestone's own
audit found a plausible real producer for (Case resolution/context
items, executed write actions, operator-authored feedback, generic
observed facts) -- none of them wired as a producer in this milestone
(see `docs/KNOWLEDGE_CONTRACT.md` §30.15 -- production writers = 0).

`ExperienceSourceOrigin` is the actual admission-trust switch (§20-22):
a CLOSED enum split into three disjoint groups by
`backend.experience_memory.domain.admission`'s `_ALLOWED_ORIGINS`/
`_DENIED_ORIGINS` sets -- ALLOWED (sufficiently trustworthy, may become
ACCEPT), DENIED (never automatically admissible, always REJECT), and
`UNSPECIFIED` (insufficient trust information, always INDETERMINATE).
The three groups mirror this milestone's own §21/§30/§31 worked
examples almost verbatim, by design, for closure-report traceability.
"""
from __future__ import annotations

from enum import Enum

__all__ = [
    "ExperienceType",
    "ExperienceLifecycle",
    "ExperienceSourceOrigin",
    "AdmissionOutcome",
]


class ExperienceType(str, Enum):
    """§18: the minimal useful category set."""

    OBSERVATION = "observation"
    ACTION_OUTCOME = "action_outcome"
    CASE_RESOLUTION = "case_resolution"
    OPERATOR_FEEDBACK = "operator_feedback"


class ExperienceLifecycle(str, Enum):
    """§40: deliberately NOT Knowledge's CANDIDATE/APPROVED/ARCHIVE --
    a durable Experience record is either still eligible for default
    retrieval (`ACTIVE`) or has been administratively withdrawn without
    being physically deleted (`INVALIDATED`, §41: "avoid making history
    rewrite itself" -- invalidation is itself an auditable event, not an
    erasure)."""

    ACTIVE = "active"
    INVALIDATED = "invalidated"


class ExperienceSourceOrigin(str, Enum):
    """The ORIGIN of the underlying source event -- distinct from
    `ExperienceType` (what KIND of experience this is) and from the
    record's own fixed `source_class = "EXPERIENCE"` trust TIER (§19).
    This is the field `evaluate_admission` (`admission.py`) actually
    switches on."""

    # ALLOWED (§21's own explicit list) -- sufficiently trustworthy that
    # a candidate carrying one of these MAY be ACCEPTed (still subject
    # to the candidate's other structural validation).
    OBSERVED_CASE_OUTCOME = "observed_case_outcome"
    EXPLICIT_CASE_RESOLUTION = "explicit_case_resolution"
    EXECUTED_ACTION_RESULT = "executed_action_result"
    VALIDATED_OPERATOR_FEEDBACK = "validated_operator_feedback"
    TRUSTED_EXTERNAL_SYSTEM_STATE = "trusted_external_system_state"

    # DENIED (§21/§30/§31's own explicit "do not automatically admit"
    # list) -- always REJECT, regardless of any other candidate field.
    LLM_SPECULATION = "llm_speculation"
    GENERATED_RECOMMENDATION = "generated_recommendation"
    UNEXECUTED_ACTION = "unexecuted_action"
    UNVERIFIED_RCA = "unverified_rca"
    ASSISTANT_ANSWER = "assistant_answer"
    USER_FREE_FORM_STATEMENT = "user_free_form_statement"
    RETRIEVAL_RANKING = "retrieval_ranking"
    SEMANTIC_SIMILARITY = "semantic_similarity"
    MODEL_CONFIDENCE = "model_confidence"

    # §22: insufficient trust information to decide either way --
    # always INDETERMINATE, never silently promoted to ACCEPT.
    UNSPECIFIED = "unspecified"


class AdmissionOutcome(str, Enum):
    """§20: the three-way, deterministic admission result."""

    ACCEPT = "accept"
    REJECT = "reject"
    INDETERMINATE = "indeterminate"
