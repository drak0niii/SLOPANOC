"""Tests for the 6A.2 Knowledge Applicability bridge
(`backend/knowledge/domain/telco_applicability.py`): the CONSTRAINED /
EXPLICIT_ANY / UNSPECIFIED distinction, mutual exclusivity, and backward
compatibility with real, already-ingested A5 `KnowledgeObject.applicability`
data.
"""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from backend.knowledge.domain.models import Applicability
from backend.knowledge.domain.telco_applicability import (
    ApplicabilityScopeKind,
    dimension_scope,
    from_knowledge_object,
)


def test_constrained_dimension_present_in_applicability_dimensions() -> None:
    applicability = Applicability(dimensions={"vendor": ["Ericsson", "Nokia"]})
    profile = from_knowledge_object(knowledge_id="k1", version_label="1.0", applicability=applicability)
    assert dimension_scope(profile, "vendor") == ApplicabilityScopeKind.CONSTRAINED


def test_explicit_any_dimension_declared_but_not_constrained() -> None:
    applicability = Applicability(dimensions={"domain": ["RAN"]})
    profile = from_knowledge_object(
        knowledge_id="k1",
        version_label="1.0",
        applicability=applicability,
        explicit_any_dimensions=frozenset({"vendor"}),
    )
    assert dimension_scope(profile, "vendor") == ApplicabilityScopeKind.EXPLICIT_ANY
    assert dimension_scope(profile, "domain") == ApplicabilityScopeKind.CONSTRAINED


def test_unspecified_is_the_default_for_every_other_dimension() -> None:
    """The fail-closed default (instruction section 17): a dimension
    nobody has said anything about is UNSPECIFIED, never silently ANY.
    """
    applicability = Applicability(dimensions={"vendor": ["Ericsson"]})
    profile = from_knowledge_object(knowledge_id="k1", version_label="1.0", applicability=applicability)
    assert dimension_scope(profile, "technology") == ApplicabilityScopeKind.UNSPECIFIED
    assert dimension_scope(profile, "customer") == ApplicabilityScopeKind.UNSPECIFIED


def test_any_and_constrained_are_mutually_exclusive_for_the_same_dimension() -> None:
    applicability = Applicability(dimensions={"vendor": ["Ericsson"]})
    with pytest.raises(ValidationError):
        from_knowledge_object(
            knowledge_id="k1",
            version_label="1.0",
            applicability=applicability,
            explicit_any_dimensions=frozenset({"vendor"}),
        )


def test_dimension_key_normalization_matches_5_1b_exactly() -> None:
    """`dimension_scope` must use the SAME `normalize_dimension_key` 5.1B
    already uses, so "Vendor"/"vendor"/"VENDOR" are always the same
    dimension on both sides of a comparison.
    """
    applicability = Applicability(dimensions={"vendor": ["Ericsson"]})
    profile = from_knowledge_object(knowledge_id="k1", version_label="1.0", applicability=applicability)
    assert dimension_scope(profile, "Vendor") == ApplicabilityScopeKind.CONSTRAINED
    assert dimension_scope(profile, "VENDOR") == ApplicabilityScopeKind.CONSTRAINED


def test_explicit_any_dimensions_are_normalized_on_construction() -> None:
    profile = from_knowledge_object(
        knowledge_id="k1",
        version_label="1.0",
        applicability=Applicability(),
        explicit_any_dimensions=frozenset({"Vendor", "VENDOR", "vendor"}),
    )
    assert profile.explicit_any_dimensions == frozenset({"vendor"})


def test_backward_compatible_with_unconstrained_a5_applicability() -> None:
    """A real A5 document with NO applicability constraints at all
    (`Applicability(dimensions={})`, the common case for a generic MOP)
    bridges cleanly: every dimension is UNSPECIFIED, nothing raises.
    """
    profile = from_knowledge_object(knowledge_id="A5-VALIDATION-DOCUMENT1", version_label="1.0", applicability=Applicability())
    assert dimension_scope(profile, "vendor") == ApplicabilityScopeKind.UNSPECIFIED
    assert dimension_scope(profile, "technology") == ApplicabilityScopeKind.UNSPECIFIED


def test_backward_compatible_with_real_constrained_a5_applicability_shape() -> None:
    """Mirrors the real A5 corpus shape (Rogers 4G-only vs 4G/5G-combined
    MOPs, CLAUDE.md's own A5 test F/G) -- multiple constrained dimensions,
    no EXPLICIT_ANY declared, bridges without any change to the existing
    `Applicability` object.
    """
    applicability = Applicability(dimensions={"vendor": ["Ericsson"], "technology": ["4G"]})
    profile = from_knowledge_object(knowledge_id="A5-VALIDATION-ROGERS-4G", version_label="1.0", applicability=applicability)
    assert dimension_scope(profile, "vendor") == ApplicabilityScopeKind.CONSTRAINED
    assert dimension_scope(profile, "technology") == ApplicabilityScopeKind.CONSTRAINED
    assert dimension_scope(profile, "release") == ApplicabilityScopeKind.UNSPECIFIED
    # The existing, unmodified Applicability object survives unchanged.
    assert profile.applicability.dimensions["vendor"] == ["Ericsson"]


def test_identity_fields_required() -> None:
    with pytest.raises(ValidationError):
        from_knowledge_object(knowledge_id="", version_label="1.0", applicability=Applicability())
    with pytest.raises(ValidationError):
        from_knowledge_object(knowledge_id="k1", version_label="", applicability=Applicability())
