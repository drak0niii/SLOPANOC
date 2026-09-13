"""Phase 6A.9 core test matrix -- AGENT TOPOLOGY, UPDATED IN PLACE FOR
PHASE 6A.10: at 6A.9's own closure, `team_manager` had NO import of
`backend.agents.troubleshooting_manager` at all (it was not yet wired to
any live turn). Phase 6A.10 (Dual-Specialist Orchestration) legitimately
changes this: `team_manager`'s own `troubleshooting_tool.py` wrapper now
imports the canonical 6A.9 direct-invocation surface (`.runtime.run_
troubleshooting_assessment`) and its typed request/response contracts
(`.schemas`) -- see that module's own docstring for why this is the
correct, narrow integration point (a plain FunctionTool, never an
`AgentTool`, never a duplicate of Intelligence Assembly). What remains
PROHIBITED, and is what this test file now actually proves: `team_
manager` may NEVER import the raw `troubleshooting_manager` ADK `Agent`
object (`.agent`) or 6A.9's own internal preparation pipeline
(`.skill_resolution`/`.experience_support`) directly -- those must stay
encapsulated behind `run_troubleshooting_assessment`. `incident_manager`
remains untouched; `troubleshooting_manager` itself remains a
structurally tool-less, non-user-facing, non-transferable specialist.
"""
from __future__ import annotations

import ast
from pathlib import Path

_BACKEND_DIR = Path(__file__).resolve().parents[2]
_TEAM_MANAGER_DIR = _BACKEND_DIR / "agents" / "team_manager"
_INCIDENT_MANAGER_DIR = _BACKEND_DIR / "agents" / "incident_manager"
_TROUBLESHOOTING_MANAGER_DIR = _BACKEND_DIR / "agents" / "troubleshooting_manager"


def _imported_module_names(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                names.add(alias.name)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module)
    return names


_PERMITTED_TROUBLESHOOTING_MANAGER_IMPORTS = frozenset(
    {
        "backend.agents.troubleshooting_manager.runtime",
        "backend.agents.troubleshooting_manager.schemas",
        "backend.agents.troubleshooting_manager.context_support",
    }
)


def test_team_manager_never_imports_troubleshooting_manager_internals() -> None:
    """UPDATED FOR 6A.10 (see this file's own module docstring): `team_
    manager`'s own `troubleshooting_tool.py` wrapper is the ONE legitimate
    integration point, and it may import ONLY the canonical 6A.9 direct-
    invocation surface (`.runtime`), its typed contracts (`.schemas`), and
    -- ADDED by the 6A.10 corrective pass -- `.context_support` (the pure/
    coordinator module built specifically so `troubleshooting_tool.py`
    can supply real, live TELCO Context/Evidence rather than a hardcoded
    empty package; it is not part of 6A.9's own internal specialist
    preparation pipeline, unlike `.skill_resolution`/`.experience_
    support`, which remain forbidden here) -- never the raw `Agent`
    object (`.agent`) or that internal preparation pipeline."""
    violations = []
    for path in sorted(_TEAM_MANAGER_DIR.glob("*.py")):
        for module_name in _imported_module_names(path):
            if module_name.startswith("backend.agents.troubleshooting_manager") and module_name not in _PERMITTED_TROUBLESHOOTING_MANAGER_IMPORTS:
                violations.append((path.name, module_name))
    assert violations == [], f"team_manager must import only the canonical 6A.9 runtime/schemas surface, never troubleshooting_manager internals: {violations}"


def test_troubleshooting_manager_never_imports_team_manager_or_incident_manager() -> None:
    violations = []
    for path in sorted(_TROUBLESHOOTING_MANAGER_DIR.glob("*.py")):
        for module_name in _imported_module_names(path):
            if module_name.startswith("backend.agents.team_manager") or module_name.startswith("backend.agents.incident_manager"):
                violations.append((path.name, module_name))
    assert violations == [], f"troubleshooting_manager must not import team_manager/incident_manager: {violations}"


def test_team_manager_exposes_troubleshooting_manager_as_a_plain_function_tool_never_an_agent_tool() -> None:
    """UPDATED FOR 6A.10 (see this file's own module docstring):
    `team_manager.tools` now legitimately includes `troubleshooting_
    manager` -- but as a plain `FunctionTool` (no `.agent` attribute
    pointing at the real ADK `Agent`), never `AgentTool(agent=
    troubleshooting_manager)`. This is the structural proof that 6A.10
    did not silently switch to the AgentTool pattern for this specialist
    (see troubleshooting_tool.py's own docstring for why)."""
    from backend.agents.team_manager.agent import team_manager

    def tname(t):
        return getattr(t, "name", None) or getattr(t, "__name__", None)

    troubleshooting_tools = [t for t in team_manager.tools if tname(t) == "troubleshooting_manager"]
    assert len(troubleshooting_tools) == 1
    assert getattr(troubleshooting_tools[0], "agent", None) is None, "troubleshooting_manager must be a plain function/FunctionTool, never an AgentTool"


def test_troubleshooting_manager_has_no_tools() -> None:
    from backend.agents.troubleshooting_manager.agent import troubleshooting_manager

    assert troubleshooting_manager.tools == []


def test_troubleshooting_manager_cannot_transfer() -> None:
    from backend.agents.troubleshooting_manager.agent import troubleshooting_manager

    assert troubleshooting_manager.disallow_transfer_to_parent is True
    assert troubleshooting_manager.disallow_transfer_to_peers is True


def test_troubleshooting_manager_has_output_schema_but_no_input_schema() -> None:
    from backend.agents.troubleshooting_manager.agent import troubleshooting_manager
    from backend.agents.troubleshooting_manager.schemas import TroubleshootingManagerResponse

    assert troubleshooting_manager.output_schema is TroubleshootingManagerResponse
    assert troubleshooting_manager.input_schema is None, "not wired as an AgentTool in this milestone -- no input_schema is needed or set"


def test_no_route_from_api_layer_to_troubleshooting_manager() -> None:
    """§104/§105: no new public HTTP route/endpoint exposes this
    specialist -- confirmed by an empty grep across the API layer."""
    api_dir = _BACKEND_DIR / "api"
    violations = []
    for path in sorted(api_dir.glob("*.py")):
        text = path.read_text(encoding="utf-8")
        if "troubleshooting_manager" in text:
            violations.append(path.name)
    assert violations == [], f"no backend/api/ file may reference troubleshooting_manager in this milestone: {violations}"
