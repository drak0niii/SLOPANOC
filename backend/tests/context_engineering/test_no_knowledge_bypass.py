"""Phase 6A.6 core test matrix -- NO KNOWLEDGE BYPASS / NO REASONING
(§56/§57). Complements `test_dependency_boundary.py`'s import-level
checks with a source-level proof that `assembly.py` itself never CALLS
a narrowing/retrieval/applicability-evaluation function, even one it
could theoretically reach through a permitted contracts-only import.
"""
from __future__ import annotations

import ast
import inspect

import backend.context_engineering.assembly as assembly_module
import backend.context_engineering.contracts as contracts_module
import backend.context_engineering.fingerprint as fingerprint_module
import backend.context_engineering.rendering as rendering_module

_FORBIDDEN_CALL_NAMES = (
    "narrow_corpus",
    "narrow_knowledge",
    "hybrid_retrieve",
    "select_evidence",
    "list_all",
    "evaluate_applicability",
    "knowledge_search",
    "knowledge_select_evidence",
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


def test_assembly_module_never_calls_narrowing_or_retrieval_functions() -> None:
    called = _called_function_names(assembly_module)
    violations = called & set(_FORBIDDEN_CALL_NAMES)
    assert violations == set(), f"backend/context_engineering/assembly.py must never CALL a Knowledge search/retrieval/applicability function -- found: {violations}"


def test_no_context_engineering_module_calls_narrowing_or_retrieval_functions() -> None:
    for module in (assembly_module, contracts_module, fingerprint_module, rendering_module):
        called = _called_function_names(module)
        violations = called & set(_FORBIDDEN_CALL_NAMES)
        assert violations == set(), f"{module.__name__} must never call a Knowledge search/retrieval/applicability function -- found: {violations}"


def test_assembly_module_has_no_llm_or_agent_call() -> None:
    called = _called_function_names(assembly_module)
    forbidden_llm_fragments = ("generate_content", "embed_content", "run_async", "Runner", "Agent")
    for name in called:
        for fragment in forbidden_llm_fragments:
            assert fragment.lower() not in name.lower(), f"unexpected LLM/agent-shaped call: {name}"


def test_assembly_function_signature_takes_already_computed_input_only() -> None:
    """A structural proof of §25: `assemble_context_package` has exactly
    ONE parameter, and it is the fully-precomputed `ContextPackageInput`
    -- never a session_id/case_id alone that it would have to look up
    itself."""
    import typing

    sig = inspect.signature(assembly_module.assemble_context_package)
    params = list(sig.parameters.values())
    assert len(params) == 1
    hints = typing.get_type_hints(assembly_module.assemble_context_package)
    assert hints[params[0].name] is contracts_module.ContextPackageInput
