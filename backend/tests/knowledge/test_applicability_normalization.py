"""Phase 5.1B: dimension key/value normalization -- syntax normalization
only, never fuzzy/semantic aliasing (instruction section 7/8/33).
"""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from backend.knowledge.domain.applicability import ApplicabilityContext
from backend.knowledge.domain.models import (
    Applicability,
    normalize_applicability_dimensions,
    normalize_dimension_key,
    normalize_dimension_value,
)

# --- dimension KEYS ----------------------------------------------------


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("vendor", "vendor"),
        (" Vendor ", "vendor"),
        ("node-type", "node_type"),
        ("Node Type", "node_type"),
        ("node_type", "node_type"),
        ("Fault  Category", "fault_category"),  # collapsed repeated separator
    ],
)
def test_dimension_key_normalizes_consistently(raw: str, expected: str) -> None:
    assert normalize_dimension_key(raw) == expected


def test_blank_dimension_key_normalizes_to_empty_string() -> None:
    assert normalize_dimension_key("   ") == ""


def test_blank_dimension_key_rejected_when_used_on_a_model() -> None:
    with pytest.raises(ValidationError):
        Applicability(dimensions={"   ": ["Ericsson"]})
    with pytest.raises(ValidationError):
        ApplicabilityContext(dimensions={"   ": ["Ericsson"]})


def test_differently_cased_keys_are_merged_into_one_canonical_key() -> None:
    merged = normalize_applicability_dimensions({"Vendor": ["Ericsson"], "vendor": ["Nokia"]})
    assert merged == {"vendor": ["Ericsson", "Nokia"]}


# --- dimension VALUES ----------------------------------------------------


def test_ericsson_matches_lowercase_ericsson_for_comparison() -> None:
    assert normalize_dimension_value("Ericsson") == normalize_dimension_value("ericsson")


def test_surrounding_whitespace_ignored_for_value_comparison() -> None:
    assert normalize_dimension_value("  5G ") == normalize_dimension_value("5g")


def test_gnodeb_casing_ignored_for_value_comparison() -> None:
    assert normalize_dimension_value("gNodeB") == normalize_dimension_value("GNODEB")


def test_blank_applicability_value_rejected() -> None:
    with pytest.raises(ValidationError):
        Applicability(dimensions={"vendor": [""]})
    with pytest.raises(ValidationError):
        Applicability(dimensions={"vendor": ["Ericsson", "   "]})


def test_empty_value_list_rejected_not_treated_as_wildcard() -> None:
    with pytest.raises(ValidationError):
        Applicability(dimensions={"vendor": []})


def test_duplicate_normalized_values_deduped_deterministically() -> None:
    applicability = Applicability(dimensions={"vendor": ["Ericsson", "ericsson", " ERICSSON "]})
    assert applicability.dimensions["vendor"] == ["Ericsson"]


def test_original_meaningful_casing_preserved_in_storage() -> None:
    """Values are trimmed but never case-rewritten in storage -- only the
    internal comparison form (normalize_dimension_value) is folded.
    """
    applicability = Applicability(dimensions={"vendor": ["Ericsson"]})
    assert applicability.dimensions["vendor"] == ["Ericsson"]

    context = ApplicabilityContext(dimensions={"vendor": ["  ERICSSON  "]})
    assert context.dimensions["vendor"] == ["ERICSSON"]
