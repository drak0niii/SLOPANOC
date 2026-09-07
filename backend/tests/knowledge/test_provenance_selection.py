"""Phase 5.1H: `validate_evidence_selection` -- the critical trust
boundary allowing a future caller/model to SELECT already-validated
evidence by minimal identity, without ever being able to supply
authoritative content/source/title/locator, and without ever being able
to cite a real repository section that was not part of the bounded
evidence set for this turn.
"""
from __future__ import annotations

import pytest

from backend.knowledge.domain.contracts import KnowledgeEvidenceReference
from backend.knowledge.domain.enums import KnowledgeDocumentType, LifecycleStatus
from backend.knowledge.domain.models import KnowledgeSection, KnowledgeSource
from backend.knowledge.provenance.contracts import (
    KnowledgeEvidenceItem,
    KnowledgeEvidenceSelectionError,
    KnowledgeEvidenceSelectionKey,
    KnowledgeEvidenceSet,
)
from backend.knowledge.provenance.service import validate_evidence_selection


def _item(knowledge_id: str, version_label: str, section_id: str, content: str = "content") -> KnowledgeEvidenceItem:
    section = KnowledgeSection(section_id=section_id, knowledge_id=knowledge_id, sequence=0, content=content)
    source = KnowledgeSource(source_system="test", source_id=f"{knowledge_id}-doc")
    reference = KnowledgeEvidenceReference(
        knowledge_id=knowledge_id, version_label=version_label, section_id=section_id,
        source_system="test", source_id=f"{knowledge_id}-doc",
    )
    return KnowledgeEvidenceItem(
        reference=reference, title=f"{knowledge_id} guide", document_type=KnowledgeDocumentType.SOP,
        lifecycle_status=LifecycleStatus.APPROVED, source=source, section=section,
    )


def _key(knowledge_id: str, version_label: str, section_id: str) -> KnowledgeEvidenceSelectionKey:
    return KnowledgeEvidenceSelectionKey(knowledge_id=knowledge_id, version_label=version_label, section_id=section_id)


_ITEM_A = _item("k1", "v1", "s-a", content="alpha content")
_ITEM_B = _item("k1", "v1", "s-b", content="beta content")
_ITEM_C = _item("k2", "v1", "s-c", content="gamma content")
_EVIDENCE_SET = KnowledgeEvidenceSet(items=[_ITEM_A, _ITEM_B, _ITEM_C])


# --- valid selection --------------------------------------------------------------


def test_valid_single_selection_returns_actual_backend_item() -> None:
    result = validate_evidence_selection(_EVIDENCE_SET, [_key("k1", "v1", "s-a")])
    assert len(result) == 1
    assert result[0] is _ITEM_A
    assert result[0].section.content == "alpha content"


def test_valid_multiple_selection_returns_actual_backend_items_with_exact_content() -> None:
    result = validate_evidence_selection(_EVIDENCE_SET, [_key("k1", "v1", "s-a"), _key("k2", "v1", "s-c")])
    assert [item.section.content for item in result] == ["alpha content", "gamma content"]


# --- fabricated selection ----------------------------------------------------------


def test_unknown_knowledge_id_fails() -> None:
    with pytest.raises(KnowledgeEvidenceSelectionError):
        validate_evidence_selection(_EVIDENCE_SET, [_key("does-not-exist", "v1", "s-a")])


def test_unknown_version_label_fails() -> None:
    with pytest.raises(KnowledgeEvidenceSelectionError):
        validate_evidence_selection(_EVIDENCE_SET, [_key("k1", "does-not-exist", "s-a")])


def test_unknown_section_id_fails() -> None:
    with pytest.raises(KnowledgeEvidenceSelectionError):
        validate_evidence_selection(_EVIDENCE_SET, [_key("k1", "v1", "does-not-exist")])


def test_real_repository_section_not_in_bounded_evidence_set_fails() -> None:
    """REAL IN REPOSITORY does not automatically mean AVAILABLE EVIDENCE
    FOR THIS TURN -- a section that is a perfectly valid identity shape
    but was simply never included in `_EVIDENCE_SET` must be rejected
    exactly like a purely fabricated one.
    """
    with pytest.raises(KnowledgeEvidenceSelectionError):
        validate_evidence_selection(_EVIDENCE_SET, [_key("k2", "v1", "s-not-retrieved-this-turn")])


# --- mixed valid/invalid selection ---------------------------------------------


def test_mixed_valid_and_fabricated_selection_fails_entirely() -> None:
    with pytest.raises(KnowledgeEvidenceSelectionError):
        validate_evidence_selection(
            _EVIDENCE_SET, [_key("k1", "v1", "s-a"), _key("k1", "v1", "fabricated"), _key("k2", "v1", "s-c")]
        )


def test_mixed_selection_failure_does_not_leak_partial_valid_results() -> None:
    try:
        validate_evidence_selection(
            _EVIDENCE_SET, [_key("k1", "v1", "s-a"), _key("k1", "v1", "fabricated"), _key("k2", "v1", "s-c")]
        )
        pytest.fail("expected KnowledgeEvidenceSelectionError")
    except KnowledgeEvidenceSelectionError as exc:
        # The error message may name the offending identity for
        # diagnosis, but must never assert/return a partial result list.
        assert "fabricated" in str(exc)


# --- duplicate selection -----------------------------------------------------------


def test_duplicate_selection_returns_one_item_preserving_first_occurrence_order() -> None:
    result = validate_evidence_selection(
        _EVIDENCE_SET, [_key("k1", "v1", "s-a"), _key("k2", "v1", "s-c"), _key("k1", "v1", "s-a")]
    )
    assert [item.reference.section_id for item in result] == ["s-a", "s-c"]


def test_duplicate_selection_is_not_itself_a_violation() -> None:
    result = validate_evidence_selection(_EVIDENCE_SET, [_key("k1", "v1", "s-a"), _key("k1", "v1", "s-a")])
    assert len(result) == 1


# --- selection order ----------------------------------------------------------------


def test_selection_order_is_preserved_as_requested_not_evidence_set_order() -> None:
    result = validate_evidence_selection(_EVIDENCE_SET, [_key("k2", "v1", "s-c"), _key("k1", "v1", "s-a")])
    assert [item.reference.section_id for item in result] == ["s-c", "s-a"]


def test_evidence_set_order_is_independent_of_selection_order() -> None:
    assert [item.reference.section_id for item in _EVIDENCE_SET.items] == ["s-a", "s-b", "s-c"]


# --- empty selection ------------------------------------------------------------------


def test_empty_selection_returns_empty_list_without_error() -> None:
    assert validate_evidence_selection(_EVIDENCE_SET, []) == []


# --- bounded evidence universe: selection never touches the repository --------


def test_validate_evidence_selection_accepts_no_repository_argument() -> None:
    import inspect

    signature = inspect.signature(validate_evidence_selection)
    assert list(signature.parameters) == ["evidence_set", "selections"]


def test_validate_evidence_selection_is_not_a_coroutine() -> None:
    """Pure and synchronous -- it never awaits a repository call, proving
    it cannot reach beyond the bounded evidence set even if handed one.
    """
    import inspect

    assert not inspect.iscoroutinefunction(validate_evidence_selection)
