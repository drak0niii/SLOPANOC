"""Phase 6A.10 core test matrix -- TOPOLOGY / TOOL REGISTRATION: Team
Manager exposes both specialists as siblings, neither specialist can
call the other, no Router Agent was introduced, and the existing
Incident Manager wiring is unaffected.
"""
from __future__ import annotations

import ast
from pathlib import Path

from backend.agents.team_manager.agent import presentation_team_manager, team_manager
from backend.agents.team_manager.troubleshooting_tool import TOOL_NAME

_BACKEND_DIR = Path(__file__).resolve().parents[2]
_TEAM_MANAGER_DIR = _BACKEND_DIR / "agents" / "team_manager"
_INCIDENT_MANAGER_DIR = _BACKEND_DIR / "agents" / "incident_manager"
_TROUBLESHOOTING_MANAGER_DIR = _BACKEND_DIR / "agents" / "troubleshooting_manager"


def _tool_names(agent) -> set[str]:
    return {getattr(tool, "name", None) or getattr(tool, "__name__", None) for tool in agent.tools}


def test_team_manager_exposes_both_specialists() -> None:
    names = _tool_names(team_manager)
    assert "incident_manager" in names
    assert TOOL_NAME in names
    assert TOOL_NAME == "troubleshooting_manager"


def test_no_duplicate_tool_registration() -> None:
    raw_names = [getattr(tool, "name", None) or getattr(tool, "__name__", None) for tool in team_manager.tools]
    assert len(raw_names) == len(set(raw_names)), f"duplicate tool registration found: {raw_names}"


def test_presentation_team_manager_still_has_zero_tools() -> None:
    """R1 FIX's own invariant (agent.py) must survive the 6A.10 addition
    -- trusted-result presentation mode must still be structurally
    incapable of calling ANY tool, including the new one."""
    assert presentation_team_manager.tools == []
    assert presentation_team_manager.before_tool_callback is None
    assert presentation_team_manager.after_tool_callback is None


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


def test_incident_manager_package_never_imports_troubleshooting_manager() -> None:
    violations = []
    for path in sorted(_INCIDENT_MANAGER_DIR.glob("*.py")):
        for module_name in _imported_module_names(path):
            if module_name.startswith("backend.agents.troubleshooting_manager") or module_name.startswith("backend.troubleshooting_intelligence"):
                violations.append((path.name, module_name))
    assert violations == [], f"incident_manager must never import troubleshooting_manager: {violations}"


def test_troubleshooting_manager_package_never_imports_incident_manager() -> None:
    violations = []
    for path in sorted(_TROUBLESHOOTING_MANAGER_DIR.glob("*.py")):
        for module_name in _imported_module_names(path):
            if module_name.startswith("backend.agents.incident_manager"):
                violations.append((path.name, module_name))
    assert violations == [], f"troubleshooting_manager must never import incident_manager: {violations}"


def test_troubleshooting_tool_module_never_imports_troubleshooting_manager_internals_beyond_runtime() -> None:
    """team_manager's own wrapper (troubleshooting_tool.py) must call
    ONLY the canonical 6A.9 direct-invocation surface
    (`run_troubleshooting_assessment`) -- never `skill_resolution`/
    `experience_support`/`troubleshooting_intelligence.assembly`
    directly, which would duplicate the 6A.9 preparation pipeline
    (instruction section 34/45)."""
    path = _TEAM_MANAGER_DIR / "troubleshooting_tool.py"
    text = path.read_text(encoding="utf-8")
    assert "from backend.agents.troubleshooting_manager.runtime import run_troubleshooting_assessment" in text
    forbidden = (
        "backend.agents.troubleshooting_manager.skill_resolution",
        "backend.agents.troubleshooting_manager.experience_support",
        "backend.troubleshooting_intelligence.assembly",
    )
    for fragment in forbidden:
        assert fragment not in text, f"troubleshooting_tool.py must not duplicate 6A.9 preparation by importing {fragment}"


def test_no_router_agent_or_skill_router_agent_introduced() -> None:
    violations = []
    for directory in (_TEAM_MANAGER_DIR, _TROUBLESHOOTING_MANAGER_DIR):
        for path in sorted(directory.glob("*.py")):
            text = path.read_text(encoding="utf-8")
            for forbidden in ("RouterAgent", "IntentAgent", "DispatchAgent", "SkillRouterAgent", "OrchestrationAgent"):
                if forbidden in text:
                    violations.append((path.name, forbidden))
    assert violations == []


def test_team_manager_does_not_import_experience_memory_skills_or_hybrid_retrieval_directly() -> None:
    """instruction sections 90/91: Team Manager itself (not the
    troubleshooting_tool wrapper) must never import Experience Memory,
    Skill resolution, or Knowledge/hybrid-retrieval services directly."""
    forbidden_prefixes = (
        "backend.experience_memory.sqlalchemy",
        "backend.skills.registry",
        "backend.skills.loader",
        "backend.skills.readiness",
        "backend.skills.applicability",
        "backend.knowledge.hybrid_retrieval.service",
        "backend.knowledge.hybrid_retrieval.repository",
    )
    violations = []
    for path in sorted(_TEAM_MANAGER_DIR.glob("*.py")):
        if path.name == "troubleshooting_tool.py":
            continue  # the wrapper itself is audited separately below -- it also must not import these
        for module_name in _imported_module_names(path):
            if module_name.startswith(forbidden_prefixes):
                violations.append((path.name, module_name))
    assert violations == []

    # The wrapper itself must ALSO never import these -- it only calls
    # run_troubleshooting_assessment, which internally uses them.
    wrapper_path = _TEAM_MANAGER_DIR / "troubleshooting_tool.py"
    for module_name in _imported_module_names(wrapper_path):
        assert not module_name.startswith(forbidden_prefixes), f"troubleshooting_tool.py must not import {module_name} directly -- it belongs inside the 6A.9 preparation pipeline"


def test_troubleshooting_tool_does_not_import_context_engineering_context_sqlalchemy() -> None:
    """instruction section 41: 6A.6 Context Engineering itself is frozen
    -- the wrapper may only call its existing, unmodified `assemble_
    context_package`, never reimplement or import its internal
    TelcoContextService persistence (backend.context.sqlalchemy),
    confirming 6A.10 does not silently start writing/reading live TELCO
    Context profiles."""
    wrapper_path = _TEAM_MANAGER_DIR / "troubleshooting_tool.py"
    imported = _imported_module_names(wrapper_path)
    assert "backend.context_engineering.assembly" in imported
    for module_name in imported:
        assert not module_name.startswith("backend.context.sqlalchemy"), "must not touch live TelcoContextProfile persistence in 6A.10"
