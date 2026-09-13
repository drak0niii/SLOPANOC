"""Phase 6A.4 Gate 1: AUTHORITY / ELIGIBILITY
(`backend/knowledge/narrowing/eligibility.py`)."""
from __future__ import annotations

from datetime import date, datetime, timezone

from backend.knowledge.domain.asset_metadata import BooleanMetadataField, DateMetadataField, KnowledgeAssetMetadata
from backend.knowledge.domain.enums import KnowledgeDocumentType, LifecycleStatus
from backend.knowledge.domain.models import KnowledgeMetadata, KnowledgeObject, KnowledgeSource, KnowledgeVersion
from backend.knowledge.narrowing.eligibility import evaluate_eligibility_for_corpus
from backend.knowledge.narrowing.contracts import NarrowingReasonCode

_AS_OF = datetime(2026, 1, 1, tzinfo=timezone.utc)
_EFF = datetime(2025, 1, 1, tzinfo=timezone.utc)


def _obj(knowledge_id: str, label: str, *, lifecycle=LifecycleStatus.APPROVED, effective_from=_EFF, effective_to=None, supersedes=None, asset_metadata=None) -> KnowledgeObject:
    return KnowledgeObject(
        knowledge_id=knowledge_id,
        document_type=KnowledgeDocumentType.MOP,
        title=knowledge_id,
        version=KnowledgeVersion(label=label, effective_from=effective_from, effective_to=effective_to, supersedes=supersedes or []),
        lifecycle_status=lifecycle,
        source=KnowledgeSource(source_system="x", source_id=knowledge_id),
        metadata=KnowledgeMetadata(asset_metadata=asset_metadata or KnowledgeAssetMetadata()),
    )


def test_approved_current_object_is_eligible() -> None:
    decisions = evaluate_eligibility_for_corpus([_obj("K1", "1.0")], _AS_OF)
    assert decisions[0].eligible is True
    assert decisions[0].reason_codes == []


def test_archived_object_is_excluded_with_archived_reason() -> None:
    decisions = evaluate_eligibility_for_corpus([_obj("K1", "1.0", lifecycle=LifecycleStatus.ARCHIVE)], _AS_OF)
    assert decisions[0].eligible is False
    assert NarrowingReasonCode.ARCHIVED in decisions[0].reason_codes


def test_candidate_object_is_excluded_with_candidate_not_approved_reason() -> None:
    decisions = evaluate_eligibility_for_corpus([_obj("K1", "1.0", lifecycle=LifecycleStatus.CANDIDATE)], _AS_OF)
    assert decisions[0].eligible is False
    assert NarrowingReasonCode.CANDIDATE_NOT_APPROVED in decisions[0].reason_codes


def test_superseded_object_is_excluded_with_superseded_reason() -> None:
    old = _obj("K1", "1.0")
    new = _obj("K1", "2.0", supersedes=["1.0"])
    decisions = {d.version_label: d for d in evaluate_eligibility_for_corpus([old, new], _AS_OF)}
    assert decisions["1.0"].eligible is False
    assert NarrowingReasonCode.SUPERSEDED in decisions["1.0"].reason_codes
    assert decisions["2.0"].eligible is True


def test_not_yet_effective_object_is_excluded() -> None:
    future = _obj("K1", "1.0", effective_from=datetime(2030, 1, 1, tzinfo=timezone.utc))
    decisions = evaluate_eligibility_for_corpus([future], _AS_OF)
    assert decisions[0].eligible is False


def test_past_effective_to_window_object_is_excluded() -> None:
    expired_window = _obj(
        "K1", "1.0", effective_from=datetime(2020, 1, 1, tzinfo=timezone.utc), effective_to=datetime(2024, 1, 1, tzinfo=timezone.utc)
    )
    decisions = evaluate_eligibility_for_corpus([expired_window], _AS_OF)
    assert decisions[0].eligible is False
    assert NarrowingReasonCode.NOT_EFFECTIVE in decisions[0].reason_codes
    assert NarrowingReasonCode.NOT_EFFECTIVE in decisions[0].reason_codes


def test_ambiguous_version_family_excludes_every_member() -> None:
    # Two APPROVED, effective, unrelated versions -> AMBIGUOUS.
    a = _obj("K1", "1.0")
    b = _obj("K1", "2.0")
    decisions = evaluate_eligibility_for_corpus([a, b], _AS_OF)
    assert all(d.eligible is False for d in decisions)
    assert all(NarrowingReasonCode.AMBIGUOUS_VERSION_RESOLUTION in d.reason_codes for d in decisions)


def test_ai_approved_signal_true_reported_never_causes_exclusion_by_itself() -> None:
    am = KnowledgeAssetMetadata()
    am.governance.ai_approved_flag = BooleanMetadataField(normalized_value=True)
    decisions = evaluate_eligibility_for_corpus([_obj("K1", "1.0", asset_metadata=am)], _AS_OF)
    assert decisions[0].ai_approved is True
    assert decisions[0].eligible is True


def test_ai_approved_signal_false_reported_never_excludes_without_policy() -> None:
    am = KnowledgeAssetMetadata()
    am.governance.ai_approved_flag = BooleanMetadataField(normalized_value=False)
    decisions = evaluate_eligibility_for_corpus([_obj("K1", "1.0", asset_metadata=am)], _AS_OF)
    assert decisions[0].ai_approved is False
    assert decisions[0].eligible is True  # eligibility.py itself never enforces policy


def test_ai_approved_signal_missing_is_none_never_true() -> None:
    decisions = evaluate_eligibility_for_corpus([_obj("K1", "1.0")], _AS_OF)
    assert decisions[0].ai_approved is None


def test_expiry_signal_expired_reported() -> None:
    am = KnowledgeAssetMetadata()
    am.lifecycle.expiry_date = DateMetadataField(normalized_value=date(2020, 1, 1))
    decisions = evaluate_eligibility_for_corpus([_obj("K1", "1.0", asset_metadata=am)], _AS_OF)
    assert decisions[0].expired is True


def test_expiry_signal_not_expired_reported() -> None:
    am = KnowledgeAssetMetadata()
    am.lifecycle.expiry_date = DateMetadataField(normalized_value=date(2030, 1, 1))
    decisions = evaluate_eligibility_for_corpus([_obj("K1", "1.0", asset_metadata=am)], _AS_OF)
    assert decisions[0].expired is False


def test_expiry_signal_missing_is_none_never_treated_as_not_expired() -> None:
    decisions = evaluate_eligibility_for_corpus([_obj("K1", "1.0")], _AS_OF)
    assert decisions[0].expired is None


def test_review_due_signal_informational_only() -> None:
    am = KnowledgeAssetMetadata()
    am.lifecycle.next_review_date = DateMetadataField(normalized_value=date(2020, 1, 1))
    decisions = evaluate_eligibility_for_corpus([_obj("K1", "1.0", asset_metadata=am)], _AS_OF)
    assert decisions[0].review_due is True
    assert decisions[0].eligible is True  # never excludes


def test_source_of_truth_flag_reported_never_excludes() -> None:
    am = KnowledgeAssetMetadata()
    am.governance.source_of_truth_flag = BooleanMetadataField(normalized_value=False)
    decisions = evaluate_eligibility_for_corpus([_obj("K1", "1.0", asset_metadata=am)], _AS_OF)
    assert decisions[0].source_of_truth is False
    assert decisions[0].eligible is True


def test_historical_object_with_zero_governance_metadata_remains_eligible() -> None:
    """§9's own explicit requirement: missing newly-added governance
    metadata must not silently broaden OR silently destroy existing
    retrieval behavior -- a real, plain historical object (no
    asset_metadata populated at all) must remain eligible exactly as
    before 6A.4/the 6A.3 addendum existed.
    """
    decisions = evaluate_eligibility_for_corpus([_obj("K1", "1.0")], _AS_OF)
    assert decisions[0].eligible is True
    assert decisions[0].ai_approved is None
    assert decisions[0].expired is None
    assert decisions[0].source_of_truth is None
