"""Phase 5.1B: deterministic applicability evaluation.

Answers exactly one question: "given a governed `KnowledgeObject`'s
`Applicability` constraints and some known operational facts, does this
knowledge apply?" -- never "is this knowledge semantically relevant" (a
later, separate 5.1G retrieval/ranking concern) and never a business
decision about lifecycle authority or version currency (5.1E). See
docs/KNOWLEDGE_CONTRACT.md's Phase 5.1B section for the full semantics.

Everything here is pure, deterministic, synchronous domain logic: no
Gemini/ADK call, no database, no network, no agent `ToolContext`, no
hidden global state. `evaluate_applicability` never asks a model "does
this apply?" -- that would violate the same independence invariant 5.1A
established (see test_dependency_boundary.py, which covers this module
too since it scans every file under backend/knowledge/domain/).

DIMENSION-AGNOSTIC BY DESIGN: applicability dimension names are open and
domain-agnostic. This module knows nothing about what a dimension
semantically represents -- it performs only deterministic syntactic
normalization and matching over whatever keys/values a caller supplies
("vendor", "hardware_family", "customer_segment", a dimension invented
tomorrow -- all handled identically, with zero code change). There is
deliberately no canonical/recommended dimension vocabulary constant here
or anywhere else in this package: a hardcoded vocabulary, even a
non-enforced advisory one, would still bake business/domain assumptions
into the generic KM runtime. If a domain-specific vocabulary or taxonomy
is ever needed, it belongs outside this generic applicability evaluator
(e.g. in a future, separate domain service) -- never inside it. Examples
using names like "vendor"/"technology"/"release" appear only in this
module's docstrings, docs/KNOWLEDGE_CONTRACT.md, and tests, purely as
illustration -- never as a runtime constraint.
"""
from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, Field, model_validator

from backend.knowledge.domain.models import Applicability, normalize_applicability_dimensions, normalize_dimension_value


class ApplicabilityOutcome(str, Enum):
    """The deterministic result of comparing one knowledge object's
    `Applicability` against an `ApplicabilityContext`. See this module's
    docstring and docs/KNOWLEDGE_CONTRACT.md for the exact semantics of
    each value -- summarized here only as a reminder:

    MATCH: every constrained dimension has known context and overlaps.
    PARTIAL_MATCH: at least one constrained dimension matches, and the
      rest are UNKNOWN (missing context) -- never combined with an
      explicit NOT_APPLICABLE dimension, which always dominates instead.
    NOT_APPLICABLE: at least one constrained dimension has known context
      that explicitly conflicts with every allowed value -- this always
      dominates the overall outcome, even if other dimensions match.
    UNKNOWN: the object has applicability constraints, but none of them
      can currently be evaluated because context is absent for all of
      them.

    Never a fuzzy score or confidence percentage -- these four values are
    the entire outcome vocabulary this phase produces.
    """

    MATCH = "match"
    PARTIAL_MATCH = "partial_match"
    NOT_APPLICABLE = "not_applicable"
    UNKNOWN = "unknown"


class ApplicabilityContext(BaseModel):
    """The known operational applicability facts to evaluate a
    `KnowledgeObject`'s `Applicability` against.

    This is deliberately NOT the future Context Engineering Layer, NOT
    Troubleshooting State, NOT Case Context, NOT an ADK object, and NOT
    retrieval input -- it is only "a set of known applicability
    dimensions", normalized the same way `Applicability.dimensions` is
    (via `normalize_applicability_dimensions`), so both sides of a
    comparison are always in the same canonical shape.
    """

    dimensions: dict[str, list[str]] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _normalize_dimensions(self) -> "ApplicabilityContext":
        self.dimensions = normalize_applicability_dimensions(self.dimensions)
        return self


class ApplicabilityDimensionEvaluation(BaseModel):
    """The deterministic result for exactly one constrained dimension --
    enough structural detail for later retrieval/ranking/provenance logic
    to understand WHY an outcome occurred, without any natural-language
    explanation, ranking score, or semantic-similarity value attached.
    """

    dimension: str
    required_values: list[str] = Field(description="The knowledge object's own allowed values for this dimension, as stored (already normalized/deduped).")
    observed_values: list[str] = Field(description="The context's known values for this dimension, as stored (already normalized/deduped) -- empty if the context does not know this dimension at all.")
    matched_values: list[str] = Field(
        default_factory=list,
        description="The subset of required_values (original casing, required-list order) that overlapped with observed_values -- empty unless outcome is MATCH.",
    )
    outcome: ApplicabilityOutcome = Field(
        description="This dimension's own outcome -- only MATCH, NOT_APPLICABLE, or UNKNOWN ever appear here; PARTIAL_MATCH exists only as an OVERALL outcome (see ApplicabilityEvaluation)."
    )


class ApplicabilityEvaluation(BaseModel):
    """The full deterministic result of `evaluate_applicability`."""

    outcome: ApplicabilityOutcome
    dimension_results: list[ApplicabilityDimensionEvaluation] = Field(
        default_factory=list,
        description="Per-dimension detail, ordered deterministically by dimension name -- empty when the knowledge object had no applicability constraints at all (see the UNCONSTRAINED case below).",
    )


def evaluate_applicability(applicability: Applicability, context: ApplicabilityContext) -> ApplicabilityEvaluation:
    """Deterministically evaluate whether `applicability` applies given
    `context`'s known facts.

    UNCONSTRAINED KNOWLEDGE: a knowledge object with no applicability
    dimensions imposes no applicability restriction, so this returns
    MATCH with no per-dimension detail. This does NOT imply semantic
    relevance, lifecycle authority, or retrieval priority -- it only
    means "no applicability rule excludes this knowledge" (5.1E/5.1G own
    those separate concerns).

    Overall precedence (deterministic, no fuzzy scoring):
      1. No constraints -> MATCH.
      2. Evaluate every constrained dimension (MATCH / NOT_APPLICABLE /
         UNKNOWN each, see `_evaluate_dimension`).
      3. Any dimension NOT_APPLICABLE -> overall NOT_APPLICABLE (an
         explicit conflict always dominates, even alongside matching
         dimensions).
      4. All constrained dimensions MATCH -> overall MATCH.
      5. Some MATCH and the rest UNKNOWN -> overall PARTIAL_MATCH.
      6. No dimension MATCH (all UNKNOWN) -> overall UNKNOWN.

    LifecycleStatus and KnowledgeVersion play no part in this evaluation
    -- a Candidate, Approved, or Archive object with identical
    Applicability dimensions produces the identical outcome; likewise,
    only `Applicability.dimensions["release"]` (an operational-environment
    fact, if the caller chooses to use that dimension name) is ever
    compared here -- `KnowledgeVersion.label` (the document's own
    revision identifier) is never read by this function at all.
    """
    if not applicability.dimensions:
        return ApplicabilityEvaluation(outcome=ApplicabilityOutcome.MATCH, dimension_results=[])

    dimension_results = [
        _evaluate_dimension(dimension, applicability.dimensions[dimension], context.dimensions.get(dimension, []))
        for dimension in sorted(applicability.dimensions)
    ]
    return ApplicabilityEvaluation(outcome=_overall_outcome(dimension_results), dimension_results=dimension_results)


def _evaluate_dimension(
    dimension: str, required_values: list[str], observed_values: list[str]
) -> ApplicabilityDimensionEvaluation:
    """Exact normalized token matching only -- no fuzzy similarity,
    substring, regex, wildcard, or release-range/version-arithmetic
    logic. A missing (empty) `observed_values` always means UNKNOWN for
    this dimension, never an inferred match or an inferred conflict.
    """
    if not observed_values:
        return ApplicabilityDimensionEvaluation(
            dimension=dimension,
            required_values=required_values,
            observed_values=observed_values,
            matched_values=[],
            outcome=ApplicabilityOutcome.UNKNOWN,
        )

    observed_folded = {normalize_dimension_value(value) for value in observed_values}
    matched_values = [value for value in required_values if normalize_dimension_value(value) in observed_folded]

    outcome = ApplicabilityOutcome.MATCH if matched_values else ApplicabilityOutcome.NOT_APPLICABLE
    return ApplicabilityDimensionEvaluation(
        dimension=dimension,
        required_values=required_values,
        observed_values=observed_values,
        matched_values=matched_values,
        outcome=outcome,
    )


def _overall_outcome(dimension_results: list[ApplicabilityDimensionEvaluation]) -> ApplicabilityOutcome:
    outcomes = {result.outcome for result in dimension_results}

    if ApplicabilityOutcome.NOT_APPLICABLE in outcomes:
        # An explicit conflict dominates, even if other dimensions match.
        return ApplicabilityOutcome.NOT_APPLICABLE
    if outcomes == {ApplicabilityOutcome.MATCH}:
        return ApplicabilityOutcome.MATCH
    if ApplicabilityOutcome.MATCH in outcomes:
        # A mix of MATCH and UNKNOWN (NOT_APPLICABLE already excluded above).
        return ApplicabilityOutcome.PARTIAL_MATCH
    return ApplicabilityOutcome.UNKNOWN
