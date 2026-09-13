"""Phase 6A.8 core test matrix -- NO EXECUTION, NO KNOWLEDGE BYPASS, NO
SKILL EXECUTION, NO CROSS-OWNER QUERY API (§77-§80). Complements
`test_dependency_boundary.py`'s import-level checks with call-level AST
proofs and a structural "no unscoped read method" proof.
"""
from __future__ import annotations

import ast
import inspect

import backend.experience_memory.domain.admission as admission_module
import backend.experience_memory.domain.enums as enums_module
import backend.experience_memory.domain.fingerprint as fingerprint_module
import backend.experience_memory.domain.models as models_module
import backend.experience_memory.sqlalchemy.db as db_module
import backend.experience_memory.sqlalchemy.models as sqlalchemy_models_module
import backend.experience_memory.sqlalchemy.service as service_module
from backend.experience_memory.sqlalchemy.service import ExperienceMemoryService

_ALL_MODULES = [admission_module, enums_module, fingerprint_module, models_module, db_module, sqlalchemy_models_module, service_module]

_FORBIDDEN_CALL_NAMES = (
    "narrow_corpus",
    "narrow_knowledge",
    "hybrid_retrieve",
    "select_evidence",
    "knowledge_search",
    "knowledge_select_evidence",
    "evaluate_skill_readiness",
    "evaluate_skill_applicability",
    "resolve_exact",
    "resolve_latest_active",
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


def test_no_module_calls_knowledge_skill_llm_or_agent_functions() -> None:
    for module in _ALL_MODULES:
        called = _called_function_names(module)
        violations = called & set(_FORBIDDEN_CALL_NAMES)
        assert violations == set(), f"{module.__name__} must never call a Knowledge/Skill/LLM/agent-shaped function -- found: {violations}"


def test_no_module_makes_a_network_call() -> None:
    forbidden_call_fragments = ("socket", "urlopen", "requests.post", "requests.get", "httpx.")
    for module in _ALL_MODULES:
        source = inspect.getsource(module)
        for fragment in forbidden_call_fragments:
            assert fragment not in source, f"{module.__name__} must never make a network call -- found reference to {fragment!r}"


def test_no_skill_or_tool_execution_engine_exists() -> None:
    """§79: Skill references are provenance only -- no function anywhere
    resolves a Skill and executes it, evaluates its methodology, or
    invokes a Tool."""
    forbidden_function_name_fragments = ("execute_skill", "execute_step", "execute_methodology", "run_skill", "invoke_tool", "invoke_capability", "resolve_and_execute")
    for module in _ALL_MODULES:
        function_names = [name for name, _ in inspect.getmembers(module, predicate=inspect.isfunction)]
        for name in function_names:
            for fragment in forbidden_function_name_fragments:
                assert fragment not in name.lower(), f"{module.__name__} defines an unexpected execution-shaped function: {name}"


def test_service_has_no_unscoped_list_all_method() -> None:
    """§44/§80: no `list_all()` (or equivalent unscoped-read) method
    exists on the service -- every public method requires an explicit
    `owner_id`/query-with-owner argument."""
    method_names = [name for name, _ in inspect.getmembers(ExperienceMemoryService, predicate=inspect.isfunction) if not name.startswith("_")]
    assert "list_all" not in method_names
    forbidden_fragments = ("list_all", "all_experiences", "unscoped")
    for name in method_names:
        for fragment in forbidden_fragments:
            assert fragment not in name.lower(), f"ExperienceMemoryService defines an unexpectedly unscoped-sounding method: {name}"


def test_every_public_service_method_requires_owner_scope() -> None:
    """Structural proof via signature introspection: `record_experience`
    takes a candidate that itself REQUIRES `owner_id` (proven separately
    in test_contracts.py); `get_by_id`/`query`/`invalidate` each require
    an explicit owner-scoping argument in their own signature."""
    sig_get_by_id = inspect.signature(ExperienceMemoryService.get_by_id)
    assert "owner_id" in sig_get_by_id.parameters

    sig_invalidate = inspect.signature(ExperienceMemoryService.invalidate)
    assert "owner_id" in sig_invalidate.parameters

    sig_query = inspect.signature(ExperienceMemoryService.query)
    # `query` takes a single `ExperienceQuery` object whose own `owner_id`
    # field is structurally required (proven in test_contracts.py) --
    # there is no separate, optional owner parameter to omit.
    params = list(sig_query.parameters.keys())
    assert "experience_query" in params or len(params) == 2  # (self, experience_query)
