"""Phase 5.1J: `backend/tools/knowledge/runtime.py` -- trusted, run-id-
keyed evidence state. Trusted as_of capture-once, multi-search merge/
dedup/contradiction, available-vs-selected semantics, selection
validation (fabricated/real-but-unavailable/previous-run/mixed/duplicate/
multi-call), cross-run and cross-concurrency isolation, and cleanup.
"""
from __future__ import annotations

import threading
from datetime import datetime, timezone

import pytest

from backend.knowledge.domain.enums import KnowledgeDocumentType, LifecycleStatus
from backend.knowledge.domain.models import KnowledgeSection, KnowledgeSource
from backend.knowledge.provenance.contracts import (
    KnowledgeEvidenceItem,
    KnowledgeEvidenceReference,
    KnowledgeEvidenceSelectionError,
    KnowledgeEvidenceSelectionKey,
    KnowledgeEvidenceSet,
)
from backend.knowledge.tools.contracts import KnowledgeSearchAgentPayload, KnowledgeSearchExecutionResult
from backend.tools.knowledge import runtime as rt


def _evidence_item(knowledge_id: str, section_id: str, content: str = "content", version_label: str = "v1") -> KnowledgeEvidenceItem:
    section = KnowledgeSection(section_id=section_id, knowledge_id=knowledge_id, sequence=0, content=content)
    source = KnowledgeSource(source_system="test", source_id=f"{knowledge_id}-doc")
    reference = KnowledgeEvidenceReference(
        knowledge_id=knowledge_id, version_label=version_label, section_id=section_id, source_system="test", source_id=f"{knowledge_id}-doc"
    )
    return KnowledgeEvidenceItem(reference=reference, title="t", document_type=KnowledgeDocumentType.SOP, lifecycle_status=LifecycleStatus.APPROVED, source=source, section=section)


def _execution(*items: KnowledgeEvidenceItem) -> KnowledgeSearchExecutionResult:
    return KnowledgeSearchExecutionResult(agent_payload=KnowledgeSearchAgentPayload(), evidence_set=KnowledgeEvidenceSet(items=list(items)))


def _key(knowledge_id: str, section_id: str, version_label: str = "v1") -> KnowledgeEvidenceSelectionKey:
    return KnowledgeEvidenceSelectionKey(knowledge_id=knowledge_id, version_label=version_label, section_id=section_id)


@pytest.fixture(autouse=True)
def _clean_runtime_state():
    """Isolate each test from any leftover run state -- production relies
    on chat_service.py's own finally-block cleanup; tests clean up
    explicitly via a fixed set of run ids used across this file.
    """
    yield
    for run_id in ("run-a", "run-b", "run-c", "run-x", "run-y"):
        rt.discard_knowledge_run_evidence_state(run_id)


# --- trusted as_of: captured once, reused ---------------------------------------


def test_as_of_is_captured_exactly_once_per_run() -> None:
    calls = []

    def fake_clock() -> datetime:
        calls.append(1)
        return datetime(2026, 1, 1, tzinfo=timezone.utc)

    state1 = rt.get_or_init_run_state("run-a", clock=fake_clock)
    state2 = rt.get_or_init_run_state("run-a", clock=fake_clock)
    state3 = rt.get_or_init_run_state("run-a", clock=fake_clock)

    assert state1 is state2 is state3
    assert len(calls) == 1
    assert state1.execution_context.as_of == datetime(2026, 1, 1, tzinfo=timezone.utc)


def test_different_runs_get_independently_captured_as_of() -> None:
    state_a = rt.get_or_init_run_state("run-a", clock=lambda: datetime(2026, 1, 1, tzinfo=timezone.utc))
    state_b = rt.get_or_init_run_state("run-b", clock=lambda: datetime(2026, 6, 1, tzinfo=timezone.utc))
    assert state_a.execution_context.as_of != state_b.execution_context.as_of


def test_initial_applicability_context_is_empty() -> None:
    state = rt.get_or_init_run_state("run-a")
    assert state.execution_context.applicability_context.dimensions == {}


# --- multi-search merge, dedup, contradiction ----------------------------------


def test_multi_search_merge_unions_and_deduplicates_preserving_order() -> None:
    rt.get_or_init_run_state("run-a")
    rt.record_search_result("run-a", _execution(_evidence_item("k1", "sA"), _evidence_item("k1", "sB")))
    rt.record_search_result("run-a", _execution(_evidence_item("k1", "sB"), _evidence_item("k1", "sC")))

    available = rt.get_available_knowledge_evidence("run-a")
    assert [item.reference.section_id for item in available.items] == ["sA", "sB", "sC"]


def test_contradictory_same_identity_evidence_fails_closed() -> None:
    rt.get_or_init_run_state("run-a")
    rt.record_search_result("run-a", _execution(_evidence_item("k1", "sA", content="original")))
    with pytest.raises(rt.KnowledgeRuntimeConsistencyError):
        rt.record_search_result("run-a", _execution(_evidence_item("k1", "sA", content="DIFFERENT")))


def test_contradiction_does_not_corrupt_existing_available_evidence() -> None:
    rt.get_or_init_run_state("run-a")
    rt.record_search_result("run-a", _execution(_evidence_item("k1", "sA", content="original")))
    try:
        rt.record_search_result("run-a", _execution(_evidence_item("k1", "sA", content="DIFFERENT")))
    except rt.KnowledgeRuntimeConsistencyError:
        pass
    available = rt.get_available_knowledge_evidence("run-a")
    assert len(available.items) == 1
    assert available.items[0].section.content == "original"


def test_record_search_result_requires_initialized_run_state() -> None:
    with pytest.raises(rt.KnowledgeRuntimeError):
        rt.record_search_result("run-never-initialized", _execution(_evidence_item("k1", "sA")))


# --- available vs selected ---------------------------------------------------------


def test_available_evidence_does_not_imply_selection() -> None:
    rt.get_or_init_run_state("run-a")
    rt.record_search_result("run-a", _execution(_evidence_item("k1", "sA")))
    assert rt.snapshot_selected_knowledge_evidence("run-a") == []
    assert len(rt.get_available_knowledge_evidence("run-a").items) == 1


# --- valid selection --------------------------------------------------------------


def test_valid_selection_returns_and_records_trusted_items() -> None:
    rt.get_or_init_run_state("run-a")
    rt.record_search_result("run-a", _execution(_evidence_item("k1", "sA"), _evidence_item("k1", "sB")))

    result = rt.select_evidence("run-a", [_key("k1", "sB"), _key("k1", "sA")])
    assert [item.reference.section_id for item in result] == ["sB", "sA"]
    assert [item.reference.section_id for item in rt.snapshot_selected_knowledge_evidence("run-a")] == ["sB", "sA"]


# --- fabricated / real-but-not-available / previous-run ------------------------


def test_fabricated_selection_fails() -> None:
    rt.get_or_init_run_state("run-a")
    rt.record_search_result("run-a", _execution(_evidence_item("k1", "sA")))
    with pytest.raises(KnowledgeEvidenceSelectionError):
        rt.select_evidence("run-a", [_key("k1", "fabricated")])


def test_real_but_not_retrieved_this_run_fails() -> None:
    """A syntactically valid identity that was simply never part of this
    run's own available evidence -- proves repository existence
    elsewhere is irrelevant; only `run-a`'s own accumulated evidence
    matters.
    """
    rt.get_or_init_run_state("run-a")
    rt.record_search_result("run-a", _execution(_evidence_item("k1", "sA")))
    with pytest.raises(KnowledgeEvidenceSelectionError):
        rt.select_evidence("run-a", [_key("k1", "sB")])


def test_previous_run_evidence_cannot_be_selected_in_a_new_run() -> None:
    rt.get_or_init_run_state("run-a")
    rt.record_search_result("run-a", _execution(_evidence_item("k1", "sA")))
    rt.discard_knowledge_run_evidence_state("run-a")  # simulates chat_service.py's own turn-end cleanup

    # A brand new run reusing no state from the old "run-a" identity:
    rt.get_or_init_run_state("run-a")
    with pytest.raises(KnowledgeEvidenceSelectionError):
        rt.select_evidence("run-a", [_key("k1", "sA")])


# --- mixed valid/invalid, duplicate, multiple calls -----------------------------


def test_mixed_valid_and_invalid_selection_fails_entirely() -> None:
    rt.get_or_init_run_state("run-a")
    rt.record_search_result("run-a", _execution(_evidence_item("k1", "sA"), _evidence_item("k1", "sB")))
    with pytest.raises(KnowledgeEvidenceSelectionError):
        rt.select_evidence("run-a", [_key("k1", "sA"), _key("k1", "fabricated"), _key("k1", "sB")])
    # Selected state must remain unchanged (empty) after the failed call.
    assert rt.snapshot_selected_knowledge_evidence("run-a") == []


def test_duplicate_selection_within_one_call_dedupes() -> None:
    rt.get_or_init_run_state("run-a")
    rt.record_search_result("run-a", _execution(_evidence_item("k1", "sA")))
    result = rt.select_evidence("run-a", [_key("k1", "sA"), _key("k1", "sA"), _key("k1", "sA")])
    assert len(result) == 1
    assert len(rt.snapshot_selected_knowledge_evidence("run-a")) == 1


def test_multiple_selection_calls_union_preserving_first_selected_order() -> None:
    rt.get_or_init_run_state("run-a")
    rt.record_search_result("run-a", _execution(_evidence_item("k1", "sA"), _evidence_item("k1", "sB")))

    rt.select_evidence("run-a", [_key("k1", "sB")])
    rt.select_evidence("run-a", [_key("k1", "sA"), _key("k1", "sB")])

    snapshot = rt.snapshot_selected_knowledge_evidence("run-a")
    assert [item.reference.section_id for item in snapshot] == ["sB", "sA"]


def test_empty_selection_is_a_valid_no_op() -> None:
    rt.get_or_init_run_state("run-a")
    result = rt.select_evidence("run-a", [])
    assert result == []
    assert rt.snapshot_selected_knowledge_evidence("run-a") == []


# --- cross-run / concurrency isolation -------------------------------------------


def test_cross_run_isolation_neither_run_can_select_the_others_evidence() -> None:
    rt.get_or_init_run_state("run-a")
    rt.get_or_init_run_state("run-b")
    rt.record_search_result("run-a", _execution(_evidence_item("k1", "sA")))
    rt.record_search_result("run-b", _execution(_evidence_item("k2", "sB")))

    assert len(rt.select_evidence("run-a", [_key("k1", "sA")])) == 1
    with pytest.raises(KnowledgeEvidenceSelectionError):
        rt.select_evidence("run-a", [_key("k2", "sB")])

    assert len(rt.select_evidence("run-b", [_key("k2", "sB")])) == 1
    with pytest.raises(KnowledgeEvidenceSelectionError):
        rt.select_evidence("run-b", [_key("k1", "sA")])


def test_concurrent_threads_do_not_corrupt_each_others_run_state() -> None:
    """A real-thread stress test (not merely sequential calls) --
    `_lock` must correctly serialize concurrent mutation from two run
    ids without cross-contamination.
    """
    errors: list[Exception] = []

    def worker(run_id: str, knowledge_id: str, section_id: str) -> None:
        try:
            rt.get_or_init_run_state(run_id)
            rt.record_search_result(run_id, _execution(_evidence_item(knowledge_id, section_id)))
            rt.select_evidence(run_id, [_key(knowledge_id, section_id)])
        except Exception as exc:  # pragma: no cover - failure path only
            errors.append(exc)

    threads = [
        threading.Thread(target=worker, args=("run-x", "k1", "sA")),
        threading.Thread(target=worker, args=("run-y", "k2", "sB")),
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert errors == []
    assert [i.reference.section_id for i in rt.snapshot_selected_knowledge_evidence("run-x")] == ["sA"]
    assert [i.reference.section_id for i in rt.snapshot_selected_knowledge_evidence("run-y")] == ["sB"]


# --- cleanup -----------------------------------------------------------------------


def test_discard_removes_available_and_selected_evidence() -> None:
    rt.get_or_init_run_state("run-a")
    rt.record_search_result("run-a", _execution(_evidence_item("k1", "sA")))
    rt.select_evidence("run-a", [_key("k1", "sA")])

    rt.discard_knowledge_run_evidence_state("run-a")

    assert rt.get_available_knowledge_evidence("run-a").items == []
    assert rt.snapshot_selected_knowledge_evidence("run-a") == []


def test_discard_is_safe_when_nothing_was_ever_recorded() -> None:
    rt.discard_knowledge_run_evidence_state("run-never-existed")  # must not raise
