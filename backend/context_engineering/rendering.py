"""Phase 6A.6: deterministic, faithful text rendering of a `ContextPackage`
(§28/§29) -- a SEPARATE adapter, never the canonical model itself. The
canonical model IS `ContextPackage`; this module only re-presents it as
text for a future model-consuming caller.

THIS MODULE NEVER REASONS (§29's own explicit prohibition): it does not
answer the query, infer RCA, recommend a troubleshooting action, resolve
a conflict, or rewrite evidence into a conclusion -- it renders exactly
what the package already contains, faithfully, deterministically, with
one clearly-labelled section per context domain.

TRUNCATION, WHEN IT HAPPENS, IS NEVER HIDDEN (§34): if the knowledge-
evidence section's rendered text would exceed `max_evidence_characters`,
each affected item's own rendered block is truncated at a stable
character boundary and annotated `[...truncated, N of M characters
shown...]` -- the item's own real `indexable_text` length (`M`) and the
rendered length (`N`) are both stated; the evidence item's OWN identity
(evidence_id/knowledge_id/...) is never itself truncated or hidden, only
its body text.
"""
from __future__ import annotations

from backend.context_engineering.contracts import ContextPackage

__all__ = ["render_context_package_as_text"]

_DEFAULT_MAX_EVIDENCE_ITEM_CHARACTERS = 4000
"""Deliberately generic, not query/corpus-tuned (matching this
codebase's own "deliberately generic" convention, e.g. 6A.5's
`RRF_K`) -- a per-ITEM budget, not a whole-section budget, so one very
long selected item never starves the visibility of the others."""


def _render_telco_section(package: ContextPackage) -> str:
    if not package.telco_context:
        return "TELCO CONTEXT:\n  (no TELCO context dimensions asserted)\n"
    lines = ["TELCO CONTEXT:"]
    for view in package.telco_context:
        lines.append(f"  {view.dimension.value}: {view.state.value}")
        for assertion in view.accepted:
            lines.append(f"    - {assertion.canonical_value or assertion.raw_value} (origin={assertion.origin.value})")
        for assertion in view.conflicting:
            lines.append(f"    ! CONFLICTING: {assertion.canonical_value or assertion.raw_value} (origin={assertion.origin.value})")
    return "\n".join(lines) + "\n"


def _render_case_section(package: ContextPackage) -> str:
    if package.case_context is None:
        return "CASE CONTEXT:\n  (no case linked)\n"
    snapshot = package.case_context
    lines = [f"CASE CONTEXT: {snapshot.case_id} -- {snapshot.title} ({snapshot.status.value})"]
    if snapshot.context_truncated:
        lines.append(f"  [case context truncated: {snapshot.included_item_count} of {snapshot.total_item_count} items shown]")
    for item in snapshot.items:
        lines.append(f"  - [{item.kind.value}] {item.content}")
    return "\n".join(lines) + "\n"


def _render_operational_section(package: ContextPackage) -> str:
    if not package.operational_observations:
        return "OPERATIONAL CONTEXT:\n  (no operational observations)\n"
    lines = ["OPERATIONAL CONTEXT:"]
    for observation in package.operational_observations:
        lines.append(f"  - [{observation.kind.value}] {observation.content}")
    return "\n".join(lines) + "\n"


def _render_evidence_section(package: ContextPackage, *, max_evidence_item_characters: int) -> str:
    evidence = package.evidence
    if not evidence.items:
        return "KNOWLEDGE EVIDENCE:\n  (no evidence selected)\n"
    lines = [f"KNOWLEDGE EVIDENCE ({evidence.selected_evidence_count} selected):"]
    for item in evidence.items:
        origin = "derived" if item.is_derived else "source"
        header = f"  [{item.selected_rank}] {item.knowledge_id} v{item.version_label} / {item.section_id} ({origin})"
        lines.append(header)
        text = item.indexable_text
        if len(text) > max_evidence_item_characters:
            shown = text[:max_evidence_item_characters]
            lines.append(f"      {shown}")
            lines.append(f"      [...truncated, {max_evidence_item_characters} of {len(text)} characters shown...]")
        else:
            lines.append(f"      {text}")
    return "\n".join(lines) + "\n"


def _render_uncertainties_section(package: ContextPackage) -> str:
    from backend.context.domain.enums import ContextState

    unresolved = [v for v in package.telco_context if v.state in (ContextState.UNKNOWN, ContextState.CONFLICTING)]
    no_evidence = package.evidence.selected_evidence_count == 0
    if not unresolved and not no_evidence:
        return "UNCERTAINTIES / CONFLICTS:\n  (none)\n"
    lines = ["UNCERTAINTIES / CONFLICTS:"]
    for view in unresolved:
        lines.append(f"  - {view.dimension.value}: {view.state.value}")
    if no_evidence:
        lines.append("  - no Knowledge evidence was selected for this request")
    return "\n".join(lines) + "\n"


def _render_source_manifest_section(package: ContextPackage) -> str:
    if not package.provenance_manifest:
        return "SOURCE MANIFEST:\n  (empty)\n"
    lines = ["SOURCE MANIFEST:"]
    for entry in package.provenance_manifest:
        lines.append(f"  - [{entry.category}] {entry.identity}")
    return "\n".join(lines) + "\n"


def render_context_package_as_text(
    package: ContextPackage,
    *,
    max_evidence_item_characters: int = _DEFAULT_MAX_EVIDENCE_ITEM_CHARACTERS,
) -> str:
    request = package.request
    request_lines = ["REQUEST:"]
    if request.chat_topic:
        request_lines.append(f"  chat_topic: {request.chat_topic}")
    if request.question:
        request_lines.append(f"  question: {request.question}")
    if request.requested_time_range:
        request_lines.append(f"  requested_time_range: {request.requested_time_range}")
    if len(request_lines) == 1:
        request_lines.append("  (no request context provided)")

    sections = [
        "\n".join(request_lines) + "\n",
        _render_telco_section(package),
        _render_case_section(package),
        _render_operational_section(package),
        _render_evidence_section(package, max_evidence_item_characters=max_evidence_item_characters),
        _render_uncertainties_section(package),
        _render_source_manifest_section(package),
    ]
    return "\n".join(sections)
