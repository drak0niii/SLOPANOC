"""Phase 6A.7 core test matrix -- NO EXECUTION, NO KNOWLEDGE BYPASS, NO
CAPABILITY DISCOVERY (§37/§56/§57/§78). Complements `test_dependency_
boundary.py`'s import-level checks with call-level AST proofs.
"""
from __future__ import annotations

import ast
import inspect

import backend.skills.applicability as applicability_module
import backend.skills.contracts as contracts_module
import backend.skills.fingerprint as fingerprint_module
import backend.skills.loader as loader_module
import backend.skills.readiness as readiness_module
import backend.skills.registry as registry_module
import backend.skills.rendering as rendering_module
import backend.skills.versioning as versioning_module

_ALL_MODULES = [applicability_module, contracts_module, fingerprint_module, loader_module, readiness_module, registry_module, rendering_module, versioning_module]

_FORBIDDEN_CALL_NAMES = (
    "narrow_corpus",
    "narrow_knowledge",
    "hybrid_retrieve",
    "select_evidence",
    "list_all",
    "evaluate_applicability",  # 6A.4's Knowledge applicability -- never re-implemented/called here
    "knowledge_search",
    "knowledge_select_evidence",
    "run_async",  # ADK Runner
    "generate_content",
    "embed_content",
)


def _called_function_names(module) -> set[str]:
    tree = ast.parse(inspect.getsource(module))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            func = node.func
            if isinstance(func, ast.Name):
                names.add(func.id)
            elif isinstance(func, ast.Attribute):
                names.add(func.attr)
    return names


def test_no_module_calls_knowledge_llm_or_agent_functions() -> None:
    for module in _ALL_MODULES:
        called = _called_function_names(module)
        violations = called & set(_FORBIDDEN_CALL_NAMES)
        assert violations == set(), f"{module.__name__} must never call a Knowledge/LLM/agent-shaped function -- found: {violations}"


def test_no_module_writes_a_file_or_makes_a_network_call() -> None:
    """Beyond `loader.py`'s own deliberate, read-only `Path.read_text`
    (proven separately by `test_dependency_boundary.py`), no module in
    this package should write to disk or open a network connection."""
    forbidden_call_fragments = ("write_text", "write_bytes", "socket", "urlopen", "requests.post", "requests.get", "httpx.")
    for module in _ALL_MODULES:
        source = inspect.getsource(module)
        for fragment in forbidden_call_fragments:
            assert fragment not in source, f"{module.__name__} must never write files or make network calls -- found reference to {fragment!r}"


def test_no_skill_execution_engine_exists() -> None:
    """§41/§78: no function anywhere in this package EXECUTES a Skill,
    a methodology step, or a Tool -- structurally, by name."""
    forbidden_function_name_fragments = ("execute_skill", "execute_step", "execute_methodology", "run_skill", "invoke_tool", "invoke_capability")
    for module in _ALL_MODULES:
        function_names = [name for name, _ in inspect.getmembers(module, predicate=inspect.isfunction)]
        for name in function_names:
            for fragment in forbidden_function_name_fragments:
                assert fragment not in name.lower(), f"{module.__name__} defines an unexpected execution-shaped function: {name}"


def test_no_workflow_engine_exists() -> None:
    """§41: no state machine / DAG executor / scheduler / retry engine /
    branching engine anywhere in this package."""
    forbidden_class_name_fragments = ("workflowengine", "statemachine", "scheduler", "dagexecutor", "retryengine")
    for module in _ALL_MODULES:
        class_names = [name for name, _ in inspect.getmembers(module, predicate=inspect.isclass)]
        for name in class_names:
            for fragment in forbidden_class_name_fragments:
                assert fragment not in name.lower(), f"{module.__name__} defines an unexpected workflow-engine-shaped class: {name}"
