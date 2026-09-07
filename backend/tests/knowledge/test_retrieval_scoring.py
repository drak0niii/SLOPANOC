"""Phase 5.1G: `TokenOverlapRelevanceScorer` -- deterministic, normalized
lexical token-overlap scoring, with NO fuzzy/synonym/semantic matching.
`KnowledgeRelevanceScorer` Protocol structural shape is also covered
here.
"""
from __future__ import annotations

import inspect
from typing import Protocol

import pytest

from backend.knowledge.domain.enums import KnowledgeDocumentType, LifecycleStatus
from backend.knowledge.domain.models import (
    KnowledgeMetadata,
    KnowledgeObject,
    KnowledgeSection,
    KnowledgeSource,
    KnowledgeVersion,
)
from backend.knowledge.retrieval.scoring import KnowledgeRelevanceScorer, TokenOverlapRelevanceScorer


def _obj(title: str = "t", tags: list[str] | None = None) -> KnowledgeObject:
    return KnowledgeObject(
        knowledge_id="k1",
        document_type=KnowledgeDocumentType.SOP,
        title=title,
        version=KnowledgeVersion(label="v1"),
        lifecycle_status=LifecycleStatus.APPROVED,
        source=KnowledgeSource(source_system="test", source_id="doc-1"),
        metadata=KnowledgeMetadata(tags=tags or []),
    )


def _section(heading: str | None = "Overview", content: str = "neutral filler text") -> KnowledgeSection:
    return KnowledgeSection(section_id="sec-1", knowledge_id="k1", heading=heading, sequence=0, content=content)


_SCORER = TokenOverlapRelevanceScorer()


# --- Protocol shape -----------------------------------------------------------


def test_scorer_protocol_is_a_protocol() -> None:
    assert issubclass(KnowledgeRelevanceScorer, Protocol)  # type: ignore[arg-type]


def test_scorer_protocol_exposes_exactly_one_score_method() -> None:
    method_names = {name for name, _ in inspect.getmembers(KnowledgeRelevanceScorer) if not name.startswith("_")}
    assert method_names == {"score"}


def test_token_overlap_scorer_satisfies_the_protocol_structurally() -> None:
    assert hasattr(TokenOverlapRelevanceScorer, "score")
    signature = inspect.signature(TokenOverlapRelevanceScorer.score)
    assert list(signature.parameters) == ["self", "query_text", "knowledge_object", "section"]


def test_score_does_not_require_a_coroutine() -> None:
    assert not inspect.iscoroutinefunction(TokenOverlapRelevanceScorer.score)


# --- basic scoring behavior ---------------------------------------------------


def test_full_overlap_scores_one() -> None:
    score = _SCORER.score("router outage", _obj(title="Router Outage Runbook"), _section(content="neutral filler text"))
    assert score == 1.0


def test_zero_overlap_scores_zero() -> None:
    score = _SCORER.score("router outage", _obj(title="Completely unrelated"), _section(content="also unrelated"))
    assert score == 0.0


def test_partial_overlap_scores_between_zero_and_one() -> None:
    score = _SCORER.score("router outage config", _obj(title="Router maintenance"), _section(content="neutral filler text"))
    assert 0.0 < score < 1.0


def test_score_is_always_in_closed_unit_interval() -> None:
    score = _SCORER.score("router router router outage", _obj(title="router outage outage outage"), _section())
    assert 0.0 <= score <= 1.0


def test_blank_query_text_scores_zero() -> None:
    assert _SCORER.score("   ", _obj(title="router outage"), _section()) == 0.0


def test_duplicate_query_terms_do_not_inflate_score() -> None:
    single = _SCORER.score("router", _obj(title="Router Outage"), _section())
    repeated = _SCORER.score("router router router", _obj(title="Router Outage"), _section())
    assert single == repeated == 1.0


def test_case_insensitivity_via_casefold() -> None:
    assert _SCORER.score("ROUTER outage", _obj(title="router OUTAGE"), _section()) == 1.0


def test_deterministic_repeated_calls_produce_identical_score() -> None:
    obj = _obj(title="Router Outage Runbook")
    section = _section(content="steps to resolve router outage")
    first = _SCORER.score("router outage", obj, section)
    second = _SCORER.score("router outage", obj, section)
    assert first == second


# --- no fuzzy / synonym / substring behavior -----------------------------------


def test_substring_is_not_a_match() -> None:
    assert _SCORER.score("Eric", _obj(title="Ericsson equipment"), _section()) == 0.0


def test_synonyms_are_never_treated_as_equivalent() -> None:
    assert _SCORER.score("failure", _obj(title="fault report"), _section()) == 0.0
    assert _SCORER.score("5G", _obj(title="NR network"), _section()) == 0.0


def test_no_edit_distance_tolerance_for_typos() -> None:
    assert _SCORER.score("routr", _obj(title="router outage"), _section()) == 0.0


# --- text surface: title + tags + heading + content, nothing else ------------


def test_title_participates_in_scoring() -> None:
    assert _SCORER.score("router", _obj(title="router guide"), _section(content="neutral filler text")) == 1.0


def test_tags_participate_in_scoring() -> None:
    assert _SCORER.score("outage", _obj(title="guide", tags=["outage", "network"]), _section(content="neutral filler text")) == 1.0


def test_section_heading_participates_in_scoring() -> None:
    assert _SCORER.score("outage", _obj(title="guide"), _section(heading="Outage Steps", content="neutral filler text")) == 1.0


def test_section_content_participates_in_scoring() -> None:
    assert _SCORER.score("outage", _obj(title="guide"), _section(heading=None, content="outage recovery steps")) == 1.0


def test_missing_section_heading_does_not_raise() -> None:
    assert _SCORER.score("outage", _obj(title="guide"), _section(heading=None, content="neutral filler text")) == 0.0


def test_source_system_never_participates_in_scoring() -> None:
    obj_a = KnowledgeObject(
        knowledge_id="k1",
        document_type=KnowledgeDocumentType.SOP,
        title="guide",
        version=KnowledgeVersion(label="v1"),
        lifecycle_status=LifecycleStatus.APPROVED,
        source=KnowledgeSource(source_system="sharepoint", source_id="doc-1"),
    )
    obj_b = obj_a.model_copy(update={"source": KnowledgeSource(source_system="teams", source_id="doc-2")})
    assert _SCORER.score("sharepoint", obj_a, _section()) == 0.0
    assert _SCORER.score("sharepoint", obj_b, _section()) == 0.0


def test_lifecycle_status_never_participates_in_scoring() -> None:
    approved = _obj(title="guide")
    archived = approved.model_copy(update={"lifecycle_status": LifecycleStatus.ARCHIVE})
    assert _SCORER.score("approved", approved, _section()) == 0.0
    assert _SCORER.score("archive", archived, _section()) == 0.0


def test_version_label_never_participates_in_scoring() -> None:
    obj = _obj(title="guide")
    assert _SCORER.score("v1", obj, _section()) == 0.0


@pytest.mark.parametrize(
    "document_type", [KnowledgeDocumentType.MOP, KnowledgeDocumentType.SOP, KnowledgeDocumentType.RCA, KnowledgeDocumentType.OTHER]
)
def test_document_type_never_inflates_or_suppresses_score(document_type: KnowledgeDocumentType) -> None:
    obj = KnowledgeObject(
        knowledge_id="k1",
        document_type=document_type,
        title="router outage",
        version=KnowledgeVersion(label="v1"),
        lifecycle_status=LifecycleStatus.APPROVED,
        source=KnowledgeSource(source_system="test", source_id="doc-1"),
    )
    assert _SCORER.score("router outage", obj, _section()) == 1.0
