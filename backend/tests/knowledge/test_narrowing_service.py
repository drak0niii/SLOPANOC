"""Phase 6A.4 integration tests: `narrow_corpus`/`narrow_knowledge`
(`backend/knowledge/narrowing/service.py`) -- the full two-gate
pipeline against representative corpora, matching this milestone's own
instruction §38 fixture exactly (Customer=Vodafone, Domain=RAN,
Vendor=Ericsson, Technology=LTE, Release=24.Q2, Alarm=VSWR; corpus A-G).
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from backend.context.domain.enums import AssertionKind, ContextDimension, ContextOrigin
from backend.context.domain.models import ContextAssertion, compute_context_state
from backend.knowledge.domain.asset_metadata import KnowledgeAssetMetadata
from backend.knowledge.domain.enums import KnowledgeDocumentType, LifecycleStatus
from backend.knowledge.domain.models import Applicability, KnowledgeMetadata, KnowledgeObject, KnowledgeSource, KnowledgeVersion
from backend.knowledge.narrowing.contracts import NarrowingPolicy, NarrowingReasonCode
from backend.knowledge.narrowing.service import narrow_corpus, narrow_knowledge
from backend.knowledge.repository.sqlalchemy import SqlAlchemyKnowledgeRepository

_AS_OF = datetime(2026, 1, 1, tzinfo=timezone.utc)
_EFF = datetime(2025, 1, 1, tzinfo=timezone.utc)


def _obj(
    knowledge_id: str,
    label: str = "1.0",
    *,
    vendor=None,
    technology=None,
    customer=None,
    explicit_any_customer: bool = False,
    supersedes=None,
    lifecycle=LifecycleStatus.APPROVED,
) -> KnowledgeObject:
    dims: dict[str, list[str]] = {}
    if vendor:
        dims["vendor"] = [vendor]
    if technology:
        dims["technology"] = [technology]
    if customer and not explicit_any_customer:
        dims["customer"] = [customer]
    am = KnowledgeAssetMetadata()
    if explicit_any_customer:
        am.applicability_scope.explicit_any_dimensions = ["customer"]
    return KnowledgeObject(
        knowledge_id=knowledge_id,
        document_type=KnowledgeDocumentType.MOP,
        title=knowledge_id,
        version=KnowledgeVersion(label=label, effective_from=_EFF, supersedes=supersedes or []),
        lifecycle_status=lifecycle,
        source=KnowledgeSource(source_system="x", source_id=knowledge_id),
        applicability=Applicability(dimensions=dims),
        metadata=KnowledgeMetadata(asset_metadata=am),
    )


def _context_state() -> dict:
    assertions = [
        ContextAssertion(assertion_id="a1", dimension=ContextDimension.CUSTOMER, kind=AssertionKind.VALUE, raw_value="Vodafone", canonical_value="VODAFONE", origin=ContextOrigin.USER),
        ContextAssertion(assertion_id="a2", dimension=ContextDimension.VENDOR, kind=AssertionKind.VALUE, raw_value="Ericsson", canonical_value="ERICSSON", origin=ContextOrigin.USER),
        ContextAssertion(assertion_id="a3", dimension=ContextDimension.TECHNOLOGY, kind=AssertionKind.VALUE, raw_value="LTE", canonical_value="LTE", origin=ContextOrigin.USER),
        ContextAssertion(assertion_id="a4", dimension=ContextDimension.RELEASE, kind=AssertionKind.VALUE, raw_value="24.Q2", canonical_value="24.Q2", origin=ContextOrigin.CASE),
        ContextAssertion(assertion_id="a5", dimension=ContextDimension.ALARM, kind=AssertionKind.VALUE, raw_value="VSWR", canonical_value="VSWR", origin=ContextOrigin.CASE),
    ]
    return compute_context_state(assertions)


def _corpus() -> list[KnowledgeObject]:
    a = _obj("A", vendor="Ericsson", technology="LTE", customer="Vodafone")
    b = _obj("B", vendor="Nokia", technology="LTE", customer="Vodafone")
    c = _obj("C", vendor="Ericsson", technology="LTE", customer="Rogers")
    d = _obj("D", vendor="Ericsson", technology="5G", customer="Vodafone")
    e_old = _obj("E", "1.0", vendor="Ericsson", technology="LTE", customer="Vodafone")
    e_new = _obj("E", "2.0", vendor="Ericsson", technology="LTE", customer="Vodafone", supersedes=["1.0"])
    f = _obj("F", vendor="Ericsson", technology="LTE", explicit_any_customer=True)
    g = _obj("G")  # no applicability declared at all
    return [a, b, c, d, e_old, e_new, f, g]


def test_deterministic_candidate_set_matches_expected_scenario() -> None:
    """6A.4 corrective pass: G (entirely unspecified applicability) now
    resolves INDETERMINATE, never permitted -- 'absence of applicability
    information is not evidence of applicability.'"""
    result = narrow_corpus(_corpus(), _context_state(), as_of=_AS_OF)
    assert sorted(result.permitted_knowledge_ids) == ["A", "E", "F"]
    assert sorted(set(result.excluded_knowledge_ids)) == ["B", "C", "D", "E"]
    assert result.indeterminate_knowledge_ids == ["G"]
    assert result.input_count == 8


def test_customer_mismatch_document_c_excluded() -> None:
    result = narrow_corpus(_corpus(), _context_state(), as_of=_AS_OF)
    item_c = next(i for i in result.items if i.knowledge_id == "C")
    assert item_c.final_bucket == "excluded"
    assert NarrowingReasonCode.DIMENSION_MISMATCH in item_c.applicability.reason_codes


def test_superseded_document_e_old_excluded_new_permitted() -> None:
    result = narrow_corpus(_corpus(), _context_state(), as_of=_AS_OF)
    e_items = {i.version_label: i for i in result.items if i.knowledge_id == "E"}
    assert e_items["1.0"].final_bucket == "excluded"
    assert NarrowingReasonCode.SUPERSEDED in e_items["1.0"].eligibility.reason_codes
    assert e_items["2.0"].final_bucket == "permitted"


def test_explicit_any_document_f_permitted_despite_no_customer_match() -> None:
    result = narrow_corpus(_corpus(), _context_state(), as_of=_AS_OF)
    assert "F" in result.permitted_knowledge_ids


def test_unconstrained_document_g_indeterminate_not_permitted() -> None:
    """6A.4 corrective pass regression proof at the integration level."""
    result = narrow_corpus(_corpus(), _context_state(), as_of=_AS_OF)
    assert "G" in result.indeterminate_knowledge_ids
    assert "G" not in result.permitted_knowledge_ids
    assert "G" not in result.excluded_knowledge_ids
    item_g = next(i for i in result.items if i.knowledge_id == "G")
    assert item_g.final_bucket == "indeterminate"
    assert NarrowingReasonCode.APPLICABILITY_UNSPECIFIED in item_g.applicability.reason_codes


def test_result_is_deterministic_across_repeated_calls() -> None:
    r1 = narrow_corpus(_corpus(), _context_state(), as_of=_AS_OF)
    r2 = narrow_corpus(_corpus(), _context_state(), as_of=_AS_OF)
    assert sorted(r1.permitted_knowledge_ids) == sorted(r2.permitted_knowledge_ids)
    assert sorted(r1.excluded_knowledge_ids) == sorted(r2.excluded_knowledge_ids)


def test_result_independent_of_input_corpus_order() -> None:
    corpus = _corpus()
    shuffled = list(reversed(corpus))
    r1 = narrow_corpus(corpus, _context_state(), as_of=_AS_OF)
    r2 = narrow_corpus(shuffled, _context_state(), as_of=_AS_OF)
    assert sorted(r1.permitted_knowledge_ids) == sorted(r2.permitted_knowledge_ids)


def test_indeterminate_bucket_populated_when_context_partially_missing() -> None:
    partial_context = compute_context_state([
        ContextAssertion(assertion_id="a1", dimension=ContextDimension.CUSTOMER, kind=AssertionKind.VALUE, raw_value="Vodafone", canonical_value="VODAFONE", origin=ContextOrigin.USER),
    ])
    result = narrow_corpus([_obj("H", vendor="Ericsson", customer="Vodafone")], partial_context, as_of=_AS_OF)
    assert result.indeterminate_knowledge_ids == ["H"]
    assert result.permitted_knowledge_ids == []
    assert result.excluded_knowledge_ids == []


def test_ai_approval_enforcement_policy_excludes_when_missing() -> None:
    am = KnowledgeAssetMetadata()  # ai_approved_flag never set
    # Explicitly declared applicable-to-everything (EXPLICIT_ANY) so this
    # object's Gate 2 outcome is always "match", isolating this test's
    # own point (the AI-Approval OBSERVE/ENFORCE policy boundary, Gate 1)
    # from the 6A.4 corrective-pass Gate 2 sufficiency rule -- an object
    # with zero declared applicability intent is a separate concern,
    # covered by test_narrowing_applicability_gate.py's own dedicated
    # tests.
    am.applicability_scope.explicit_any_dimensions = ["vendor"]
    obj = KnowledgeObject(
        knowledge_id="P", document_type=KnowledgeDocumentType.MOP, title="P",
        version=KnowledgeVersion(label="1.0", effective_from=_EFF), lifecycle_status=LifecycleStatus.APPROVED,
        source=KnowledgeSource(source_system="x", source_id="P"), metadata=KnowledgeMetadata(asset_metadata=am),
    )
    result_observe = narrow_corpus([obj], {}, as_of=_AS_OF, policy=NarrowingPolicy())
    assert "P" in result_observe.permitted_knowledge_ids  # OBSERVE mode: never excludes

    result_enforce = narrow_corpus([obj], {}, as_of=_AS_OF, policy=NarrowingPolicy(enforce_ai_approved=True))
    assert "P" not in result_enforce.permitted_knowledge_ids
    assert "P" in result_enforce.excluded_knowledge_ids


def test_historical_corpus_without_governance_metadata_unaffected_by_default_policy() -> None:
    """§9's explicit exit criterion: default OBSERVE policy must not
    silently destroy existing retrieval behavior for historical Knowledge
    lacking the new governance metadata."""
    plain_historical = _obj("HIST", vendor="Ericsson", technology="LTE")
    result = narrow_corpus([plain_historical], _context_state(), as_of=_AS_OF)
    assert "HIST" in result.permitted_knowledge_ids


# --- Document revision vs. network release separation (§6, mandatory) -------


def test_document_revision_and_network_release_are_independent() -> None:
    """A document's own KnowledgeVersion.revision (e.g. "PA1") must never
    be read as, or confused with, a network/software release value (e.g.
    "24.Q2") used for TELCO applicability matching."""
    obj = KnowledgeObject(
        knowledge_id="REV1",
        document_type=KnowledgeDocumentType.MOP,
        title="T",
        version=KnowledgeVersion(label="1.0", revision="PA1", effective_from=_EFF),
        lifecycle_status=LifecycleStatus.APPROVED,
        source=KnowledgeSource(source_system="x", source_id="REV1"),
        applicability=Applicability(dimensions={"release": ["24.Q2"]}),
    )
    context_release_matches_document_revision_string = compute_context_state([
        ContextAssertion(assertion_id="a1", dimension=ContextDimension.RELEASE, kind=AssertionKind.VALUE, raw_value="PA1", canonical_value="PA1", origin=ContextOrigin.USER),
    ])
    # The context happens to assert the SAME literal string as the
    # document's own revision label ("PA1") for the RELEASE dimension --
    # this must NOT match the object's real release constraint ("24.Q2"),
    # proving revision and release are never cross-read.
    result = narrow_corpus([obj], context_release_matches_document_revision_string, as_of=_AS_OF)
    assert "REV1" in result.excluded_knowledge_ids


def test_resolve_canonical_dimensions_never_reads_knowledge_version_revision() -> None:
    """Source-level proof: `resolve_canonical_dimensions`/`evaluate_
    telco_applicability` never reference `.version.revision` anywhere.
    """
    import ast
    import inspect

    from backend.knowledge.narrowing import applicability_gate as module

    tree = ast.parse(inspect.getsource(module))
    violations = [
        ast.dump(node)
        for node in ast.walk(tree)
        if isinstance(node, ast.Attribute) and node.attr == "revision"
    ]
    assert violations == []


# --- Real repository integration (async) -------------------------------------


@pytest.mark.asyncio
async def test_narrow_knowledge_reads_from_a_real_repository() -> None:
    repository = SqlAlchemyKnowledgeRepository("sqlite+aiosqlite:///:memory:")
    for obj in _corpus():
        await repository.add(obj)

    result = await narrow_knowledge(repository, _context_state(), as_of=_AS_OF)
    assert sorted(result.permitted_knowledge_ids) == ["A", "E", "F"]
    assert result.indeterminate_knowledge_ids == ["G"]
    assert result.input_count == 8
    await repository.close()


@pytest.mark.asyncio
async def test_narrow_knowledge_never_mutates_the_repository() -> None:
    """Read-only proof: the repository's own corpus is byte-identical
    before and after narrowing."""
    repository = SqlAlchemyKnowledgeRepository("sqlite+aiosqlite:///:memory:")
    for obj in _corpus():
        await repository.add(obj)
    before = sorted((o.knowledge_id, o.version.label, o.model_dump_json()) for o in await repository.list_all())

    await narrow_knowledge(repository, _context_state(), as_of=_AS_OF)

    after = sorted((o.knowledge_id, o.version.label, o.model_dump_json()) for o in await repository.list_all())
    assert before == after
    await repository.close()
