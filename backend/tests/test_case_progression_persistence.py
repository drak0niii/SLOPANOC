"""Case-owned, versioned TroubleshootingProgression (the single source of truth).

    session linked to a Case -> slopanoc_case_troubleshooting_progressions (compare-and-swap version)
    session not linked       -> the session's own document
    TroubleshootingState / troubleshooting_threads -> READ projections re-derived on every save

Covers: sharing across sessions of one Case, isolation between Cases, survival across new
sessions (pending step, observations, hypotheses), optimistic concurrency, projection derivation,
one-way legacy migration, the Alembic migration, and the absence of competing writers.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Any, Optional

import pytest
import pytest_asyncio
from google.genai import types

from backend.agents.technical_authority_engineer import procedure_actions as pa
from backend.agents.technical_authority_engineer import progression_repository as repository_module
from backend.agents.technical_authority_engineer.progression_controller import ProgressionController, ProposalDecision, TurnKind
from backend.agents.technical_authority_engineer.progression_repository import PROGRESSION_REF_KEY, ProgressionRepository
from backend.agents.technical_authority_engineer.schemas import EvidenceReference
from backend.agents.technical_authority_engineer.troubleshooting_threads import (
    ACTIVE_THREAD_STATE_KEY,
    THREADS_STATE_KEY,
    load_active_thread,
    save_projections,
)
from backend.cases.db import CaseDatabase
from backend.cases.progression_store import CaseProgressionStore, ProgressionConflict
from backend.cases.service import CaseService
from backend.cases.troubleshooting_progression import (
    PROGRESSION_STATE_KEY,
    HypothesisState,
    StepStatus,
    TroubleshootingProgression,
    project_troubleshooting_state,
)
from backend.cases.troubleshooting_state import TroubleshootingState
from backend.tests.test_ess_service_unavailable_e2e_verification import isolated_km_repo  # noqa: F401 (fixture)
from backend.tests.test_progression_architecture_e2e import read_adapter  # noqa: F401 (fixture)

ROOT = Path(__file__).resolve().parents[2]
_KID, _SEC = "KID-PERSIST", "sec-0000"
_CONTENT = "Radio unit state check: `st ru`\nCell state check: `st cell`\n"
_EV = EvidenceReference(
    source_id=f"{_KID}:v1:{_SEC}", source_type="governed_knowledge", title="Persist MOP", content_snippet=_CONTENT,
    metadata={"knowledge_id": _KID, "version_label": "v1", "section_id": _SEC, "source_locator": "l", "title": "Persist MOP",
              "heading": None, "lifecycle_status": "approved", "applicability_outcome": "match"},
)
_ACTIONS = {a.command_template: a for a in pa.actions_for_evidence(_EV, _EV.metadata)[0]}
_OBJECTIVE = {"st ru": "Check the radio unit state", "st cell": "Check the cell state"}


@pytest_asyncio.fixture
async def cases():
    database = CaseDatabase(database_url="sqlite+aiosqlite:///:memory:")
    service, store = CaseService(database), CaseProgressionStore(database)
    yield service, store
    await database.close()


async def _case(service: CaseService, title: str = "Radio fault") -> str:
    return (await service.create_case("eng", title, "radio unit fault on node N1")).case_id


async def _linked_state(service: CaseService, case_id: str, session_id: str) -> dict[str, Any]:
    await service.link_session("eng", case_id, session_id, "eng")
    return {"active_case_id": case_id}


class _Turn:
    """One TAE-like turn against the authoritative store: load -> project -> controller -> save."""

    def __init__(self, state: dict[str, Any], session_id: str, store: CaseProgressionStore) -> None:
        self.state, self.session_id, self.store = state, session_id, store

    async def run(self, operator_text: str = "", propose: Optional[str] = None, hypothesis: Optional[str] = None) -> tuple[ProgressionController, ProgressionRepository]:
        repository = ProgressionRepository(self.state, session_id=self.session_id, store=self.store)
        progression = await repository.load()
        if progression.faults:
            save_projections(self.state, progression)
        thread = load_active_thread(self.state) or TroubleshootingState(fault_id="FAULT-RADIO", symptom_summary="radio unit fault")
        controller = ProgressionController(self.state, thread, session_id=self.session_id, progression=progression)
        controller.classify_turn(operator_text)
        controller.apply_operator_turn(operator_text)
        if hypothesis:
            controller.apply_hypothesis_updates([{"statement": hypothesis, "proposed_state": "supported"}])
        if propose:
            action = _ACTIONS[propose]
            resolution, _ = pa.resolve_procedure_action(action.action_id, issued_ids={action.action_id}, selected_evidence=[_EV])
            proposal = {"outcome": "recommended", "technical_interpretation": "t", "missing_information": [],
                        "diagnostic_step": {"action": _OBJECTIVE[propose], "reason": "r", "expected_evidence": "state", "command": propose,
                                            "command_source": _EV.source_id, "restrictions": [], "procedure_action_id": action.action_id}}
            decision, result = controller.evaluate_proposal(proposal, resolution, [_EV])
            controller.record(decision, result, controller.check_id_for(decision, f"chk-{propose.replace(' ', '-')}"),
                              selected_evidence_ids=[_EV.source_id], applicability={_EV.source_id: "match"})
            controller.last_decision = decision  # type: ignore[attr-defined]
        await repository.save(progression, thread.fault_id)
        return controller, repository


# ---- store: versioned compare-and-swap ---------------------------------------------------------------------
@pytest.mark.asyncio
async def test_store_persists_version_n_plus_one_and_refuses_stale_writes(cases) -> None:
    service, store = cases
    case_id = await _case(service)
    progression = TroubleshootingProgression(case_id=case_id)
    assert await store.save(case_id, progression, None) == 1
    first = await store.load(case_id)
    assert first.version == 1
    progression.open_questions = []
    assert await store.save(case_id, first.progression, 1) == 2
    stale = first.progression.model_copy(update={"case_id": "overwritten"})
    with pytest.raises(ProgressionConflict) as conflict:
        await store.save(case_id, stale, 1)
    assert (conflict.value.expected_version, conflict.value.current_version) == (1, 2)
    assert (await store.load(case_id)).progression.case_id == case_id, "the newer progression was not overwritten"
    with pytest.raises(ProgressionConflict):
        await store.save(case_id, progression, None)  # a second "create" loses too


# ---- sharing / isolation / survival --------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_same_case_across_two_sessions_shares_one_progression(cases) -> None:
    service, store = cases
    case_id = await _case(service)
    session_a = _Turn(await _linked_state(service, case_id, "S-A"), "S-A", store)
    session_b = _Turn(await _linked_state(service, case_id, "S-B"), "S-B", store)

    controller_a, repository_a = await session_a.run(propose="st ru", hypothesis="The radio unit is disabled")
    assert repository_a.case_scoped and repository_a.version == 1
    step_id = controller_a.pending_step().step_id

    controller_b, repository_b = await session_b.run()
    assert controller_b.pending_step().step_id == step_id, "session B continues session A's pending step"
    assert session_b.state[ACTIVE_THREAD_STATE_KEY]["diagnostic_history"][0]["grounded_command"] == "st ru"

    controller_b, _ = await session_b.run("$ st ru\nRadioUnit=1 OPER=DISABLED")  # the result binds in session B
    stored = await store.load(case_id)
    assert stored.version == 3 and stored.progression.step(step_id).status is StepStatus.COMPLETED
    assert set(stored.progression.session_ids) >= {"S-A", "S-B"}

    controller_a, _ = await session_a.run(propose="st ru")  # session A sees the completion: no repeat
    assert controller_a.last_decision is ProposalDecision.REJECTED_REPEATED_STEP
    assert (await store.load(case_id)).version == 4


@pytest.mark.asyncio
async def test_different_cases_remain_isolated(cases) -> None:
    service, store = cases
    case_1, case_2 = await _case(service, "Case one"), await _case(service, "Case two")
    await _Turn(await _linked_state(service, case_1, "S-1"), "S-1", store).run(propose="st ru")
    controller_2, repository_2 = await _Turn(await _linked_state(service, case_2, "S-2"), "S-2", store).run()
    assert repository_2.case_id == case_2 and controller_2.pending_step() is None
    assert controller_2.progression.steps == []
    assert (await store.load(case_1)).version == 1 and (await store.load(case_2)).progression.steps == []


@pytest.mark.asyncio
async def test_forged_case_hint_without_a_link_never_reaches_the_case(cases) -> None:
    service, store = cases
    case_id = await _case(service)
    await _Turn(await _linked_state(service, case_id, "S-A"), "S-A", store).run(propose="st ru")
    intruder_state = {"active_case_id": case_id}  # hint only: session S-X is not linked
    controller, repository = await _Turn(intruder_state, "S-X", store).run(propose="st cell")
    assert not repository.case_scoped and controller.progression.case_id is None
    assert [s.command for s in (await store.load(case_id)).progression.steps] == ["st ru"], "the Case is untouched"


@pytest.mark.asyncio
async def test_progression_pending_step_observations_and_hypotheses_survive_a_new_session(cases) -> None:
    service, store = cases
    case_id = await _case(service)
    first = _Turn(await _linked_state(service, case_id, "S-1"), "S-1", store)
    await first.run(propose="st ru")
    await first.run("NRCellDU=1 OPER=DISABLED")  # rejected candidate observation (not attributable)
    await first.run("$ st ru\nRadioUnit=1 OPER=DISABLED", hypothesis="The radio unit is disabled")
    await first.run(propose="st cell")

    fresh = _Turn(await _linked_state(service, case_id, "S-NEW"), "S-NEW", store)
    controller, repository = await fresh.run()
    progression = controller.progression
    ru, cell = progression.steps_for(controller.fault_id)
    assert (ru.status, ru.result.text, ru.result.validation["status"]) == (StepStatus.COMPLETED, "$ st ru\nRadioUnit=1 OPER=DISABLED", "validated")
    assert [c.validation["status"] for c in ru.candidate_observations] == ["ambiguous"]
    assert controller.pending_step().step_id == cell.step_id and controller.pending_context()["command"] == "st cell"
    hypothesis = progression.hypotheses[0]
    assert (hypothesis.state, hypothesis.transitions[-1].evidence_ids) == (HypothesisState.SUPPORTED, [ru.result.result_id])
    assert repository.version == 5 and fresh.state[ACTIVE_THREAD_STATE_KEY]["fault_id"] == controller.fault_id


# ---- concurrency ------------------------------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_stale_concurrent_update_cannot_overwrite_the_newer_progression(cases) -> None:
    service, store = cases
    case_id = await _case(service)
    await _Turn(await _linked_state(service, case_id, "S-A"), "S-A", store).run(propose="st ru")
    state_a, state_b = await _linked_state(service, case_id, "S-A2"), await _linked_state(service, case_id, "S-B2")
    repository_a = ProgressionRepository(state_a, session_id="S-A2", store=store)
    repository_b = ProgressionRepository(state_b, session_id="S-B2", store=store)
    progression_a, progression_b = await repository_a.load(), await repository_b.load()
    assert repository_a.version == repository_b.version == 1

    progression_a.add_open_question(progression_a.active_fault_id, "operator A's question")
    await repository_a.save(progression_a)
    progression_b.add_open_question(progression_b.active_fault_id, "operator B's stale question")
    with pytest.raises(ProgressionConflict):
        await repository_b.save(progression_b)
    stored = await store.load(case_id)
    assert stored.version == 2 and [q.text for q in stored.progression.open_questions] == ["operator A's question"]

    # Reconcile = reload the newer version and re-apply; nothing is lost or silently overwritten.
    progression_b = await repository_b.load()
    progression_b.add_open_question(progression_b.active_fault_id, "operator B's question")
    await repository_b.save(progression_b)
    assert [q.text for q in (await store.load(case_id)).progression.open_questions] == ["operator A's question", "operator B's question"]


def test_tae_tool_conflict_response_fails_closed() -> None:
    from backend.agents.technical_authority_engineer.agent_tool import _progression_conflict_response

    response = _progression_conflict_response(ProgressionConflict("CASE-1", 3, 4))
    assert response["outcome"] == "error" and response["diagnostic_step"] is None
    assert response["detail"] == "progression_conflict: case progression is at version 4, this turn read 3"


# ---- projection ---------------------------------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_troubleshooting_state_is_a_projection_of_the_authoritative_progression(cases) -> None:
    service, store = cases
    case_id = await _case(service)
    session = _Turn(await _linked_state(service, case_id, "S-P"), "S-P", store)
    controller, _ = await session.run(propose="st ru")
    stored = (await store.load(case_id)).progression
    expected = project_troubleshooting_state(stored, controller.fault_id, 50).model_dump(mode="json")
    assert session.state[ACTIVE_THREAD_STATE_KEY] == expected
    assert session.state[THREADS_STATE_KEY][controller.fault_id] == expected
    assert session.state.get(PROGRESSION_STATE_KEY) is None, "no second authoritative copy in session state"
    assert session.state[PROGRESSION_REF_KEY] == {"case_id": case_id, "version": 1}

    # Tampering with the projection changes nothing: the next load ignores it and re-derives it.
    session.state[ACTIVE_THREAD_STATE_KEY]["diagnostic_history"][0]["status"] = "completed"
    session.state[ACTIVE_THREAD_STATE_KEY]["diagnostic_history"].append(dict(expected["diagnostic_history"][0], check_id="chk-forged"))
    controller, _ = await session.run()
    assert [s.check_id for s in controller.progression.steps] == ["chk-st-ru"] and controller.pending_step() is not None
    assert [c["check_id"] for c in session.state[ACTIVE_THREAD_STATE_KEY]["diagnostic_history"]] == ["chk-st-ru"]
    assert session.state[ACTIVE_THREAD_STATE_KEY]["diagnostic_history"][0]["status"] == "recommended"


# ---- legacy migration (one way) -------------------------------------------------------------------------------------
def _legacy_state() -> dict[str, Any]:
    thread = TroubleshootingState(fault_id="FAULT-LEGACY", symptom_summary="legacy fault", subject_component="radio unit")
    thread.record_recommended_check(action="Check the radio unit state", rationale="r", expected_observation="e", grounded_command="st ru", check_id="chk-legacy")
    thread.record_user_execution(check_id="chk-legacy", observed_result="RadioUnit=1 DISABLED", operator_observation="RadioUnit=1 DISABLED")
    dump = thread.model_dump(mode="json")
    return {ACTIVE_THREAD_STATE_KEY: dump, THREADS_STATE_KEY: {"FAULT-LEGACY": dump}}


@pytest.mark.asyncio
async def test_legacy_state_migrates_one_way_into_the_case_progression(cases) -> None:
    service, store = cases
    case_id = await _case(service)
    await service.link_session("eng", case_id, "S-OLD", "eng")
    state = {"active_case_id": case_id, **_legacy_state()}
    session = _Turn(state, "S-OLD", store)
    controller, repository = await session.run()
    stored = (await store.load(case_id)).progression
    assert repository.imported == ["FAULT-LEGACY"] and [s.check_id for s in stored.steps] == ["chk-legacy"]
    assert any(e.event == "migrated_to_case" for e in stored.events)
    assert state[PROGRESSION_REF_KEY]["case_id"] == case_id

    # Legacy state edited afterwards is never imported again: the projection is simply re-derived.
    extra = TroubleshootingState(fault_id="FAULT-STRAY", symptom_summary="stray").model_dump(mode="json")
    state[THREADS_STATE_KEY]["FAULT-STRAY"] = extra
    state[ACTIVE_THREAD_STATE_KEY]["diagnostic_history"].append(dict(state[ACTIVE_THREAD_STATE_KEY]["diagnostic_history"][0], check_id="chk-stray"))
    controller, repository = await session.run()
    stored = (await store.load(case_id)).progression
    assert set(stored.faults) == {"FAULT-LEGACY"} and [s.check_id for s in stored.steps] == ["chk-legacy"]
    assert repository.imported == [] and "FAULT-STRAY" not in state[THREADS_STATE_KEY]


@pytest.mark.asyncio
async def test_pre_link_session_progression_is_imported_once_then_removed(cases) -> None:
    service, store = cases
    case_id = await _case(service)
    await _Turn(await _linked_state(service, case_id, "S-CASE"), "S-CASE", store).run(propose="st ru")
    solo_state: dict[str, Any] = {ACTIVE_THREAD_STATE_KEY: TroubleshootingState(fault_id="FAULT-SOLO", symptom_summary="cell fault").model_dump(mode="json")}
    solo = _Turn(solo_state, "S-SOLO", store)
    controller, repository = await solo.run(propose="st cell")  # no Case yet: session scope
    assert not repository.case_scoped and solo_state[PROGRESSION_STATE_KEY] is not None and controller.fault_id == "FAULT-SOLO"

    await service.link_session("eng", case_id, "S-SOLO", "eng")
    solo_state["active_case_id"] = case_id
    controller, repository = await solo.run()
    assert repository.case_scoped and repository.imported == ["FAULT-SOLO"]
    stored = (await store.load(case_id)).progression
    assert [(s.fault_id, s.command) for s in stored.steps] == [("FAULT-RADIO", "st ru"), ("FAULT-SOLO", "st cell")]
    assert solo_state[PROGRESSION_STATE_KEY] is None, "the session copy is gone: one authority"
    controller, repository = await solo.run()
    assert repository.imported == [] and len((await store.load(case_id)).progression.steps) == 2, "imported exactly once"


# ---- runtime: two real ChatService sessions on one Case ----------------------------------------------------------------
@pytest.mark.asyncio
async def test_two_chat_sessions_on_one_case_share_the_runtime_progression(isolated_km_repo, monkeypatch, cases) -> None:
    from backend.tests.test_progression_controller import _CATALOG, _RU, _RU_STEP, _mop, _search, _select
    from backend.tests.test_live_sequence_baseband_then_rru_reset import _Conversation, _respond

    service, store = cases
    monkeypatch.setattr(repository_module, "get_case_progression_store", lambda: store)
    await isolated_km_repo.add(_mop())
    case_id = await _case(service)

    async def _linked(conversation: _Conversation) -> None:
        conversation.session_id = await conversation.sessions.create_session(user_id="eng")
        await service.link_session("eng", case_id, conversation.session_id, "eng")
        session = await conversation.sessions.get_session(conversation.session_id, "eng")
        await conversation.sessions.persist_state_delta(session, {"active_case_id": case_id})

    propose_ru = [_search("radio unit fault"), _select("KID-RADIO-FAULT", "sec-checks"), _CATALOG, _respond(action_id=_RU, action=_RU_STEP)]
    first, second = _Conversation(monkeypatch), _Conversation(monkeypatch)
    await _linked(first)
    await _linked(second)

    t1 = await first.turn("Ericsson 5G: radio unit fault alarm on the node. What should I check?", propose_ru, "Run st ru.", {"subject_component": "radio unit"})
    assert t1["record"]["diagnostic_step"]["command"] == "st ru"
    assert t1["state"].get(PROGRESSION_STATE_KEY) is None and t1["state"][PROGRESSION_REF_KEY]["case_id"] == case_id

    t2 = await second.turn("what is next cmd?", propose_ru, "Run st ru.")
    assert t2["tae_request"]["pending_step"]["procedure_action_id"] == _RU, "the second session resolves the SAME pending step"
    t3 = await second.turn("$ st ru\nRadioUnit=1 OPER=ENABLED", propose_ru, "Run st ru again.")
    assert [e for e in t3["trace"]["operational_events"] if e.get("stage") == "progression"][-1]["decision"] == "rejected_repeated_step"

    t4 = await first.turn("what next?", propose_ru, "Run st ru.")
    assert [e for e in t4["trace"]["operational_events"] if e.get("stage") == "progression"][-1]["decision"] == "rejected_repeated_step", (
        "the first session sees the result recorded in the second"
    )
    stored = await store.load(case_id)
    assert [(s.command, s.status) for s in stored.progression.steps] == [("st ru", StepStatus.COMPLETED)]
    assert stored.version == t4["state"][PROGRESSION_REF_KEY]["version"]


@pytest.mark.asyncio
async def test_controlled_read_in_one_session_binds_into_the_case_progression(isolated_km_repo, monkeypatch, cases, read_adapter) -> None:
    from backend.api import operational_execution_service
    from backend.tests.test_progression_controller import _CATALOG, _RU, _RU_STEP, _mop, _search, _select
    from backend.tests.test_live_sequence_baseband_then_rru_reset import _Conversation, _respond

    service, store = cases
    monkeypatch.setattr(repository_module, "get_case_progression_store", lambda: store)
    await isolated_km_repo.add(_mop())
    case_id = await _case(service)
    first, second = _Conversation(monkeypatch), _Conversation(monkeypatch)
    for conversation in (first, second):
        conversation.session_id = await conversation.sessions.create_session(user_id="eng")
        await service.link_session("eng", case_id, conversation.session_id, "eng")
        await conversation.sessions.persist_state_delta(await conversation.sessions.get_session(conversation.session_id, "eng"), {"active_case_id": case_id})
    propose_ru = [_search("radio unit fault"), _select("KID-RADIO-FAULT", "sec-checks"), _CATALOG, _respond(action_id=_RU, action=_RU_STEP)]

    t1 = await first.turn("Ericsson 5G: radio unit fault alarm on the node. What should I check?", propose_ru, "Run st ru.", {"subject_component": "radio unit"})
    control = t1["record"]["operational_control"]
    assert control["control_stage"] == "ready_for_execution"
    read_adapter.output = "RadioUnit=1 OPER=ENABLED AVAIL="
    await operational_execution_service.execute_read(first.sessions, first.session_id, control["check_id"], "eng")
    stored = await store.load(case_id)
    step = stored.progression.steps[0]
    assert (step.status, step.result.source.value, step.result.text) == (StepStatus.COMPLETED, "execution_adapter", "RadioUnit=1 OPER=ENABLED AVAIL=")

    t2 = await second.turn("what next?", propose_ru, "Run st ru.")
    assert [e for e in t2["trace"]["operational_events"] if e.get("stage") == "progression"][-1]["decision"] == "rejected_repeated_step"
    check = t2["state"][ACTIVE_THREAD_STATE_KEY]["diagnostic_history"][0]
    assert (check["status"], check["observed_result"]) == ("completed", "RadioUnit=1 OPER=ENABLED AVAIL="), "second session's projection"


# ---- Alembic migration ----------------------------------------------------------------------------------------------
def _alembic(db_url: str, *args: str) -> subprocess.CompletedProcess:
    env = dict(os.environ, SLOPANOC_DATABASE_URL=db_url)
    return subprocess.run([sys.executable, "-m", "alembic", *args], cwd=ROOT, env=env, capture_output=True, text=True, timeout=120)


def test_alembic_migration_creates_and_drops_the_versioned_progression_table(tmp_path) -> None:
    from sqlalchemy import create_engine, inspect

    from backend.attachments.models import Base as AttachmentBase
    from backend.cases.models import Base as CaseBase
    from backend.knowledge.repository.sqlalchemy import Base as KnowledgeBase

    path = tmp_path / "migration.db"
    engine = create_engine(f"sqlite:///{path}")
    table = "slopanoc_case_troubleshooting_progressions"
    CaseBase.metadata.create_all(engine, tables=[t for t in CaseBase.metadata.sorted_tables if t.name != table])
    KnowledgeBase.metadata.create_all(engine)
    AttachmentBase.metadata.create_all(engine)
    url = f"sqlite+aiosqlite:///{path}"
    assert _alembic(url, "stamp", "3e59584b1012").returncode == 0
    upgrade = _alembic(url, "upgrade", "head")
    assert upgrade.returncode == 0, upgrade.stderr[-2000:]
    columns = {c["name"]: c for c in inspect(engine).get_columns(table)}
    assert set(columns) == {"case_id", "version", "schema_version", "progression", "created_at", "updated_at", "updated_by_session_id"}
    assert not columns["version"]["nullable"] and inspect(engine).get_pk_constraint(table)["constrained_columns"] == ["case_id"]
    assert [fk["referred_table"] for fk in inspect(engine).get_foreign_keys(table)] == ["slopanoc_cases"]
    downgrade = _alembic(url, "downgrade", "3e59584b1012")
    assert downgrade.returncode == 0, downgrade.stderr[-2000:]
    assert table not in inspect(engine).get_table_names()
    engine.dispose()


# ---- no competing writers ----------------------------------------------------------------------------------------------
def test_no_production_code_writes_troubleshooting_state_directly() -> None:
    """Every step/result/status mutation originates in the progression; the only permitted thread
    write is the control plane mirroring its own control-record stage into the read projection."""
    mutators = re.compile(r"\.(record_recommended_check|record_user_execution|mark_check_skipped|update_check|record_adapter_execution)\(")
    offenders = []
    for path in (ROOT / "backend").rglob("*.py"):
        if "tests" in path.parts or path.name == "troubleshooting_state.py":
            continue
        for number, line in enumerate(path.read_text().splitlines(), 1):
            if mutators.search(line) or re.search(r"\bthread\.status\s*=|troubleshooting_state\.status\s*=", line):
                offenders.append(f"{path.relative_to(ROOT)}:{number}")
    assert offenders == []
