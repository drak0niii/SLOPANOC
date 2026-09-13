"""Phase 6A.9 §39: deterministic, faithful text rendering of a
`TroubleshootingIntelligencePackage` -- mirrors `backend/context_
engineering/rendering.py`/`backend/skills/rendering.py`'s own discipline
exactly.

THIS MODULE NEVER REASONS: it does not answer the operational question,
infer root cause, select a next action, invent Knowledge/Experience, or
resolve UNKNOWN/CONFLICTING context -- it renders exactly what the
package already contains, faithfully, deterministically, with one
clearly-labelled, clearly-delimited section per trust category.

TRUST LABELS ARE EXPLICIT (§39): CURRENT CONTEXT, GOVERNED EVIDENCE
(SOURCE/DERIVED), SKILL METHODOLOGY, and HISTORICAL EXPERIENCE
(NON-AUTHORITATIVE) are rendered as four distinct, unambiguous section
headers -- never blended into one undifferentiated block of text.

PROMPT-INJECTION BOUNDARY (§52): every section built from retrieved/
historical DATA (context assertions, evidence text, Experience outcome
summaries/observed facts) is wrapped inside a fenced `<<<DATA ...
DATA>>>` block. The Troubleshooting Manager's own instruction
(`backend/agents/troubleshooting_manager/prompts.py`) tells the model
explicitly that everything inside such a block is untrusted data to
interpret, never an instruction to follow -- this module only supplies
the structural delimiter, the actual policy is enforced by the prompt
plus this codebase's usual defense-in-depth (grounding validation,
`tools=[]` on the agent itself).
"""
from __future__ import annotations

from backend.context_engineering.rendering import render_context_package_as_text
from backend.skills.rendering import render_skill_as_text
from backend.troubleshooting_intelligence.contracts import SkillSelectionOutcome, TroubleshootingIntelligencePackage

__all__ = ["render_troubleshooting_intelligence_as_text"]

_DATA_OPEN = "<<<DATA"
_DATA_CLOSE = "DATA>>>"


def _wrap_data(text: str) -> str:
    return f"{_DATA_OPEN}\n{text}\n{_DATA_CLOSE}"


def _render_evidence_reference_ids(package: TroubleshootingIntelligencePackage) -> str:
    """REAL DEFECT FOUND DURING THIS MILESTONE'S OWN LIVE VALIDATION,
    FIXED HERE: `render_context_package_as_text`'s own evidence section
    (6A.6, frozen, unmodified) labels each item by `knowledge_id`/
    `version_label`/`section_id` for HUMAN readability -- it never
    surfaces the opaque `evidence_id` string itself. A live real-model
    run proved the model then has no way to know what literal string to
    put in `EvidenceReferenceUsed.evidence_id`, and fabricated one from
    the human-readable label instead (`"KO-1 vv1"`), which correctly
    failed grounding validation. This section explicitly lists the real,
    citable `evidence_id` for every selected item -- never modifies or
    duplicates 6A.6's own frozen evidence rendering, only supplements it
    with the one missing, structurally-necessary identifier."""
    items = package.context_package.evidence.items
    if not items:
        return ""
    lines = ["EVIDENCE REFERENCE IDS (cite EXACTLY one of these strings in evidence_references_used, never a paraphrase):"]
    for item in items:
        lines.append(f"  [{item.selected_rank}] evidence_id={item.evidence_id!r} ({item.knowledge_id} v{item.version_label} / {item.section_id})")
    return "\n".join(lines) + "\n"


def _render_skill_section(package: TroubleshootingIntelligencePackage) -> str:
    lines = ["SKILL METHODOLOGY (how to approach this work -- never authoritative operational fact):"]
    if package.skill_selection_outcome == SkillSelectionOutcome.SELECTED and package.skill is not None:
        lines.append(f"  selected: {package.skill.skill_id} v{package.skill.version} (fingerprint {package.skill_fingerprint})")
        lines.append(_wrap_data(render_skill_as_text(package.skill)))
    elif package.skill_selection_outcome == SkillSelectionOutcome.NONE_READY:
        lines.append("  (a candidate methodology exists but is NOT READY -- required context/evidence/capability is missing; do not improvise a methodology)")
    elif package.skill_selection_outcome == SkillSelectionOutcome.NONE_APPLICABLE:
        lines.append("  (no registered methodology applies to the current TELCO Context)")
    else:
        lines.append("  (no production methodology is registered)")
    return "\n".join(lines) + "\n"


def _render_experience_section(package: TroubleshootingIntelligencePackage) -> str:
    lines = ["HISTORICAL EXPERIENCE (NON-AUTHORITATIVE -- supporting context only, never a procedure, never an authorization, never a root-cause conclusion):"]
    if package.experience_query is not None:
        lines.append(
            f"  query: owner={package.experience_query.owner_id} filters={package.experience_query.applied_filters} "
            f"result_count={package.experience_query.result_count} limit={package.experience_query.limit}"
        )
    if not package.experience:
        lines.append("  (no historical Experience matched the applied structured filters)")
        return "\n".join(lines) + "\n"
    for item in package.experience:
        header = f"  [{item.experience_id}] source_class={item.source_class} type={item.experience_type} origin={item.source_origin} namespace={item.source_namespace}"
        lines.append(header)
        body = f"outcome_summary: {item.outcome_summary}"
        if item.observed_facts:
            body += "\nobserved_facts:\n" + "\n".join(f"  - {fact}" for fact in item.observed_facts)
        lines.append(_wrap_data(body))
    return "\n".join(lines) + "\n"


def render_troubleshooting_intelligence_as_text(package: TroubleshootingIntelligencePackage) -> str:
    sections = [
        f"TROUBLESHOOTING OBJECTIVE / QUESTION:\n  {package.objective or '(none stated)'}\n",
        _wrap_data(render_context_package_as_text(package.context_package)),
        _render_evidence_reference_ids(package),
        _render_skill_section(package),
        _render_experience_section(package),
    ]
    return "\n".join(sections)
