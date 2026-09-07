"""Phase 5.1I: structural validation of `KnowledgeSearchToolRequest`,
`KnowledgeToolExecutionContext`, `KnowledgeToolEvidenceItem`,
`KnowledgeSearchDiagnostic`, `KnowledgeSearchAgentPayload`, and
`KnowledgeSearchExecutionResult` -- the trust boundary that a model may
supply only `query_text`/`limit`, and never `as_of`/`applicability_context`
or any authoritative provenance field.
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from backend.knowledge.domain.applicability import ApplicabilityContext, ApplicabilityOutcome
from backend.knowledge.domain.enums import KnowledgeDocumentType
from backend.knowledge.provenance.contracts import KnowledgeEvidenceSelectionKey, KnowledgeEvidenceSet
from backend.knowledge.retrieval.contracts import KnowledgeRetrievalDiagnosticReason
from backend.knowledge.tools.contracts import (
    DEFAULT_SEARCH_LIMIT,
    MAXIMUM_SEARCH_LIMIT,
    KnowledgeSearchAgentPayload,
    KnowledgeSearchDiagnostic,
    KnowledgeSearchExecutionResult,
    KnowledgeSearchToolRequest,
    KnowledgeToolEvidenceItem,
    KnowledgeToolExecutionContext,
)

_AS_OF = datetime(2026, 6, 1, tzinfo=timezone.utc)


def _selection_key() -> KnowledgeEvidenceSelectionKey:
    return KnowledgeEvidenceSelectionKey(knowledge_id="k1", version_label="v1", section_id="k1:v1:s0")


def _tool_item(**overrides: object) -> KnowledgeToolEvidenceItem:
    fields: dict[str, object] = dict(
        selection_key=_selection_key(),
        title="Router Outage Guide",
        document_type=KnowledgeDocumentType.SOP,
        section_heading="Overview",
        content="Router outage recovery steps.",
        source_system="test",
        source_id="doc-1",
        source_display_name="Doc 1",
        source_locator="p3",
        applicability_outcome=ApplicabilityOutcome.MATCH,
        relevance_score=0.9,
        section_sequence=0,
    )
    fields.update(overrides)
    return KnowledgeToolEvidenceItem(**fields)


# --- KnowledgeSearchToolRequest: the model-controlled input -------------------


def test_request_accepts_minimal_query_text_with_default_limit() -> None:
    request = KnowledgeSearchToolRequest(query_text="router outage")
    assert request.limit == DEFAULT_SEARCH_LIMIT


def test_request_declared_field_set_is_exactly_query_text_and_limit() -> None:
    assert set(KnowledgeSearchToolRequest.model_fields.keys()) == {"query_text", "limit"}


def test_request_rejects_blank_query_text() -> None:
    with pytest.raises(ValidationError):
        KnowledgeSearchToolRequest(query_text="   ")


@pytest.mark.parametrize("limit", [1, DEFAULT_SEARCH_LIMIT, MAXIMUM_SEARCH_LIMIT])
def test_request_accepts_limit_within_bounds(limit: int) -> None:
    request = KnowledgeSearchToolRequest(query_text="router outage", limit=limit)
    assert request.limit == limit


@pytest.mark.parametrize("limit", [0, -1, MAXIMUM_SEARCH_LIMIT + 1, 1000])
def test_request_rejects_limit_out_of_bounds(limit: int) -> None:
    with pytest.raises(ValidationError):
        KnowledgeSearchToolRequest(query_text="router outage", limit=limit)


def test_request_does_not_silently_clamp_an_excessive_limit() -> None:
    """An excessive limit must be REJECTED, never silently clamped down
    to the maximum -- if this raised anything other than `ValidationError`
    (or didn't raise at all), the request would have been clamped instead
    of rejected.
    """
    with pytest.raises(ValidationError):
        KnowledgeSearchToolRequest(query_text="router outage", limit=MAXIMUM_SEARCH_LIMIT + 5)


@pytest.mark.parametrize(
    "extra_field",
    ["source_system", "document_type", "version_label", "lifecycle_status", "content", "evidence", "provenance", "as_of", "applicability_context", "database_url", "some_future_field"],
)
def test_request_rejects_any_unknown_field(extra_field: str) -> None:
    with pytest.raises(ValidationError):
        KnowledgeSearchToolRequest(query_text="router outage", **{extra_field: "fabricated"})


def test_request_cannot_supply_evidence_or_selection_fields() -> None:
    for forbidden in ("evidence", "evidence_set", "selection_key", "section_id"):
        with pytest.raises(ValidationError):
            KnowledgeSearchToolRequest(query_text="router outage", **{forbidden: "fabricated"})


# --- KnowledgeToolExecutionContext: trusted, backend-supplied only ------------


def test_execution_context_requires_as_of() -> None:
    with pytest.raises(ValidationError):
        KnowledgeToolExecutionContext()  # type: ignore[call-arg]


def test_execution_context_applicability_context_defaults_to_empty() -> None:
    context = KnowledgeToolExecutionContext(as_of=_AS_OF)
    assert context.applicability_context == ApplicabilityContext()


def test_execution_context_accepts_arbitrary_applicability_dimensions() -> None:
    context = KnowledgeToolExecutionContext(
        as_of=_AS_OF, applicability_context=ApplicabilityContext(dimensions={"some_future_dimension": ["x"]})
    )
    assert context.applicability_context.dimensions == {"some_future_dimension": ["x"]}


def test_execution_context_declared_field_set_is_exactly_as_of_and_applicability_context() -> None:
    assert set(KnowledgeToolExecutionContext.model_fields.keys()) == {"as_of", "applicability_context"}


@pytest.mark.parametrize("extra_field", ["query_text", "limit", "source_system", "some_future_field"])
def test_execution_context_rejects_any_unknown_field(extra_field: str) -> None:
    with pytest.raises(ValidationError):
        KnowledgeToolExecutionContext(as_of=_AS_OF, **{extra_field: "fabricated"})


# --- KnowledgeToolEvidenceItem: model-safe evidence view ----------------------


def test_tool_item_accepts_valid_fields() -> None:
    item = _tool_item()
    assert item.content == "Router outage recovery steps."


def test_tool_item_does_not_expose_source_uri() -> None:
    assert "source_uri" not in KnowledgeToolEvidenceItem.model_fields


def test_tool_item_relevance_score_field_is_not_named_confidence() -> None:
    field_names = set(KnowledgeToolEvidenceItem.model_fields.keys())
    assert "confidence" not in field_names
    assert "certainty" not in field_names
    assert "probability" not in field_names
    assert "relevance_score" in field_names


@pytest.mark.parametrize("score", [-0.01, 1.01])
def test_tool_item_rejects_relevance_score_out_of_range(score: float) -> None:
    with pytest.raises(ValidationError):
        _tool_item(relevance_score=score)


@pytest.mark.parametrize(
    "outcome", [ApplicabilityOutcome.MATCH, ApplicabilityOutcome.PARTIAL_MATCH, ApplicabilityOutcome.UNKNOWN]
)
def test_tool_item_accepts_every_eligible_applicability_outcome(outcome: ApplicabilityOutcome) -> None:
    item = _tool_item(applicability_outcome=outcome)
    assert item.applicability_outcome is outcome


def test_tool_item_uses_the_real_selection_key_type_not_a_new_citation_id() -> None:
    item = _tool_item()
    assert isinstance(item.selection_key, KnowledgeEvidenceSelectionKey)


# --- KnowledgeSearchDiagnostic --------------------------------------------------


def test_search_diagnostic_reuses_the_5_1g_diagnostic_reason_enum() -> None:
    diagnostic = KnowledgeSearchDiagnostic(knowledge_id="k1", reason=KnowledgeRetrievalDiagnosticReason.CURRENT_VERSION_AMBIGUOUS)
    assert diagnostic.reason is KnowledgeRetrievalDiagnosticReason.CURRENT_VERSION_AMBIGUOUS


# --- KnowledgeSearchAgentPayload: the only serializable, model-safe object ----


def test_agent_payload_defaults_to_empty() -> None:
    payload = KnowledgeSearchAgentPayload()
    assert payload.items == []
    assert payload.diagnostics == []


def test_agent_payload_does_not_declare_an_evidence_set_field() -> None:
    """The CRITICAL structural trust-split test: `agent_payload` must
    never contain a field named `evidence_set` or any trusted aggregate.
    """
    field_names = set(KnowledgeSearchAgentPayload.model_fields.keys())
    assert field_names == {"items", "diagnostics"}
    assert "evidence_set" not in field_names
    assert "evidence" not in field_names


def test_agent_payload_json_serializes_cleanly() -> None:
    payload = KnowledgeSearchAgentPayload(items=[_tool_item()])
    dumped = payload.model_dump(mode="json")
    assert dumped["items"][0]["content"] == "Router outage recovery steps."
    assert dumped["items"][0]["applicability_outcome"] == "match"
    assert "source_uri" not in dumped["items"][0]
    assert "evidence_set" not in dumped


# --- KnowledgeSearchExecutionResult: the internal trust bridge ----------------


def test_execution_result_carries_both_agent_payload_and_evidence_set() -> None:
    payload = KnowledgeSearchAgentPayload()
    evidence_set = KnowledgeEvidenceSet()
    result = KnowledgeSearchExecutionResult(agent_payload=payload, evidence_set=evidence_set)
    assert result.agent_payload is payload
    assert result.evidence_set is evidence_set


def test_execution_result_is_frozen() -> None:
    result = KnowledgeSearchExecutionResult(agent_payload=KnowledgeSearchAgentPayload(), evidence_set=KnowledgeEvidenceSet())
    with pytest.raises((AttributeError, Exception)):
        result.agent_payload = KnowledgeSearchAgentPayload(items=[_tool_item()])  # type: ignore[misc]


def test_execution_result_declares_exactly_two_fields() -> None:
    import dataclasses

    field_names = {f.name for f in dataclasses.fields(KnowledgeSearchExecutionResult)}
    assert field_names == {"agent_payload", "evidence_set"}
