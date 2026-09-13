"""Phase 6A.7: deterministic, faithful text rendering of a
`SkillDefinition` (§55) -- mirrors `backend/context_engineering/
rendering.py`'s own discipline exactly.

THIS MODULE NEVER REASONS: it does not answer the current operational
question, infer root cause, select commands, recommend a next action,
invent Knowledge, or resolve UNKNOWN/CONFLICTING context -- it renders
exactly what the Skill definition already contains. The canonical Skill
model remains the structured `SkillDefinition` object; this is a
separate, optional adapter.
"""
from __future__ import annotations

from backend.skills.contracts import SkillDefinition

__all__ = ["render_skill_as_text"]


def render_skill_as_text(skill: SkillDefinition) -> str:
    lines = [
        f"SKILL: {skill.name} ({skill.skill_id} v{skill.version}, {skill.lifecycle.value})",
        "",
        "OBJECTIVE:",
        f"  {skill.objective}",
        "",
    ]

    lines.append("APPLICABILITY:")
    if not skill.applicability.conditions:
        lines.append("  (no scope restriction declared)")
    else:
        for condition in skill.applicability.conditions:
            lines.append(f"  - {condition.dimension.value} in {condition.allowed_values}")
    lines.append("")

    lines.append("REQUIREMENTS:")
    if skill.requires_case_context:
        lines.append("  - case context required")
    for requirement in skill.context_requirements:
        lines.append(f"  - {requirement.dimension.value} must be KNOWN")
    if skill.evidence_requirement.minimum_selected_items > 0:
        lines.append(f"  - at least {skill.evidence_requirement.minimum_selected_items} selected evidence item(s)")
    if skill.evidence_requirement.requires_source_evidence:
        lines.append("  - at least one SOURCE (non-derived) evidence item")
    if not skill.requires_case_context and not skill.context_requirements and skill.evidence_requirement.minimum_selected_items == 0 and not skill.evidence_requirement.requires_source_evidence:
        lines.append("  (none declared)")
    lines.append("")

    lines.append("METHODOLOGY:")
    if not skill.methodology:
        lines.append("  (no methodology steps declared)")
    else:
        for step in sorted(skill.methodology, key=lambda s: s.sequence):
            suffix = f" (requires capability: {step.required_capability})" if step.required_capability else ""
            lines.append(f"  {step.sequence + 1}. {step.title}{suffix}")
            if step.purpose:
                lines.append(f"     {step.purpose}")
    lines.append("")

    lines.append("CAPABILITIES:")
    if not skill.capability_requirements:
        lines.append("  (none declared)")
    else:
        for capability in skill.capability_requirements:
            lines.append(f"  - {capability}")
    lines.append("")

    lines.append("GUARDRAILS:")
    if not skill.guardrails:
        lines.append("  (none declared)")
    else:
        for guardrail in skill.guardrails:
            lines.append(f"  - {guardrail}")
    lines.append("")

    lines.append("EXPECTED OUTPUT:")
    lines.append(f"  {skill.expected_output}" if skill.expected_output else "  (not declared)")

    return "\n".join(lines) + "\n"
