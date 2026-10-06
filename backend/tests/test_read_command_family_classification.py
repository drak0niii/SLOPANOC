"""Regression: a legitimate read-only `get`-family command (live: `hget near Rfportref`) was
rejected by Command Authority as `unknown operation type`.

The classifier now recognises the `get` read family with a one-letter modifier, structurally
(no command/vendor/object list) and only as a single invocation. Classification never authorizes:
SELECTED governed evidence, grounding, applicability MATCH and Command Authority still apply.
"""
from __future__ import annotations

import pytest

from backend.agents.technical_authority_engineer import procedure_actions as pa
from backend.agents.technical_authority_engineer.agent_tool import build_server_validated_commands, classify_command_operation
from backend.agents.technical_authority_engineer.schemas import CommandOperationType, EvidenceReference

READ = CommandOperationType.READ_ONLY_DIAGNOSTIC


@pytest.mark.parametrize("command", ["hget near Rfportref", "lget Equipment=1", "kget", "get x", "hget X X"])
def test_get_family_single_invocations_are_read_only(command: str) -> None:
    assert classify_command_operation(command) is READ


@pytest.mark.parametrize(
    "command,expected",
    [
        ("hget near Rfportref; deb 3", CommandOperationType.MUTATING_OPERATIONAL),
        ("hget a && rm b", CommandOperationType.MUTATING_OPERATIONAL),
        ("hget $(reboot)", CommandOperationType.MUTATING_OPERATIONAL),
        ("hget x restart", CommandOperationType.MUTATING_OPERATIONAL),
        ("hget a | set b", CommandOperationType.UNKNOWN),
        ("hget a > file", CommandOperationType.UNKNOWN),
        ("hget a\nset b", CommandOperationType.UNKNOWN),
        ("xyzget a", CommandOperationType.UNKNOWN),
        ("getx a", CommandOperationType.UNKNOWN),
    ],
)
def test_chained_substituted_or_lookalike_forms_are_never_read_only(command: str, expected: CommandOperationType) -> None:
    assert classify_command_operation(command) is expected


def _ev(content: str, applicability: str = "match", lifecycle: str = "approved") -> EvidenceReference:
    return EvidenceReference(
        source_id="K:v1:s",
        source_type="governed_knowledge",
        title="Procedure",
        content_snippet=content,
        metadata={"knowledge_id": "K", "version_label": "v1", "section_id": "s", "lifecycle_status": lifecycle, "applicability_outcome": applicability},
    )


def _authorize(ev: EvidenceReference, command: str = "hget near Rfportref") -> list[str]:
    return [c.command for c in build_server_validated_commands([ev], [{"command": command, "source_id": "K:v1:s"}])]


def test_grounded_selected_match_read_is_now_authorized() -> None:
    assert _authorize(_ev("Check the port reference first: `hget near Rfportref`")) == ["hget near Rfportref"]


@pytest.mark.parametrize(
    "ev",
    [
        _ev("Check alarms: `alt`"),  # not grounded in the selected section
        _ev("Check: `hget near Rfportref`", applicability="unknown"),
        _ev("Check: `hget near Rfportref`", lifecycle="candidate"),
        _ev("Do not run `hget near Rfportref` during upgrades."),  # prohibited in source
    ],
)
def test_classification_alone_never_authorizes(ev: EvidenceReference) -> None:
    assert _authorize(ev) == []


def test_get_family_template_becomes_a_diagnostic_read_procedure_action() -> None:
    actions, skipped = pa.extract_procedure_actions(
        knowledge_id="K", version_label="v1", section_id="s", content="Port reference: `hget <mo> <attribute>`"
    )
    assert [(a.command_template, a.action_type) for a in actions] == [("hget <mo> <attribute>", pa.ProcedureActionType.DIAGNOSTIC_READ)]
    assert skipped == []
