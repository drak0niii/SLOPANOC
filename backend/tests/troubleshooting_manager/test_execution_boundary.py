"""Phase 6A.9 core test matrix -- EXECUTION BOUNDARY: no tool execution,
no Teams/Power Automate/network-command capability, no iterative loop,
no Experience Memory WRITE capability (§29/§36/§70/§71/§116)."""
from __future__ import annotations

import ast
from pathlib import Path

_BACKEND_DIR = Path(__file__).resolve().parents[2]
_TROUBLESHOOTING_MANAGER_DIR = _BACKEND_DIR / "agents" / "troubleshooting_manager"

_FORBIDDEN_SUBSTRINGS = (
    "teams_",
    "power_automate",
    "PowerAutomate",
    "teams_create_chat",
    "teams_send_message",
    "execute_write",
    "propose_write",
    "record_experience",  # this package must never WRITE to Experience Memory
)


def _source_text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_no_teams_or_power_automate_references() -> None:
    violations = []
    for path in sorted(_TROUBLESHOOTING_MANAGER_DIR.glob("*.py")):
        text = _source_text(path)
        for fragment in _FORBIDDEN_SUBSTRINGS:
            if fragment in text:
                violations.append((path.name, fragment))
    assert violations == [], f"backend/agents/troubleshooting_manager must never reference Teams/Power Automate/write-execution/Experience-write capability: {violations}"


def test_experience_service_used_read_only() -> None:
    """A structural proof that this package's own Experience Memory
    usage is limited to `.query` -- never `.record_experience`/
    `.invalidate`."""
    path = _TROUBLESHOOTING_MANAGER_DIR / "experience_support.py"
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    called_attrs = {node.attr for node in ast.walk(tree) if isinstance(node, ast.Attribute)}
    assert "record_experience" not in called_attrs
    assert "invalidate" not in called_attrs
    assert "query" in called_attrs


def test_no_iterative_reasoning_loop() -> None:
    """§70/§71: `runtime.py` invokes the model AT MOST ONCE per call --
    no `while` loop wraps a model invocation, no retry-until-success
    pattern exists anywhere in this package (structurally distinct from
    the ALREADY-FROZEN, unrelated `provenance_compliance.py` one-retry
    mechanism this package never imports or reuses)."""
    for path in sorted(_TROUBLESHOOTING_MANAGER_DIR.glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            assert not isinstance(node, ast.While), f"{path.name} must not contain a while-loop (no iterative troubleshooting loop in this milestone)"


def test_no_reasoning_trace_database_module() -> None:
    """§95: no new persistence table/module for reasoning traces exists
    anywhere in this package."""
    violations = []
    for path in sorted(_TROUBLESHOOTING_MANAGER_DIR.glob("*.py")):
        text = _source_text(path)
        for fragment in ("agent_runs", "troubleshooting_runs", "reasoning_trace", "prompt_log"):
            if fragment in text:
                violations.append((path.name, fragment))
    assert violations == []


def test_no_chain_of_thought_field_on_response() -> None:
    """§96: the structured response never carries a raw model reasoning/
    chain-of-thought field."""
    from backend.agents.troubleshooting_manager.schemas import TroubleshootingManagerResponse

    field_names = set(TroubleshootingManagerResponse.model_fields)
    forbidden = {"chain_of_thought", "reasoning", "thoughts", "internal_reasoning"}
    assert field_names & forbidden == set()


def test_no_confidence_score_field() -> None:
    """§93: no invented numeric confidence field anywhere in the
    response contract."""
    from backend.agents.troubleshooting_manager.schemas import TroubleshootingManagerResponse

    for name, field in TroubleshootingManagerResponse.model_fields.items():
        assert "confidence" not in name.lower(), f"unexpected confidence-shaped field: {name}"
