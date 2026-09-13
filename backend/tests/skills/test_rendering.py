"""Phase 6A.7: the deterministic Skill text renderer (§55) -- faithful,
never reasoning.
"""
from __future__ import annotations

import ast
import inspect

from backend.context.domain.enums import ContextDimension
from backend.skills.contracts import ApplicabilityCondition, ContextRequirement, MethodologyStep, SkillApplicability, SkillDefinition, SkillLifecycle
from backend.skills.rendering import render_skill_as_text


def _skill(**overrides) -> SkillDefinition:
    defaults = dict(
        skill_id="telco.fault_diagnosis", version="1.0.0", lifecycle=SkillLifecycle.ACTIVE,
        name="Fault Diagnosis", description="d", objective="Establish context and review evidence.",
        applicability=SkillApplicability(conditions=[ApplicabilityCondition(dimension=ContextDimension.TECHNOLOGY, allowed_values=["LTE"])]),
        context_requirements=[ContextRequirement(dimension=ContextDimension.VENDOR)],
        methodology=[MethodologyStep(step_id="s1", sequence=0, title="Establish context"), MethodologyStep(step_id="s2", sequence=1, title="Review evidence")],
        capability_requirements=["alarms.read"],
        guardrails=["read-only methodology"],
    )
    defaults.update(overrides)
    return SkillDefinition(**defaults)


def test_renders_all_labelled_sections() -> None:
    rendered = render_skill_as_text(_skill())
    for label in ("SKILL:", "OBJECTIVE:", "APPLICABILITY:", "REQUIREMENTS:", "METHODOLOGY:", "CAPABILITIES:", "GUARDRAILS:", "EXPECTED OUTPUT:"):
        assert label in rendered


def test_faithfully_renders_content() -> None:
    rendered = render_skill_as_text(_skill())
    assert "Fault Diagnosis" in rendered
    assert "Establish context and review evidence." in rendered
    assert "Establish context" in rendered
    assert "Review evidence" in rendered
    assert "alarms.read" in rendered
    assert "read-only methodology" in rendered


def test_methodology_rendered_in_sequence_order_regardless_of_list_order() -> None:
    skill = _skill(methodology=[MethodologyStep(step_id="s2", sequence=1, title="second"), MethodologyStep(step_id="s1", sequence=0, title="first")])
    rendered = render_skill_as_text(skill)
    assert rendered.index("first") < rendered.index("second")


def test_empty_sections_render_explicit_none_marker() -> None:
    skill = SkillDefinition(skill_id="x", version="1.0.0", lifecycle=SkillLifecycle.ACTIVE, name="n", description="d", objective="o")
    rendered = render_skill_as_text(skill)
    assert "(no scope restriction declared)" in rendered
    assert "(none declared)" in rendered
    assert "(not declared)" in rendered


def test_renderer_source_never_reasons_or_answers() -> None:
    import backend.skills.rendering as module

    tree = ast.parse(inspect.getsource(module))
    forbidden_name_fragments = ("answer", "infer", "recommend", "resolve_conflict", "diagnose", "select_command")
    function_names = [node.name for node in ast.walk(tree) if isinstance(node, ast.FunctionDef)]
    for name in function_names:
        for fragment in forbidden_name_fragments:
            assert fragment not in name.lower(), f"unexpected reasoning-shaped function name: {name}"


def test_renderer_has_no_llm_or_agent_import() -> None:
    import backend.skills.rendering as module

    tree = ast.parse(inspect.getsource(module))
    imported = {node.module for node in ast.walk(tree) if isinstance(node, ast.ImportFrom) and node.module}
    imported |= {alias.name for node in ast.walk(tree) if isinstance(node, ast.Import) for alias in node.names}
    forbidden = ("google.adk", "google.genai", "backend.agents", "backend.tools")
    assert not any(m.startswith(f) for m in imported for f in forbidden)
