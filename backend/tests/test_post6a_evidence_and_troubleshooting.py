"""POST-6A -- focused checks for the descriptor-governance, shared-evidence
and iterative-troubleshooting contracts.

Narrow by instruction: one check per changed boundary, no live calls, no
full-stack turn.
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from backend.agents.team_manager.investigation_state import (
    InvestigationState,
    PrerequisiteStatus,
    StepLifecycle,
    advance_after_observation,
    may_advance_to_next_step,
    parse_investigation_state,
    record_observation,
    record_requested_evidence,
    render_investigation_context,
)
from backend.agents.team_manager.unsupported_capabilities import (
    UNSUPPORTED_CAPABILITY_TEXT,
    UnsupportedCapability,
    unsupported_capability_text,
)
from backend.context.domain.enums import (
    AssertionKind,
    ContextDimension,
    ContextOrigin,
    ContextState,
)
from backend.context.domain.models import ContextAssertion, compute_context_state, effective_assertions
from backend.knowledge.domain.enums import KnowledgeDocumentType, LifecycleStatus
from backend.knowledge.domain.models import (
    Applicability,
    KnowledgeObject,
    KnowledgeSection,
    KnowledgeSource,
    KnowledgeVersion,
)
from backend.knowledge.domain.operation_descriptor import (
    GovernedOperationDescriptor,
    OperationDescriptorAuthority,
    OperationTargetScope,
)
from backend.knowledge.governance.operation_approval import (
    OperationApprovalError,
    OperationApprovalRecord,
    approve_section_operation,
    author_section_operation,
    descriptor_fingerprint,
)
from backend.knowledge.governance.service import approve_version
from backend.knowledge.hybrid_retrieval.contracts import EvidenceIndexRecord, HybridRetrievalQuery
from backend.knowledge.hybrid_retrieval.indexing import index_knowledge_object
from backend.knowledge.narrowing.service import narrow_corpus
from backend.knowledge.shared_evidence import (
    EvidenceAvailability,
    EvidenceRequest,
    SharedEvidenceResult,
    SharedEvidenceService,
    discard_turn_evidence,
)
from backend.tests._governed_operation_fixtures import build_knowledge_object

_NOW = datetime(2026, 1, 1, tzinfo=timezone.utc)


# ===========================================================================
# P3.1 -- descriptor authoring and approval
# ===========================================================================


def test_authoring_never_approves_whatever_the_caller_claimed() -> None:
    claimed = GovernedOperationDescriptor(
        operation_id="restart-rru",
        authority=OperationDescriptorAuthority.APPROVED,
        target_scope=OperationTargetScope.TARGET_INDEPENDENT,
    )
    authored = author_section_operation(build_knowledge_object(), "s1", claimed).sections[0].operation
    assert authored is not None
    assert authored.authority is OperationDescriptorAuthority.CANDIDATE


def test_approval_does_not_carry_across_a_content_edit() -> None:
    descriptor = GovernedOperationDescriptor(
        operation_id="restart-rru", target_scope=OperationTargetScope.SINGLE_TARGET
    )
    obj = approve_version(author_section_operation(build_knowledge_object(), "s1", descriptor))
    approval = OperationApprovalRecord(
        knowledge_id="k1",
        version_label="v1",
        section_id="s1",
        descriptor_fingerprint=descriptor_fingerprint(obj.sections[0].operation),
        approved_by="reviewer",
    )
    assert approve_section_operation(obj, approval).sections[0].operation.authority is (
        OperationDescriptorAuthority.APPROVED
    )

    widened = author_section_operation(
        obj, "s1", descriptor.model_copy(update={"target_scope": OperationTargetScope.TARGET_INDEPENDENT})
    )
    with pytest.raises(OperationApprovalError):
        approve_section_operation(widened, approval)


def test_fingerprint_is_stable_across_binding_and_promotion() -> None:
    """Binding a descriptor to its source, and promoting it, must not
    change the fingerprint -- otherwise an approval would invalidate
    itself the moment it was applied."""
    descriptor = GovernedOperationDescriptor(operation_id="restart-rru")
    bound = descriptor.bind_to_source(knowledge_id="k1", version_label="v1", section_id="s1")
    promoted = bound.model_copy(update={"authority": OperationDescriptorAuthority.APPROVED})
    assert descriptor_fingerprint(descriptor) == descriptor_fingerprint(bound) == descriptor_fingerprint(promoted)


# ===========================================================================
# Version identity through narrowing
# ===========================================================================


def _versioned(knowledge_id: str, label: str, status: LifecycleStatus) -> KnowledgeObject:
    """A document CONSTRAINED to vendor=ericsson, so that it actually
    narrows to `permitted` against `_ERICSSON_CONTEXT` below. An
    unconstrained document narrows to `indeterminate` against an empty
    context -- correct existing behavior, but not the case under test
    here."""
    return KnowledgeObject(
        knowledge_id=knowledge_id,
        document_type=KnowledgeDocumentType.OPERATIONAL_PROCEDURE,
        title="Doc",
        version=KnowledgeVersion(label=label),
        lifecycle_status=status,
        source=KnowledgeSource(source_system="local", source_id=knowledge_id),
        applicability=Applicability(dimensions={"vendor": ["ericsson"]}),
        sections=[KnowledgeSection(section_id=f"{label}-s1", knowledge_id=knowledge_id, sequence=0, content="body")],
    )


_ERICSSON_CONTEXT = compute_context_state(
    [
        ContextAssertion(
            assertion_id="ctx-vendor",
            dimension=ContextDimension.VENDOR,
            kind=AssertionKind.VALUE,
            raw_value="ericsson",
            canonical_value="ERICSSON",
            origin=ContextOrigin.USER,
        )
    ]
)


def test_narrowing_reports_permitted_versions_not_just_ids() -> None:
    corpus = [
        _versioned("K1", "1.0", LifecycleStatus.ARCHIVE),
        _versioned("K1", "2.0", LifecycleStatus.APPROVED),
    ]
    result = narrow_corpus(corpus, _ERICSSON_CONTEXT, as_of=_NOW)
    assert ("K1", "2.0") in result.permitted_version_keys
    assert ("K1", "1.0") not in result.permitted_version_keys
    # The id-only projection cannot express that distinction -- which is
    # exactly why retrieval must constrain on the version keys.
    assert "K1" in result.permitted_knowledge_ids


@pytest.mark.asyncio
async def test_retrieval_query_carries_version_keys_into_every_channel() -> None:
    seen: dict[str, object] = {}

    class _Repo:
        vector_available = False

        async def ensure_schema(self):
            return None

        async def exact_match(self, ids, query_text, permitted_version_keys=None):
            seen["exact"] = permitted_version_keys
            return []

        async def lexical_search(self, ids, query_text, limit, permitted_version_keys=None):
            seen["lexical"] = permitted_version_keys
            return []

        async def semantic_search(self, ids, vec, limit, permitted_version_keys=None):
            seen["semantic"] = permitted_version_keys
            return []

        async def get_many(self, ids):
            return {}

    from backend.knowledge.hybrid_retrieval.service import hybrid_retrieve

    class _Embed:
        async def embed(self, texts):
            raise AssertionError("must not be called when vector_available is False")

    await hybrid_retrieve(
        HybridRetrievalQuery(
            query_text="restart", permitted_knowledge_ids=["K1"], permitted_version_keys=[("K1", "2.0")]
        ),
        _Repo(),
        _Embed(),
    )
    assert seen["exact"] == [("K1", "2.0")]
    assert seen["lexical"] == [("K1", "2.0")]


@pytest.mark.asyncio
async def test_embedding_failure_preserves_authorized_exact_and_lexical_results() -> None:
    from backend.knowledge.hybrid_retrieval.contracts import ChannelHit, RetrievalChannel
    from backend.knowledge.hybrid_retrieval.service import hybrid_retrieve

    record = EvidenceIndexRecord(
        evidence_id="e1",
        knowledge_id="K1",
        version_label="2.0",
        section_id="s1",
        is_derived=False,
        indexable_text="restart the unit",
        content_hash="h",
    )

    class _Repo:
        vector_available = True

        async def ensure_schema(self):
            return None

        async def exact_match(self, ids, query_text, permitted_version_keys=None):
            return [ChannelHit(evidence_id="e1", channel=RetrievalChannel.EXACT, raw_score=1.0)]

        async def lexical_search(self, ids, query_text, limit, permitted_version_keys=None):
            return []

        async def semantic_search(self, ids, vec, limit, permitted_version_keys=None):
            raise AssertionError("must not be reached once embedding fails")

        async def get_many(self, ids):
            return {"e1": record}

    class _BrokenEmbed:
        async def embed(self, texts):
            raise RuntimeError("embedding provider unavailable")

    result = await hybrid_retrieve(
        HybridRetrievalQuery(query_text="restart", permitted_knowledge_ids=["K1"]), _Repo(), _BrokenEmbed()
    )
    # The authorized exact hit survives, and the loss is reported.
    assert [c.record.evidence_id for c in result.candidates] == ["e1"]
    assert result.telemetry.semantic_channel_mode == "degraded_embedding_unavailable"


# ===========================================================================
# Index reconciliation
# ===========================================================================


class _FakeIndexRepo:
    def __init__(self) -> None:
        self.rows: dict[str, EvidenceIndexRecord] = {}
        self.embeddings: dict[str, object] = {}

    async def get(self, evidence_id):
        return self.rows.get(evidence_id)

    async def upsert(self, record, embedding, *, now):
        self.rows[record.evidence_id] = record
        if embedding is not None:
            self.embeddings[record.evidence_id] = embedding
        return True

    async def delete_missing_for_version(self, knowledge_id, version_label, *, keep_evidence_ids):
        keep = set(keep_evidence_ids)
        stale = [
            eid
            for eid, r in self.rows.items()
            if r.knowledge_id == knowledge_id and r.version_label == version_label and eid not in keep
        ]
        for eid in stale:
            self.rows.pop(eid, None)
        return len(stale)


class _CountingEmbed:
    model = "m"
    model_version = "1"
    dimensions = 3

    def __init__(self) -> None:
        self.calls = 0

    async def embed(self, texts):
        from backend.knowledge.hybrid_retrieval.embedding import EmbeddingVector

        self.calls += 1
        return [
            EmbeddingVector(values=(0.1, 0.2, 0.3), model=self.model, model_version=self.model_version, dimensions=self.dimensions)
            for _ in texts
        ]


def _obj(sections: list[str]) -> KnowledgeObject:
    return KnowledgeObject(
        knowledge_id="K1",
        document_type=KnowledgeDocumentType.MOP,
        title="K1",
        version=KnowledgeVersion(label="1.0"),
        lifecycle_status=LifecycleStatus.APPROVED,
        source=KnowledgeSource(source_system="x", source_id="K1"),
        sections=[
            KnowledgeSection(section_id=sid, knowledge_id="K1", sequence=i, content=f"content {sid}")
            for i, sid in enumerate(sections)
        ],
    )


@pytest.mark.asyncio
async def test_unchanged_hash_does_not_prevent_a_required_embedding_retry() -> None:
    repo = _FakeIndexRepo()
    provider = _CountingEmbed()
    obj = _obj(["s1"])

    await index_knowledge_object(obj, repo, provider)
    assert provider.calls == 1

    # Simulate the real failure shape: the row exists with the same
    # content hash but NO vector, because a prior embedding attempt failed.
    row = repo.rows["".join(repo.rows.keys())]
    repo.rows[row.evidence_id] = row.model_copy(update={"embedding_generated_at": None})

    stats = await index_knowledge_object(obj, repo, provider)
    assert provider.calls == 2, "a missing vector must be retried despite an unchanged content hash"
    assert stats["reembedded"] == 1


@pytest.mark.asyncio
async def test_changed_embedding_model_forces_reembedding() -> None:
    repo = _FakeIndexRepo()
    provider = _CountingEmbed()
    obj = _obj(["s1"])
    await index_knowledge_object(obj, repo, provider)
    assert provider.calls == 1

    await index_knowledge_object(obj, repo, provider)
    assert provider.calls == 1, "nothing changed -- no retry"

    provider.model_version = "2"
    await index_knowledge_object(obj, repo, provider)
    assert provider.calls == 2, "a changed embedding model version must re-embed"


@pytest.mark.asyncio
async def test_removed_sections_are_deleted_from_the_index() -> None:
    repo = _FakeIndexRepo()
    provider = _CountingEmbed()
    await index_knowledge_object(_obj(["s1", "s2"]), repo, provider)
    assert len(repo.rows) == 2

    stats = await index_knowledge_object(_obj(["s1"]), repo, provider)
    assert stats["removed_sections"] == 1
    assert len(repo.rows) == 1


# ===========================================================================
# Shared evidence -- typed unavailability and no duplicate retrieval
# ===========================================================================


class _EmptyRepo:
    async def list_all(self):
        return []

    async def get(self, knowledge_id, version_label):
        return None


class _BoomRetrieval:
    async def retrieve(self, query):
        raise RuntimeError("database unavailable")


class _EmptyRetrieval:
    def __init__(self) -> None:
        self.calls = 0

    async def retrieve(self, query):
        from backend.knowledge.retrieval.contracts import KnowledgeRetrievalResult

        self.calls += 1
        return KnowledgeRetrievalResult(items=[], excluded_families=[])


class _NoopProvenance:
    async def build_evidence_set(self, retrieval_result):
        raise AssertionError("not reached in these cases")


@pytest.mark.asyncio
async def test_no_applicable_evidence_is_not_reported_as_missing_user_context() -> None:
    service = SharedEvidenceService(
        _EmptyRepo(), retrieval_service=_EmptyRetrieval(), provenance_service=_NoopProvenance()
    )
    result = await service.acquire(EvidenceRequest(run_id="r1", query_text="q", as_of=_NOW))
    discard_turn_evidence("r1")
    assert result.availability is EvidenceAvailability.NO_APPLICABLE_EVIDENCE
    assert result.is_infrastructure_failure is False


@pytest.mark.asyncio
async def test_infrastructure_failure_is_never_disguised_as_a_user_information_gap() -> None:
    class _OneDoc:
        async def list_all(self):
            return [_versioned("K1", "2.0", LifecycleStatus.APPROVED)]

        async def get(self, knowledge_id, version_label):
            return None

    service = SharedEvidenceService(
        _OneDoc(), retrieval_service=_BoomRetrieval(), provenance_service=_NoopProvenance()
    )
    result = await service.acquire(EvidenceRequest(run_id="r2", query_text="q", as_of=_NOW), _ERICSSON_CONTEXT)
    discard_turn_evidence("r2")
    assert result.availability is EvidenceAvailability.RETRIEVAL_UNAVAILABLE
    assert result.is_infrastructure_failure is True
    assert result.availability is not EvidenceAvailability.MISSING_USER_CONTEXT


@pytest.mark.asyncio
async def test_both_specialists_share_one_retrieval_per_turn() -> None:
    class _OneDoc:
        async def list_all(self):
            return [_versioned("K1", "2.0", LifecycleStatus.APPROVED)]

        async def get(self, knowledge_id, version_label):
            return None

    retrieval = _EmptyRetrieval()
    service = SharedEvidenceService(
        _OneDoc(), retrieval_service=retrieval, provenance_service=_NoopProvenance()
    )
    first = await service.acquire(
        EvidenceRequest(run_id="r3", query_text="q", as_of=_NOW, requested_by="incident_manager"),
        _ERICSSON_CONTEXT,
    )
    second = await service.acquire(
        EvidenceRequest(run_id="r3", query_text="q", as_of=_NOW, requested_by="troubleshooting_manager"),
        _ERICSSON_CONTEXT,
    )
    discard_turn_evidence("r3")

    assert retrieval.calls == 1, "the second specialist must reuse the first one's evidence"
    assert first.reused is False
    assert second.reused is True


# ===========================================================================
# Context corrections / supersession
# ===========================================================================


def _assertion(aid: str, dimension: ContextDimension, value: str, *, supersedes=None, retracted=False):
    return ContextAssertion(
        assertion_id=aid,
        dimension=dimension,
        kind=AssertionKind.VALUE,
        raw_value=value,
        canonical_value=value.upper(),
        origin=ContextOrigin.USER,
        supersedes_assertion_id=supersedes,
        retracted=retracted,
    )


def test_a_followup_keeps_previously_verified_facts() -> None:
    history = [
        _assertion("a1", ContextDimension.VENDOR, "ericsson"),
        _assertion("a2", ContextDimension.FAULT, "hw partial fault"),
        _assertion("a3", ContextDimension.NETWORK_ELEMENT, "RRU-3"),
    ]
    state = compute_context_state(history)
    assert state[ContextDimension.VENDOR].state is ContextState.KNOWN
    assert state[ContextDimension.FAULT].state is ContextState.KNOWN
    assert state[ContextDimension.NETWORK_ELEMENT].state is ContextState.KNOWN


def test_a_correction_replaces_rather_than_conflicts() -> None:
    history = [
        _assertion("a1", ContextDimension.VENDOR, "ericsson"),
        _assertion("a2", ContextDimension.VENDOR, "nokia"),
    ]
    assert compute_context_state(history)[ContextDimension.VENDOR].state is ContextState.CONFLICTING

    corrected = history + [_assertion("a3", ContextDimension.VENDOR, "nokia", supersedes="a1")]
    value = compute_context_state(corrected)[ContextDimension.VENDOR]
    assert value.state is ContextState.KNOWN
    assert {a.canonical_value for a in value.accepted} == {"NOKIA"}


def test_a_retraction_removes_a_fact_without_deleting_history() -> None:
    history = [_assertion("a1", ContextDimension.VENDOR, "ericsson", retracted=True)]
    assert effective_assertions(history) == []
    assert len(history) == 1, "the append-only history is never edited"
    assert ContextDimension.VENDOR not in compute_context_state(history)


def test_mutually_superseding_claims_establish_nothing() -> None:
    history = [
        _assertion("a1", ContextDimension.VENDOR, "ericsson", supersedes="a2"),
        _assertion("a2", ContextDimension.VENDOR, "nokia", supersedes="a1"),
    ]
    assert effective_assertions(history) == []


# ===========================================================================
# Investigation state -- interpret before advance
# ===========================================================================


def _investigation() -> InvestigationState:
    return InvestigationState(
        objective="HW Partial Fault",
        knowledge_id="k1",
        version_label="v1",
        section_id="s1",
        prerequisite_status=PrerequisiteStatus.SATISFIED,
    )


def test_a_suggestion_alone_never_advances_the_procedure() -> None:
    state = record_requested_evidence(_investigation(), "the output of the status check")
    assert state.is_awaiting_evidence is True
    assert may_advance_to_next_step(state) is False
    assert advance_after_observation(state).step_index == 0


def test_execution_without_an_observation_does_not_advance() -> None:
    state = record_requested_evidence(_investigation(), "the output of the status check")
    executed = record_observation(state, "I ran it", interpretation=None)
    assert executed.step_lifecycle is StepLifecycle.EXECUTED
    assert may_advance_to_next_step(executed) is False
    assert advance_after_observation(executed).step_index == 0


def test_an_interpreted_observation_is_what_advances() -> None:
    state = record_requested_evidence(_investigation(), "the output of the status check")
    observed = record_observation(state, "status: degraded", interpretation="the unit is still faulty")
    assert observed.step_lifecycle is StepLifecycle.OBSERVED
    assert may_advance_to_next_step(observed) is True

    advanced = advance_after_observation(observed, next_permitted_check="check the optical levels")
    assert advanced.step_index == 1
    assert advanced.received_observation is None
    assert advanced.interpretation is None
    assert advanced.prerequisite_status is PrerequisiteStatus.UNKNOWN


def test_a_new_request_resets_the_observation_half() -> None:
    observed = record_observation(
        record_requested_evidence(_investigation(), "first"), "out", interpretation="meaning"
    )
    reasked = record_requested_evidence(observed, "second")
    assert reasked.received_observation is None
    assert reasked.step_lifecycle is StepLifecycle.SUGGESTED
    assert may_advance_to_next_step(reasked) is False


def test_rendered_context_states_plainly_that_it_is_waiting() -> None:
    state = record_requested_evidence(_investigation(), "the output of the status check")
    rendered = render_investigation_context(state)
    assert "HW Partial Fault" in rendered
    assert "waiting for that evidence" in rendered
    assert "do not describe it as done" in rendered


def test_investigation_state_round_trips_and_parses_fail_closed() -> None:
    state = _investigation()
    assert parse_investigation_state(state.model_dump(mode="json")) == state
    assert parse_investigation_state(None) is None
    assert parse_investigation_state({"nonsense": True}) is None


# ===========================================================================
# Unsupported capabilities
# ===========================================================================


@pytest.mark.parametrize("capability", list(UnsupportedCapability))
def test_every_unsupported_capability_states_it_is_absent(capability: UnsupportedCapability) -> None:
    text = unsupported_capability_text(capability)
    assert text
    assert "can't" in text or "cannot" in text or "not connected" in text
    # Never implies a retry would help.
    assert "try again" not in text.lower()


def test_remote_execution_is_explicitly_unavailable() -> None:
    text = UNSUPPORTED_CAPABILITY_TEXT[UnsupportedCapability.REMOTE_EXECUTION]
    assert "run commands" in text
    assert "you'll need to run it" in text.lower()
