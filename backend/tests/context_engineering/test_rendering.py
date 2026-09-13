"""Phase 6A.6: the deterministic text renderer (§28/§29/§34). It must
faithfully render the package, never reason, and never hide a
truncation.
"""
from __future__ import annotations

import ast
import inspect
from datetime import datetime, timezone

from backend.context.domain.enums import ContextProfileOwnerKind
from backend.context_engineering.assembly import assemble_context_package
from backend.context_engineering.contracts import ContextPackageInput, RequestContext
from backend.context_engineering.rendering import render_context_package_as_text
from backend.knowledge.hybrid_retrieval.contracts import (
    ChannelHit,
    EvidenceIndexRecord,
    EvidenceSelectionResult,
    HybridRetrievalCandidate,
    RetrievalChannel,
)

_NOW = datetime(2026, 1, 1, tzinfo=timezone.utc)


def _package_with_evidence(text: str) -> "ContextPackage":  # noqa: F821
    record = EvidenceIndexRecord(evidence_id="ev1", knowledge_id="K1", version_label="1.0", section_id="S1", is_derived=False, indexable_text=text, content_hash="h", created_at=_NOW, updated_at=_NOW)
    candidate = HybridRetrievalCandidate(record=record, channel_hits=[ChannelHit(evidence_id="ev1", channel=RetrievalChannel.EXACT, raw_score=1.0)], fusion_score=0.5, rerank_score=1.5)
    sel = EvidenceSelectionResult(query_text="q", selected=[candidate], selection_reason="top_k_within_budget")
    return assemble_context_package(ContextPackageInput(
        owner_kind=ContextProfileOwnerKind.SESSION, owner_id="s",
        request=RequestContext(question="what should I check?"),
        evidence_selection=sel,
    ))


def test_renders_all_labelled_sections() -> None:
    pkg = _package_with_evidence("short evidence text")
    rendered = render_context_package_as_text(pkg)
    for label in ("REQUEST:", "TELCO CONTEXT:", "CASE CONTEXT:", "OPERATIONAL CONTEXT:", "KNOWLEDGE EVIDENCE", "UNCERTAINTIES / CONFLICTS:", "SOURCE MANIFEST:"):
        assert label in rendered


def test_faithfully_renders_request_and_evidence_content() -> None:
    pkg = _package_with_evidence("VSWR Over Threshold alarm procedure text")
    rendered = render_context_package_as_text(pkg)
    assert "what should I check?" in rendered
    assert "VSWR Over Threshold alarm procedure text" in rendered
    assert "K1 v1.0 / S1" in rendered


def test_truncation_is_never_hidden() -> None:
    long_text = "A" * 5000
    pkg = _package_with_evidence(long_text)
    rendered = render_context_package_as_text(pkg, max_evidence_item_characters=100)
    assert "[...truncated, 100 of 5000 characters shown...]" in rendered
    assert "A" * 5000 not in rendered
    # The item's OWN identity must still be fully visible, never truncated.
    assert "K1 v1.0 / S1" in rendered


def test_no_truncation_when_within_budget() -> None:
    pkg = _package_with_evidence("short text")
    rendered = render_context_package_as_text(pkg, max_evidence_item_characters=100)
    assert "truncated" not in rendered


def test_renderer_source_never_reasons_or_answers() -> None:
    """§29's own explicit prohibition, proven structurally: the renderer
    module's source contains no call to any function whose name suggests
    reasoning/answering/recommending, and defines no such function
    itself."""
    import backend.context_engineering.rendering as module

    tree = ast.parse(inspect.getsource(module))
    forbidden_name_fragments = ("answer", "infer", "recommend", "resolve_conflict", "diagnose", "troubleshoot")
    function_names = [node.name for node in ast.walk(tree) if isinstance(node, ast.FunctionDef)]
    for name in function_names:
        for fragment in forbidden_name_fragments:
            assert fragment not in name.lower(), f"unexpected reasoning-shaped function name: {name}"


def test_renderer_has_no_llm_or_agent_import() -> None:
    import backend.context_engineering.rendering as module

    tree = ast.parse(inspect.getsource(module))
    imported = {node.module for node in ast.walk(tree) if isinstance(node, ast.ImportFrom) and node.module}
    imported |= {alias.name for node in ast.walk(tree) if isinstance(node, ast.Import) for alias in node.names}
    forbidden = ("google.adk", "google.genai", "backend.agents", "backend.tools")
    assert not any(m.startswith(f) for m in imported for f in forbidden)
