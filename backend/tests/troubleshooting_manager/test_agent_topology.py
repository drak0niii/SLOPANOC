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


_APP_FILE = _BACKEND_DIR / "api" / "app.py"
_ROUTE_DECORATOR_METHODS = frozenset({"get", "post", "put", "patch", "delete", "websocket"})


def _is_route_decorator(decorator: ast.expr) -> bool:
    """True for `@app.get(...)`/`@app.post(...)`/etc. (any HTTP-verb-named
    attribute access called on an object -- deliberately not tied to a
    specific variable name like `app`, so this stays correct even if the
    FastAPI instance is ever renamed)."""
    call_func = decorator.func if isinstance(decorator, ast.Call) else decorator
    return isinstance(call_func, ast.Attribute) and call_func.attr in _ROUTE_DECORATOR_METHODS


def _route_handler_source_segments(tree: ast.Module, source: str) -> list[str]:
    """Every function body (source text) that is directly decorated as a
    FastAPI route handler in `app.py` -- the ONLY place this codebase
    registers a public HTTP endpoint (verified: every `@app.<verb>(...)`
    route in this repository lives in this one file, never scattered
    across `backend/api/*.py`)."""
    segments: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        if not any(_is_route_decorator(dec) for dec in node.decorator_list):
            continue
        segment = ast.get_source_segment(source, node)
        if segment:
            segments.append(segment)
    return segments


def test_no_route_from_api_layer_to_troubleshooting_manager() -> None:
    """§104/§105's own real security intent, preserved: no public HTTP
    route/endpoint directly exposes this specialist.

    CONTROL-PLANE-SEQ-06 -- reconciled with the now-legitimate SEQ-04
    architecture: `chat_service.py` (never itself an HTTP route handler --
    it is called BY the route handlers in `app.py`, the actual FastAPI
    registration surface) now performs internal, capability-filtered
    ROUTING among `WorkEnvelope`-selected Team Manager Runner variants,
    which necessarily means it references the name `troubleshooting_
    manager` (agent/tool identity, capability flags, Runner construction)
    -- a fundamentally different thing from a route DIRECTLY invoking the
    specialist, bypassing Team Manager's own orchestration/governance
    entirely, which is what this test's own docstring always actually
    warned against. The blanket "the string must never appear anywhere in
    backend/api/" proxy predates that legitimate architecture and is no
    longer the correct check.

    Reformulated as the PRECISE, still fully automated structural check:
    no FastAPI route handler (`@app.get/post/put/patch/delete/websocket`
    in `app.py` -- the sole place this codebase registers a public HTTP
    endpoint) may reference `troubleshooting_manager` in its OWN body at
    all. `chat_service.py`'s own internal routing is never inspected here
    (deliberately -- it is not a route handler and never receives a
    request directly), preserving the original invariant exactly while no
    longer flagging legitimate internal orchestration.
    """
    source = _APP_FILE.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(_APP_FILE))
    violations = [
        segment for segment in _route_handler_source_segments(tree, source) if "troubleshooting_manager" in segment
    ]
    assert violations == [], f"a public HTTP route handler directly references troubleshooting_manager: {violations}"
