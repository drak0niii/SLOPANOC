"""Procedure extraction semantics + document-structure scope (Prompt 3).

Extraction decides WHAT governed text is (template / fixed instance / example / sample output /
screenshot transcription); it never decides what value may be bound. A literal is never turned
into a slot, examples and outputs never become actions or grounding text, and every state change
-- however recognized -- still goes through the target gate, Command Authority, confirmation and
approval. Document1 content below reproduces the governed validation document's text; unit
identifiers are fixtures only.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Optional

import pytest

from backend.agents.technical_authority_engineer import procedure_actions as pa
from backend.agents.technical_authority_engineer import procedure_semantics as ps
from backend.agents.technical_authority_engineer.agent_tool import (
    classify_command_operation,
    ground_command_candidate,
    state_change_class,
)
from backend.agents.technical_authority_engineer.schemas import CommandOperationType, EvidenceReference
from backend.api import approval_service
from backend.approval.service import load_active_proposal
from backend.gateway.safe_error import SafeErrorException
from backend.knowledge.domain.applicability import Applicability
from backend.knowledge.domain.artifacts import KnowledgeArtifact
from backend.knowledge.domain.enums import KnowledgeDocumentType, LifecycleStatus
from backend.knowledge.domain.models import KnowledgeObject, KnowledgeSection, KnowledgeSource, KnowledgeVersion
from backend.operations import control_plane as cp
from backend.tests.test_ess_service_unavailable_e2e_verification import _fc, isolated_km_repo  # noqa: F401 (fixture)
from backend.tests.test_live_sequence_baseband_then_rru_reset import _CATALOG, _Conversation, _respond, _search

_PRE = """Alarm Handling and Restart Procedure
1. General Preconditions
• Once an alarm appears, wait 30 minutes before attempting any restart.
• Restart is allowed only if the alarm is active.
• Validate the alarm using altk:
– If the alarm is not found → No restart
– If the alarm is found → Proceed with restart
• Attach altk output on the synthetic alarm.
• No restart is allowed for 7 days on the same site and same RRU.
2. Post-Restart Actions
• If the alarm clears after restart → Close the action.
• If the alarm does not clear → Raise Field TT with Priority P4.
Restart failure handling:
• If restart fails → Retry after 15 minutes.
• If retry also fails → Raise Field TT.
Restart success handling:
• If restart is successful → Initiate mail notification.
3. Alarm-Specific Actions"""
_FIXED = "accn FieldReplaceableUnit=RRU-9 restartunit 1 1 1"
_TEMPLATE = "accn FieldReplaceableUnit=xxxx restartunit 1 1 1"
_HW_PARTIAL = f"• Restart is allowed only on RRU.\nCommand:\n{_FIXED}\n• If:\nSupportUnit=---\n→ No restart\n• For AAS units:\naccn FieldReplaceableUnit=AAS-1 restartunit 1 1 1"
_V2 = [
    (None, _PRE),
    ("HW Partial Fault", _HW_PARTIAL),
    ("HW Fault", "• Restart procedure same as HW Partial Fault.\n• For Antenna Group / Unit alarms:\n– Execute:\nhget near Rfportref\n– Fetch the associated RRU\n– Restart the identified RRU"),
    ("Linearization Disturbance – Performance Degraded", "• Restart on the identified RRU."),
    ("No Connection", "• Execute:\nhget near Rfportref\n• Example output:\nAntennaUnitGroup=6, AntennaNearUnit=1\n• Fetch the corresponding RRU.\n• Restart the identified RRU."),
    ("SW Error", f"• Restart allowed on RRU or BB.\nCommands:\n{_FIXED}\nOR\naccn FieldReplaceableUnit=BB-1 restartunit 1 1 1"),
    ("VSWR Over Threshold", "• No restart allowed.\n• Perform diagnosis only.\n• Validate the below values from alert key:\n– Return Loss: 8.6 dB\n– VSWR: 2.2"),
]
_V1 = _PRE + "\n" + "\n".join(f"{i}) {heading}\n{content}" for i, (heading, content) in enumerate(_V2[1:], start=1))
_DOC, _ALARM_KID = "DOC1-FIXTURE", "ALARM-CHECK-FIXTURE"


def _select(kid: str, version: str, section: str) -> Any:
    return _fc("knowledge_select_evidence", {"selections": [{"knowledge_id": kid, "version_label": version, "section_id": section}]})


def _doc(sections: list[tuple[Optional[str], str]], *, kid: str = _DOC, version: str = "v2", dims: Optional[dict] = None) -> KnowledgeObject:
    return KnowledgeObject(
        knowledge_id=kid, document_type=KnowledgeDocumentType.MOP, title="Document1",
        version=KnowledgeVersion(label=version, effective_from=datetime(2020, 1, 1, tzinfo=timezone.utc)),
        lifecycle_status=LifecycleStatus.APPROVED, applicability=Applicability(dimensions=dims if dims is not None else {}),
        source=KnowledgeSource(source_system="test", source_id="Document1.docx", display_name="Document1.docx"),
        sections=[
            KnowledgeSection(section_id=f"section-{i:04d}", knowledge_id=kid, heading=heading, sequence=i, content=content, source_locator=f"lines:{i}")
            for i, (heading, content) in enumerate(sections)
        ],
    )


def _extract(content: str, *, scope: Optional[dict] = None, section: str = "s", derived: bool = False):
    return pa.extract_procedure_actions(knowledge_id="K", version_label="v1", section_id=section, content=content,
                                        governed_scope=scope, artifact_derived=derived)


def _ev(content: str, **meta: Any) -> EvidenceReference:
    return EvidenceReference(
        source_id="K:v1:s", source_type="governed_knowledge", title="T", content_snippet=content,
        metadata={"knowledge_id": "K", "version_label": "v1", "section_id": "s", "lifecycle_status": "approved",
                  "applicability_outcome": "match", **meta},
    )


def _grounded(command: str, ev: EvidenceReference) -> bool:
    return ground_command_candidate({"command": command, "source_id": ev.source_id}, [ev], {ev.source_id: ev}) is not None


# =============================================================================================
# 1. taxonomy: literal fixed / explicit placeholder / ambiguous literal
# =============================================================================================


def test_literal_instance_is_fixed_and_never_parameterized() -> None:
    (rru9, aas1), skipped = _extract(_HW_PARTIAL)
    assert skipped == []
    assert (rru9.command_template, rru9.instance_semantics, rru9.parameters) == (_FIXED, ps.InstanceSemantics.FIXED_INSTANCE, [])
    assert [t.view() for t in rru9.targets] == [{"key": "FieldReplaceableUnit", "kind": "fixed", "parameter": None, "fixed_value": "RRU-9"}]
    assert rru9.action_type is pa.ProcedureActionType.STATE_CHANGE and rru9.state_change_class == "restart"
    assert aas1.instance_semantics is ps.InstanceSemantics.FIXED_INSTANCE and aas1.targets[0].fixed_value == "AAS-1"


def test_explicit_placeholder_is_a_parameterized_template_slot_only() -> None:
    (action,), _ = _extract(f"Command:\n{_TEMPLATE}\nReplace xxxx with the identified unit.")
    assert action.instance_semantics is ps.InstanceSemantics.PARAMETERIZED_TEMPLATE
    assert [(p.name, p.key, p.placeholder) for p in action.parameters] == [("FieldReplaceableUnit", "FieldReplaceableUnit", "xxxx")]
    assert [t.view() for t in action.targets] == [
        {"key": "FieldReplaceableUnit", "kind": "parameterized", "parameter": "FieldReplaceableUnit", "fixed_value": None}
    ]
    # Extraction declares the slot; it binds nothing (no value appears anywhere in the action).
    assert "RRU" not in str(action.governance_view())


def test_ambiguous_literal_under_generic_prose_stays_a_fixed_instance() -> None:
    (action,), _ = _extract(f"• Restart the identified RRU.\nCommand:\n{_FIXED}")
    assert action.instance_semantics is ps.InstanceSemantics.FIXED_INSTANCE
    assert action.semantics_reason.startswith("literal instance")
    assert action.command_template == _FIXED and not action.parameters


# =============================================================================================
# 2. explicit examples, sample output, transcripts, screenshot transcription
# =============================================================================================


@pytest.mark.parametrize(
    "content",
    [
        f"Example:\n{_FIXED}",
        f"Sample command:\n{_FIXED}",
        f"Restart the unit, e.g. `{_FIXED}`.",
        f"Restart the unit, for example `{_FIXED}`.",
        f"Command: `{_FIXED}` (example)",
        f"Command:\n{_FIXED}\nReplace RRU-9 with the identified unit.",
        f"Illustrative command:\n```\n{_FIXED}\n```",
    ],
)
def test_explicit_example_is_never_an_action_nor_grounding_text(content: str) -> None:
    actions, skipped = _extract(content)
    assert actions == []
    assert {k["template"]: k["semantics"] for k in skipped}[_FIXED] == "explicit_example"
    assert {k["semantics"] for k in skipped} == {"explicit_example"}
    assert "RRU-9 restartunit" not in pa.instruction_view(content)
    assert not _grounded(_FIXED, _ev(content))


@pytest.mark.parametrize(
    "content,command",
    [
        ("Example output:\nst fru\nFieldReplaceableUnit=RRU-2 ENABLED", "st fru"),
        ("Printout:\nst fru\nFieldReplaceableUnit=RRU-2 ENABLED", "st fru"),
        ("```\nNODE01> st fru\nFieldReplaceableUnit=RRU-2 ENABLED\n```", "st fru"),
        ("```output\nst fru\n```", "st fru"),
        ("Check the units.\nNODE01> st fru\nFieldReplaceableUnit=RRU-2 ENABLED", "st fru"),
        (f"Expected result:\n{_FIXED}", _FIXED),
    ],
)
def test_sample_output_and_cli_transcripts_never_become_actions_or_authority(content: str, command: str) -> None:
    actions, _ = _extract(content)
    assert actions == []
    assert not _grounded(command, _ev(content))
    # Positive control: the same command written as an instruction grounds and extracts.
    assert _grounded(command, _ev(f"Run `{command}`.\n{content}"))


@pytest.mark.parametrize("label", ["Screenshot:", "Figure 3:", "Screen capture:"])
def test_screenshot_transcription_label_is_never_an_action(label: str) -> None:
    for line, command in (("st FieldReplaceableUnit=RRU-2", "st FieldReplaceableUnit=RRU-2"), ("`st fru`", "st fru"), ("st fru", "st fru")):
        content = f"{label}\n{line}"
        actions, skipped = _extract(content)
        assert actions == [] and {k["semantics"] for k in skipped} <= {"screenshot_transcription"}
        assert not _grounded(command, _ev(content)), content
    assert _extract(f"{label}\nst FieldReplaceableUnit=RRU-2")[1][0]["semantics"] == "screenshot_transcription"


def test_section_transcribed_from_an_image_artifact_has_no_actions_and_no_grounding() -> None:
    content = "Unit check: `st fru`"
    assert _extract(content)[0], "control: as primary document text it is an action"
    actions, skipped = _extract(content, derived=True)
    assert actions == [] and skipped[0]["semantics"] == "screenshot_transcription"
    assert not _grounded("st fru", _ev(content, artifact_derived=True))
    image = KnowledgeArtifact(artifact_id="img-1", kind="image", depth=0, extracted_text=content, derived=True)
    obj = _doc([(None, content)]).model_copy(update={"artifacts": [image]})
    obj.sections[0].artifact_id = "img-1"
    assert ps.governed_document_scope(obj, "section-0000")["artifact_derived"] is True


# =============================================================================================
# 3. operation classifier (structural; no command name list)
# =============================================================================================


@pytest.mark.parametrize(
    "command,word",
    [
        (_FIXED, "restart"),
        (_TEMPLATE, "restart"),
        ("accn FieldReplaceableUnit=<unit> restartunit", "restart"),
        ("accn Equipment=1,FieldReplaceableUnit=RRU-9 restartunit", "restart"),
        ("acc FieldReplaceableUnit=RRU-9 restartunit 1 1 1", "restart"),
        ("accn FieldReplaceableUnit=RRU-9 resetunit", "reset"),
        ("act Unit=4 manualrestart", "manualrestart"),
        ("accn Unit=4 lockunit", "lock"),
    ],
)
def test_action_invocations_with_state_changing_action_words_are_state_changes(command: str, word: str) -> None:
    assert classify_command_operation(command) is CommandOperationType.MUTATING_OPERATIONAL
    assert state_change_class(command) == word


@pytest.mark.parametrize(
    "command,expected",
    [
        ("st FieldReplaceableUnit=RRU-9", CommandOperationType.READ_ONLY_DIAGNOSTIC),
        ("get FieldReplaceableUnit=1 restartCounter", CommandOperationType.READ_ONLY_DIAGNOSTIC),  # attribute, not an action
        ("hget near Rfportref", CommandOperationType.READ_ONLY_DIAGNOSTIC),
        ("alt", CommandOperationType.READ_ONLY_DIAGNOSTIC),
        ("accn FieldReplaceableUnit=RRU-9 lockState", CommandOperationType.UNKNOWN),
        (f"{_FIXED}; st fru", CommandOperationType.UNKNOWN),  # composed: never authorizable
    ],
)
def test_reads_and_non_actions_keep_their_classification(command: str, expected: CommandOperationType) -> None:
    assert classify_command_operation(command) is expected


# =============================================================================================
# 4. document structure: global + block conditions, condition scope (no fabricated relations)
# =============================================================================================


def test_global_preconditions_attach_from_the_same_governed_version_with_provenance() -> None:
    scope = ps.governed_document_scope(_doc(_V2), "section-0001")
    pre = [c for c in scope["document_conditions"] if c["kind"] == "precondition"]
    post = [c for c in scope["document_conditions"] if c["kind"] == "post_action"]
    assert [c["text"] for c in pre][0] == "• Once an alarm appears, wait 30 minutes before attempting any restart."
    assert {c["source"] for c in pre + post} == {f"{_DOC}:v2:section-0000"}
    assert pre[-1]["text"] == "• No restart is allowed for 7 days on the same site and same RRU." and pre[-1]["line"] == 9
    assert "• If the alarm does not clear → Raise Field TT with Priority P4." in [c["text"] for c in post]
    (rru9, _), _ = _extract(_HW_PARTIAL, scope=scope, section="section-0001")
    kinds = [c["kind"] for c in rru9.conditions]
    assert kinds.count("precondition") == 7 and kinds.count("post_action") == 7
    assert any("wait 30 minutes" in r for r in rru9.restrictions), "bound into the card / approval text"


def test_section_local_block_conditions_are_the_actions_own_block_verbatim() -> None:
    (rru9, _), _ = _extract(_HW_PARTIAL, scope=ps.governed_document_scope(_doc(_V2), "section-0001"), section="section-0001")
    assert [c["text"] for c in rru9.conditions if c["kind"] == "block_condition"] == [
        "• Restart is allowed only on RRU.", "• If:", "SupportUnit=---", "→ No restart", "• For AAS units:"
    ]
    # Single-section layout (v1): the same block conditions come from the numbered child block.
    actions, _ = _extract(_V1)
    rru9_v1 = next(a for a in actions if a.command_template == _FIXED)
    assert "• Restart is allowed only on RRU." in [c["text"] for c in rru9_v1.conditions]
    assert [c["kind"] for c in rru9_v1.conditions].count("precondition") == 7


def test_condition_scope_is_explicit_structure_only() -> None:
    v2 = _doc(_V2)
    assert ps.governed_document_scope(v2, "section-0001")["condition_scope"]["value"] == "HW Partial Fault"
    assert ps.governed_document_scope(v2, "section-0005")["condition_scope"]["value"] == "SW Error"
    assert ps.governed_document_scope(v2, "section-0000")["condition_scope"] is None
    # The same literal command governed under two alarm blocks applies to either named condition.
    rru9_v1 = next(a for a in _extract(_V1)[0] if a.command_template == _FIXED)
    assert [a["value"] for a in rru9_v1.condition_scope["alternatives"]] == ["HW Partial Fault", "SW Error"]
    # Cross references ("same as ...") and prose restarts create nothing.
    assert _extract(_V2[2][1])[0] == [] and _extract(_V2[3][1])[0] == []
    # A document without a condition-specific outline has no scope and no conditions.
    (plain,), _ = _extract(f"Command:\n{_FIXED}")
    assert plain.condition_scope is None and plain.conditions == []


def test_condition_check_requires_the_exact_named_condition_in_trusted_results() -> None:
    scope = {"alternatives": [{"dimension": "alarm", "value": "HW Partial Fault"}]}
    hit = pa.check_condition_scope(scope, [{"step_id": "s1", "result_id": "r1", "text": "M  HW Partial Fault  FieldReplaceableUnit=RRU-2"}])
    assert hit["passed"] and hit["established"][0]["evidence"] == [{"step_id": "s1", "result_id": "r1"}]
    for text in ("M  Link Failure  FieldReplaceableUnit=RRU-2", "M  HW Fault  FieldReplaceableUnit=RRU-2", "HW Partial Faults"):
        assert not pa.check_condition_scope(scope, [{"step_id": "s", "result_id": "r", "text": text}])["passed"], text
    assert not pa.check_condition_scope({"alternatives": [{"dimension": "alarm", "value": None}]}, [{"text": "anything"}])["passed"]


def test_state_change_without_established_governed_scope_fails_closed_reads_do_not() -> None:
    content = f"Unit check: `st fru`\nCommand:\n{_TEMPLATE}"
    actions = {a.command_template: a for a in _extract(content)[0]}
    for scope in ({"status": "unavailable", "reason": "governed version could not be loaded"}, None):
        ev = _ev(content, governed_scope=scope)
        restart, _ = pa.resolve_procedure_action(actions[_TEMPLATE].action_id, issued_ids={actions[_TEMPLATE].action_id}, selected_evidence=[ev])
        assert restart.status is pa.ProcedureActionResolutionStatus.GOVERNED_SCOPE_UNAVAILABLE and restart.candidate is None
        read, _ = pa.resolve_procedure_action(actions["st fru"].action_id, issued_ids={actions["st fru"].action_id}, selected_evidence=[ev])
        assert read.status is pa.ProcedureActionResolutionStatus.RESOLVED


# =============================================================================================
# 5. production chain (ChatService + TAE tool + control plane), Document1 replica
# =============================================================================================
_ALARM_SECTION = "Active alarm check: `alt`"
_ALARM_ACTION = pa.extract_procedure_actions(knowledge_id=_ALARM_KID, version_label="v1", section_id="section-0000", content=_ALARM_SECTION)[0][0].action_id
_RESTART_REQUEST = {"subject_component": "radio unit", "requested_operation": "restart", "continues_active_objective": True}
_FACTS = {"known_applicability_facts": {"vendor": ["ericsson"], "technology": ["4g"]}}


def _action_id(template: str, kid: str = _DOC, version: str = "v2", section: str = "section-0001", content: str = _HW_PARTIAL) -> str:
    return next(a.action_id for a in pa.extract_procedure_actions(knowledge_id=kid, version_label=version, section_id=section, content=content)[0]
                if a.command_template == template)


async def _seed(repo: Any, doc: KnowledgeObject) -> None:
    await repo.add(_doc([(None, _ALARM_SECTION)], kid=_ALARM_KID, version="v1"))
    await repo.add(doc)


async def _alarm_observed(conv: _Conversation, alarm_line: str) -> None:
    step = [_search("active alarm check"), _select(_ALARM_KID, "v1", "section-0000"), _CATALOG]
    t1 = await conv.turn("radio unit alarm on an Ericsson 4G node, what should I check?",
                         step + [_respond(action_id=_ALARM_ACTION, action="List the active alarms")], "Run alt.",
                         {"subject_component": "radio unit"}, _FACTS)
    assert t1["record"]["diagnostic_step"]["command"] == "alt"
    await conv.turn(f"alt\n{alarm_line}", step[:2] + [_respond(outcome="insufficient_evidence")], "Noted.")


async def _restart(conv: _Conversation, action_id: str, version: str = "v2") -> dict[str, Any]:
    step = [_search("HW Partial Fault restart unit"), _select(_DOC, version, "section-0001"), _CATALOG]
    return await conv.turn("restart it", step + [_respond(action_id=action_id, action="Restart the faulty unit")], "Run the restart.", _RESTART_REQUEST)


def _gate(turn: dict[str, Any]) -> dict[str, Any]:
    (gate,) = [e for e in turn["trace"]["operational_events"] if e.get("stage") == "target_gate"]
    return gate


def _no_state_change_offered(turn: dict[str, Any]) -> None:
    record = turn["record"]
    assert record["procedure_action_resolution"]["rendered_command"] is None
    assert "operational_control" not in record and load_active_proposal(turn["state"]) is None
    assert not [c for c in turn["trace"].get("command_authority", []) if "restartunit" in c["command"] and c["decision"] != "rejected"]
    assert "restartunit" not in turn["final"]


@pytest.mark.asyncio
async def test_document1_fixed_rru9_is_never_substituted_for_the_case_target(isolated_km_repo, monkeypatch) -> None:  # noqa: F811
    await _seed(isolated_km_repo, _doc(_V2, dims={"vendor": ["ericsson"], "technology": ["4g"]}))
    conv = _Conversation(monkeypatch)
    await _alarm_observed(conv, "2026-10-02 11:01:45 M HW Partial Fault  FieldReplaceableUnit=RRU-2")
    t3 = await _restart(conv, _action_id(_FIXED))
    catalog = t3["trace"]["action_catalogs"][-1]["actions"]
    entry = next(a for a in catalog if a["command_template"] == _FIXED)
    assert (entry["instance_semantics"], entry["state_change_class"]) == ("fixed_instance", "restart")
    gate = _gate(t3)
    assert gate["condition_check"]["passed"] is True, "the scoped alarm IS observed in this fault"
    assert [(t["status"], t["value"]) for t in gate["targets"]] == [("fixed_target_not_in_case", "RRU-9")]
    assert t3["record"]["procedure_action_resolution"]["status"] == "target_not_validated"
    _no_state_change_offered(t3)


@pytest.mark.asyncio
async def test_restart_scoped_to_another_alarm_is_not_applicable_despite_vendor_technology_match(isolated_km_repo, monkeypatch) -> None:  # noqa: F811
    await _seed(isolated_km_repo, _doc(_V2, dims={"vendor": ["ericsson"], "technology": ["4g"]}))
    conv = _Conversation(monkeypatch)
    await _alarm_observed(conv, "2026-10-02 11:01:45 M Link Failure  FieldReplaceableUnit=RRU-9")
    t3 = await _restart(conv, _action_id(_FIXED))
    search = t3["trace"]["searches"][0]
    assert next(r for r in search["results"] if r["selection_state"] == "SELECTED")["applicability_outcome"] == "match"
    resolution = t3["record"]["procedure_action_resolution"]
    assert resolution["status"] == "condition_not_established"
    assert resolution["condition_check"]["required_any_of"][0]["value"] == "HW Partial Fault"
    assert "HW Partial Fault" in t3["record"]["target_clarification"]["text"]
    _no_state_change_offered(t3)


@pytest.mark.asyncio
async def test_explicit_placeholder_binds_only_through_target_authority_and_approval_covers_global_preconditions(isolated_km_repo, monkeypatch) -> None:  # noqa: F811
    amended = [(h, c.replace(_FIXED, _TEMPLATE)) for h, c in _V2]
    await _seed(isolated_km_repo, _doc(amended, version="v3"))
    conv = _Conversation(monkeypatch)
    await _alarm_observed(conv, "2026-10-02 11:01:45 M HW Partial Fault  FieldReplaceableUnit=RRU-2")
    t3 = await _restart(conv, _action_id(_TEMPLATE, version="v3", content=amended[1][1]), version="v3")
    gate = _gate(t3)
    assert gate["passed"] is True and [(t["status"], t["value"]) for t in gate["targets"]] == [("validated", "RRU-2")]
    assert t3["record"]["procedure_action_resolution"]["rendered_command"] == "accn FieldReplaceableUnit=RRU-2 restartunit 1 1 1"
    assert t3["record"]["diagnostic_step"]["command"] is None, "a state change is never an in-turn command"
    card = load_active_proposal(t3["state"])
    state = dict((await conv.sessions.get_session(conv.session_id, "eng")).state)
    control = cp.OperationalControlRecord.model_validate(list(state[cp.OPERATIONAL_CONTROLS_STATE_KEY].values())[-1])
    assert any("wait 30 minutes" in c["text"] and c["source"].endswith("section-0000") for c in control.context.governed_conditions)
    assert [a["value"] for a in control.context.condition_scope["alternatives"]] == ["HW Partial Fault"]
    assert "governed_conditions" in control.context.binding()
    approval = (await approval_service.approve(conv.sessions, conv.session_id, card.proposal_id, "eng")).pending_action
    assert "wait 30 minutes" in " ".join(approval.operational.restrictions)

    # The governed general preconditions change in place: the approval no longer covers them.
    edited = _doc([(None, _PRE.replace("30 minutes", "60 minutes"))] + amended[1:], version="v3")
    await isolated_km_repo.replace(edited)
    with pytest.raises(SafeErrorException) as refused:
        await approval_service.approve(conv.sessions, conv.session_id, approval.proposal_id, "eng")
    assert refused.value.safe_error.reason == "SOURCE_CONDITIONS_CHANGED"
