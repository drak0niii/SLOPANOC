"""Phase 6A.9: deterministic rendering -- trust labels are explicit and
distinct, retrieved/historical content is wrapped in DATA delimiters,
and the renderer never reasons (no answer/infer/recommend-shaped
function anywhere in the module)."""
from __future__ import annotations

import ast
import inspect

import backend.troubleshooting_intelligence.rendering as rendering_module
from backend.troubleshooting_intelligence.assembly import assemble_troubleshooting_intelligence
from backend.troubleshooting_intelligence.contracts import SkillSelectionOutcome, TroubleshootingIntelligenceInput
from backend.troubleshooting_intelligence.rendering import render_troubleshooting_intelligence_as_text

from ._fixtures import make_context_package, make_experience_record, make_skill


def test_all_four_trust_sections_present_and_distinct() -> None:
    skill = make_skill()
    record = make_experience_record("exp-1")
    package = make_context_package()
    input_ = TroubleshootingIntelligenceInput(
        owner_id="OWNER-1",
        objective="what next?",
        context_package=package,
        skill=skill,
        skill_fingerprint="fp",
        skill_selection_outcome=SkillSelectionOutcome.SELECTED,
        experience_records=[record],
    )
    intelligence = assemble_troubleshooting_intelligence(input_)
    text = render_troubleshooting_intelligence_as_text(intelligence)

    assert "TELCO CONTEXT:" in text
    assert "SKILL METHODOLOGY" in text
    assert "HISTORICAL EXPERIENCE (NON-AUTHORITATIVE" in text
    assert "KNOWLEDGE EVIDENCE" in text
    # Trust sections must appear in a stable, distinguishable order.
    assert text.index("SKILL METHODOLOGY") < text.index("HISTORICAL EXPERIENCE")


def test_data_delimiters_wrap_retrieved_and_historical_content() -> None:
    record = make_experience_record("exp-1", outcome_summary="a historical fact")
    package = make_context_package()
    input_ = TroubleshootingIntelligenceInput(owner_id="OWNER-1", context_package=package, skill_selection_outcome=SkillSelectionOutcome.NONE_REGISTERED, experience_records=[record])
    intelligence = assemble_troubleshooting_intelligence(input_)
    text = render_troubleshooting_intelligence_as_text(intelligence)
    assert "<<<DATA" in text and "DATA>>>" in text
    # The experience outcome summary appears inside a DATA block.
    data_start = text.index("a historical fact")
    preceding_open = text.rfind("<<<DATA", 0, data_start)
    preceding_close = text.rfind("DATA>>>", 0, data_start)
    assert preceding_open > preceding_close, "historical content must be inside an open DATA block"


def test_no_skill_selected_states_it_plainly() -> None:
    package = make_context_package()
    input_ = TroubleshootingIntelligenceInput(owner_id="OWNER-1", context_package=package, skill_selection_outcome=SkillSelectionOutcome.NONE_APPLICABLE)
    intelligence = assemble_troubleshooting_intelligence(input_)
    text = render_troubleshooting_intelligence_as_text(intelligence)
    assert "no registered methodology applies" in text


def test_renderer_defines_no_reasoning_shaped_function() -> None:
    tree = ast.parse(inspect.getsource(rendering_module))
    forbidden_fragments = ("answer", "infer", "recommend", "resolve", "diagnose")
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef):
            lowered = node.name.lower()
            for fragment in forbidden_fragments:
                assert fragment not in lowered, f"unexpected reasoning-shaped function name: {node.name}"
