"""POST-6A PROMPT 3 -- INTEGRATION checks through the real entry points.

Each test drives a genuine production path with a fake PROVIDER (a fake
repository, a fake embedding provider, a FastAPI TestClient) -- never a
helper called in isolation. The point is to prove the production wiring
exists, not that the helpers work.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

import pytest
from fastapi.testclient import TestClient

from backend.api.turn_context import bind_run_id, bind_user_id, reset_run_id, reset_user_id
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
from backend.knowledge.shared_evidence import discard_turn_evidence

_NOW = datetime(2026, 1, 1, tzinfo=timezone.utc)


# ===========================================================================
# Fake providers
# ===========================================================================


def _doc(knowledge_id: str, label: str, status: LifecycleStatus, content: str) -> KnowledgeObject:
    return KnowledgeObject(
        knowledge_id=knowledge_id,
        document_type=KnowledgeDocumentType.OPERATIONAL_PROCEDURE,
        title=f"{knowledge_id} {label}",
        version=KnowledgeVersion(label=label),
        lifecycle_status=status,
        source=KnowledgeSource(source_system="local", source_id=knowledge_id),
        applicability=Applicability(dimensions={"vendor": ["ericsson"]}),
        sections=[
            KnowledgeSection(
                section_id=f"{label}-s1", knowledge_id=knowledge_id, sequence=0, content=content
            )
        ],
    )


class FakeKnowledgeRepository:
    """Duck-typed `KnowledgeRepository` -- the real services run against it."""

    def __init__(self, objects: list[KnowledgeObject]) -> None:
        self.objects = {(o.knowledge_id, o.version.label): o for o in objects}

    async def list_all(self) -> list[KnowledgeObject]:
        return list(self.objects.values())

    async def get(self, knowledge_id: str, version_label: str) -> Optional[KnowledgeObject]:
        return self.objects.get((knowledge_id, version_label))

    async def add(self, knowledge_object: KnowledgeObject) -> None:
        self.objects[(knowledge_object.knowledge_id, knowledge_object.version.label)] = knowledge_object

    async def replace(self, knowledge_object: KnowledgeObject) -> None:
        self.objects[(knowledge_object.knowledge_id, knowledge_object.version.label)] = knowledge_object


def _ericsson_context():
    from backend.context.domain.enums import AssertionKind, ContextDimension, ContextOrigin
    from backend.context.domain.models import ContextAssertion, compute_context_state

    return compute_context_state(
        [
            ContextAssertion(
                assertion_id="c1",
                dimension=ContextDimension.VENDOR,
                kind=AssertionKind.VALUE,
                raw_value="ericsson",
                canonical_value="ERICSSON",
                origin=ContextOrigin.USER,
            )
        ]
    )


def _shared_service(repository: FakeKnowledgeRepository):
    from backend.knowledge.provenance.service import KnowledgeProvenanceService
    from backend.knowledge.retrieval.service import KnowledgeRetrievalService
    from backend.knowledge.shared_evidence import SharedEvidenceService

    return SharedEvidenceService(
        repository,
        retrieval_service=KnowledgeRetrievalService(repository),
        provenance_service=KnowledgeProvenanceService(repository),
    )


# ===========================================================================
# 1. Both specialists reach shared evidence
# ===========================================================================


@pytest.mark.asyncio
async def test_incident_manager_knowledge_search_reaches_shared_evidence(monkeypatch) -> None:
    """Drives the REAL `knowledge_search` tool, through the REAL
    `KnowledgeToolService`, and asserts the shared evidence path ran."""
    import backend.tools.knowledge.runtime as runtime
    import backend.tools.knowledge.tools as tools
    from backend.knowledge.provenance.service import KnowledgeProvenanceService
    from backend.knowledge.retrieval.service import KnowledgeRetrievalService
    from backend.knowledge.tools.service import KnowledgeToolService

    repository = FakeKnowledgeRepository([_doc("K1", "2.0", LifecycleStatus.APPROVED, "restart the faulty unit")])
    shared = _shared_service(repository)
    service = KnowledgeToolService(
        KnowledgeRetrievalService(repository),
        KnowledgeProvenanceService(repository),
        shared_evidence_service=shared,
    )
    monkeypatch.setattr(tools, "get_knowledge_tool_service", lambda: service)

    acquired: list[str] = []
    original = shared.acquire

    async def _spy(request, context_state=None, **kwargs):
        acquired.append(request.requested_by)
        return await original(request, context_state, **kwargs)

    monkeypatch.setattr(shared, "acquire", _spy)

    run_token = bind_run_id("run-integration-1")
    user_token = bind_user_id("alice")
    try:
        state = runtime.get_or_init_run_state("run-integration-1")
        state.context_state = _ericsson_context()
        result = await tools.knowledge_search("restart", limit=3)
    finally:
        runtime.discard_knowledge_run_evidence_state("run-integration-1")
        discard_turn_evidence("run-integration-1")
        reset_user_id(user_token)
        reset_run_id(run_token)

    assert "error" not in result
    assert acquired == ["incident_manager"], "knowledge_search must consume the shared evidence service"


@pytest.mark.asyncio
async def test_troubleshooting_manager_reaches_the_same_shared_evidence(monkeypatch) -> None:
    """Drives the REAL `query_selected_evidence` entry point and asserts
    it goes through the same shared service (and its per-turn cache)."""
    import backend.agents.troubleshooting_manager.context_support as context_support
    import backend.tools.knowledge.runtime as runtime

    repository = FakeKnowledgeRepository([_doc("K1", "2.0", LifecycleStatus.APPROVED, "restart the faulty unit")])
    shared = _shared_service(repository)
    monkeypatch.setattr(runtime, "get_shared_evidence_service", lambda: shared)

    acquired: list[str] = []
    original = shared.acquire

    async def _spy(request, context_state=None, **kwargs):
        acquired.append(request.requested_by)
        return await original(request, context_state, **kwargs)

    monkeypatch.setattr(shared, "acquire", _spy)

    run_token = bind_run_id("run-integration-2")
    user_token = bind_user_id("alice")
    try:
        selection = await context_support.query_selected_evidence("restart", _ericsson_context())
    finally:
        discard_turn_evidence("run-integration-2")
        reset_user_id(user_token)
        reset_run_id(run_token)

    assert acquired == ["troubleshooting_manager"]
    assert selection.selection_reason == "shared_evidence_service"
    assert selection.selected, "authorized evidence must reach the troubleshooting specialist"


@pytest.mark.asyncio
async def test_a_different_user_scope_never_reuses_a_cached_result() -> None:
    from backend.knowledge.shared_evidence import EvidenceRequest

    repository = FakeKnowledgeRepository([_doc("K1", "2.0", LifecycleStatus.APPROVED, "restart the faulty unit")])
    shared = _shared_service(repository)
    context = _ericsson_context()
    try:
        first = await shared.acquire(
            EvidenceRequest(run_id="run-scope", query_text="restart", as_of=_NOW, user_id="alice"), context
        )
        same = await shared.acquire(
            EvidenceRequest(run_id="run-scope", query_text="restart", as_of=_NOW, user_id="alice"), context
        )
        other = await shared.acquire(
            EvidenceRequest(run_id="run-scope", query_text="restart", as_of=_NOW, user_id="bob"), context
        )
        different_query = await shared.acquire(
            EvidenceRequest(run_id="run-scope", query_text="reseat", as_of=_NOW, user_id="alice"), context
        )
    finally:
        discard_turn_evidence("run-scope")

    assert first.reused is False
    assert same.reused is True, "the same user, query and scope reuses"
    assert other.reused is False, "a different user must never reuse a cached result"
    assert different_query.reused is False, "a different query must never reuse a cached result"


# ===========================================================================
# 2. Disallowed versions never enter retrieval candidates
# ===========================================================================


@pytest.mark.asyncio
async def test_disallowed_versions_never_enter_retrieval_candidates() -> None:
    """The ARCHIVE version's section text is the ONLY one matching the
    query, so if authorization were a post-filter it would occupy the
    candidate slot and the result would be empty. It must never be a
    candidate at all."""
    from backend.knowledge.retrieval.contracts import KnowledgeRetrievalQuery
    from backend.knowledge.retrieval.service import KnowledgeRetrievalService

    repository = FakeKnowledgeRepository(
        [
            _doc("K1", "1.0", LifecycleStatus.ARCHIVE, "obsolete restart guidance"),
            _doc("K1", "2.0", LifecycleStatus.APPROVED, "current restart guidance"),
        ]
    )
    retrieval = KnowledgeRetrievalService(repository)

    unconstrained = await retrieval.retrieve(
        KnowledgeRetrievalQuery(query_text="obsolete restart", as_of=_NOW, limit=5)
    )
    constrained = await retrieval.retrieve(
        KnowledgeRetrievalQuery(
            query_text="obsolete restart", as_of=_NOW, limit=5, permitted_version_keys=[("K1", "2.0")]
        )
    )
    assert all(item.version_label == "2.0" for item in constrained.items)
    assert "1.0" not in {item.version_label for item in constrained.items}
    # And an empty permitted set is never read as "no filter".
    empty = await retrieval.retrieve(
        KnowledgeRetrievalQuery(query_text="restart", as_of=_NOW, limit=5, permitted_version_keys=[])
    )
    assert empty.items == []
    del unconstrained


@pytest.mark.asyncio
async def test_shared_evidence_never_returns_a_disallowed_version() -> None:
    from backend.knowledge.shared_evidence import EvidenceAvailability, EvidenceRequest

    repository = FakeKnowledgeRepository(
        [
            _doc("K1", "1.0", LifecycleStatus.ARCHIVE, "restart guidance"),
            _doc("K1", "2.0", LifecycleStatus.APPROVED, "restart guidance"),
        ]
    )
    shared = _shared_service(repository)
    try:
        result = await shared.acquire(
            EvidenceRequest(run_id="run-ver", query_text="restart", as_of=_NOW), _ericsson_context()
        )
    finally:
        discard_turn_evidence("run-ver")
    assert result.availability is EvidenceAvailability.AVAILABLE
    assert {item.reference.version_label for item in result.items} == {"2.0"}


# ===========================================================================
# 3. Authoring -> authorized approval -> runtime resolution
# ===========================================================================


def _governance_client(monkeypatch, repository: FakeKnowledgeRepository, store):
    import backend.api.app as app_module
    from backend.api.knowledge_governance_service import KnowledgeGovernanceService

    service = KnowledgeGovernanceService(repository, store)
    app = app_module.create_app()
    app.dependency_overrides[app_module.get_knowledge_governance_service] = lambda: service
    return TestClient(app), service


@pytest.mark.asyncio
async def test_authoring_then_authorized_approval_then_runtime_resolution(monkeypatch, tmp_path) -> None:
    """The full human path through the REAL HTTP API, then the REAL
    runtime resolver reading what it produced."""
    from backend.agents.team_manager.governed_operation_resolution import (
        resolve_governed_operation_descriptor,
    )
    from backend.knowledge.governance.operation_approval_store import OperationApprovalStore
    from backend.knowledge.provenance.contracts import KnowledgeEvidenceItem
    from backend.knowledge.domain.contracts import KnowledgeEvidenceReference

    repository = FakeKnowledgeRepository([_doc("K1", "2.0", LifecycleStatus.APPROVED, "restart the unit")])
    # `dev_mode=True` mirrors the deployment configuration these routes
    # now REQUIRE: governance mutations fail closed until a deployment
    # explicitly declares itself a development environment, because the
    # identity they stand on is an unverified header.
    store = OperationApprovalStore(
        f"sqlite+aiosqlite:///{tmp_path / 'approvals.db'}",
        governors=frozenset({"governor"}),
        dev_mode=True,
        manage_schema=True,
    )
    monkeypatch.setenv("SLOPANOC_KNOWLEDGE_GOVERNORS", "governor")
    monkeypatch.setenv("SLOPANOC_GOVERNANCE_DEV_MODE", "true")
    client, service = _governance_client(monkeypatch, repository, store)

    base = "/api/knowledge/K1/versions/2.0/operations"

    # 1. DRAFT -- POST-6A, permission-gated. Drafting grants nothing, but
    # it WITHDRAWS any live approval on the section, so a non-governor
    # could otherwise disable a governed operation for everyone.
    refused_draft = client.post(
        f"{base}/2.0-s1/draft",
        json={
            "descriptor": GovernedOperationDescriptor(
                operation_id="restart-rru", target_scope=OperationTargetScope.TARGET_INDEPENDENT
            ).model_dump(mode="json")
        },
        headers={"X-SLOPANOC-DEV-USER": "author"},
    )
    assert refused_draft.status_code == 403, refused_draft.text

    drafted = client.post(
        f"{base}/2.0-s1/draft",
        json={
            "descriptor": GovernedOperationDescriptor(
                operation_id="restart-rru", target_scope=OperationTargetScope.TARGET_INDEPENDENT
            ).model_dump(mode="json")
        },
        headers={"X-SLOPANOC-DEV-USER": "governor"},
    )
    assert drafted.status_code == 200, drafted.text
    assert drafted.json()["authority"] == "candidate"

    # 2. APPROVE -- refused for a non-governor.
    refused = client.post(f"{base}/2.0-s1/approve", json={}, headers={"X-SLOPANOC-DEV-USER": "author"})
    assert refused.status_code == 403

    approved = client.post(
        f"{base}/2.0-s1/approve", json={"note": "reviewed"}, headers={"X-SLOPANOC-DEV-USER": "governor"}
    )
    assert approved.status_code == 200, approved.text
    assert approved.json()["approved_by"] == "governor"
    assert approved.json()["descriptor_fingerprint"]

    # 3. RUNTIME RESOLUTION -- the real resolver sees an APPROVED descriptor.
    stored = await repository.get("K1", "2.0")
    item = KnowledgeEvidenceItem(
        reference=KnowledgeEvidenceReference(
            knowledge_id="K1", version_label="2.0", section_id="2.0-s1", source_system="local", source_id="K1"
        ),
        title=stored.title,
        document_type=stored.document_type,
        lifecycle_status=stored.lifecycle_status,
        source=stored.source,
        section=stored.sections[0],
    )
    resolved = resolve_governed_operation_descriptor(
        [item], knowledge_id="K1", version_label="2.0", section_id="2.0-s1"
    )
    assert resolved is not None
    assert resolved.authority is OperationDescriptorAuthority.APPROVED

    # 4. REVOCATION removes runtime authority.
    revoked = client.post(f"{base}/2.0-s1/revoke", headers={"X-SLOPANOC-DEV-USER": "governor"})
    assert revoked.status_code == 200 and revoked.json()["revoked"] is True
    stored_after = await repository.get("K1", "2.0")
    assert stored_after.sections[0].operation.authority is OperationDescriptorAuthority.CANDIDATE

    await store.close()


@pytest.mark.asyncio
async def test_editing_a_descriptor_invalidates_its_approval(tmp_path) -> None:
    from backend.knowledge.governance.operation_approval_store import OperationApprovalStore

    repository = FakeKnowledgeRepository([_doc("K1", "2.0", LifecycleStatus.APPROVED, "restart the unit")])
    store = OperationApprovalStore(
        f"sqlite+aiosqlite:///{tmp_path / 'approvals2.db'}",
        governors=frozenset({"governor"}),
        dev_mode=True,
        manage_schema=True,
    )
    from backend.api.knowledge_governance_service import KnowledgeGovernanceService

    service = KnowledgeGovernanceService(repository, store)
    await service.author_operation(
        "K1",
        "2.0",
        "2.0-s1",
        GovernedOperationDescriptor(operation_id="op", target_scope=OperationTargetScope.SINGLE_TARGET),
        actor_user_id="governor",
    )
    await service.approve_operation("K1", "2.0", "2.0-s1", actor_user_id="governor")
    assert (await repository.get("K1", "2.0")).sections[0].operation.authority is (
        OperationDescriptorAuthority.APPROVED
    )

    # An EDIT re-drafts as CANDIDATE and the stored approval stops matching.
    await service.author_operation(
        "K1",
        "2.0",
        "2.0-s1",
        GovernedOperationDescriptor(operation_id="op", target_scope=OperationTargetScope.TARGET_INDEPENDENT),
        actor_user_id="governor",
    )
    edited = await repository.get("K1", "2.0")
    assert edited.sections[0].operation.authority is OperationDescriptorAuthority.CANDIDATE
    assert await store.active_approval_for(edited, "2.0-s1") is None
    await store.close()


# ===========================================================================
# 4. Stale target confirmation is rejected
# ===========================================================================


def test_stale_target_confirmation_is_rejected() -> None:
    from backend.agents.team_manager.request_contract import (
        PendingGovernedRequest,
        RequestClass,
        RequestedOutput,
        RequestIntent,
        RequestParameter,
        TargetConfirmation,
    )
    from backend.agents.team_manager.target_confirmation import (
        TargetConfirmationRejection,
        apply_confirmations,
        build_confirmation,
    )

    pending = PendingGovernedRequest(
        request_class=RequestClass.EXACT_COMMAND,
        requested_output=RequestedOutput.EXACT_COMMAND,
        intent=RequestIntent.COMMAND,
        subject="restart RRU",
        missing_context=["unit_id"],
    )
    param = RequestParameter(name="unit_id", value="RRU-3", provenance="user")
    record = build_confirmation(param, pending, operation_id="restart-rru", candidate_revision="rev-1")

    live, rejections = apply_confirmations(
        [param], [record], pending, operation_id="restart-rru", candidate_revision="rev-1"
    )
    assert live[0].confirmation is TargetConfirmation.CONFIRMED
    assert rejections == {}

    # The governed operation changed since the confirmation was taken.
    stale_op, rejected_op = apply_confirmations(
        [param], [record], pending, operation_id="reseat-rru", candidate_revision="rev-1"
    )
    assert stale_op[0].confirmation is TargetConfirmation.MENTIONED
    assert rejected_op["unit_id"] == TargetConfirmationRejection.OPERATION_CHANGED.value

    # The descriptor was edited and re-approved.
    stale_rev, rejected_rev = apply_confirmations(
        [param], [record], pending, operation_id="restart-rru", candidate_revision="rev-2"
    )
    assert stale_rev[0].confirmation is TargetConfirmation.MENTIONED
    assert rejected_rev["unit_id"] == TargetConfirmationRejection.CANDIDATE_REVISION_CHANGED.value

    # The outstanding question itself changed.
    other_pending = pending.model_copy(update={"subject": "reseat RRU"})
    stale_req, rejected_req = apply_confirmations(
        [param], [record], other_pending, operation_id="restart-rru", candidate_revision="rev-1"
    )
    assert stale_req[0].confirmation is TargetConfirmation.MENTIONED
    assert rejected_req["unit_id"] == TargetConfirmationRejection.PENDING_REQUEST_CHANGED.value

    # A correction supersedes the confirmation of the old value.
    corrected = RequestParameter(
        name="unit_id", value="RRU-10", provenance="user", corrects_prior_value="RRU-3"
    )
    stale_corr, rejected_corr = apply_confirmations(
        [corrected], [record], pending, operation_id="restart-rru", candidate_revision="rev-1"
    )
    assert stale_corr[0].confirmation is TargetConfirmation.MENTIONED
    assert rejected_corr["unit_id"] == TargetConfirmationRejection.SUPERSEDED_BY_CORRECTION.value


def test_confirmation_is_not_permission_to_execute() -> None:
    """A confirmed target says which unit. It never says "act on it"."""
    from backend.agents.team_manager.request_contract import (
        RequestContract,
        RequestedOutput,
        RequestIntent,
        RequestParameter,
        TargetConfirmation,
        derive_request_class,
    )
    from backend.agents.team_manager.request_execution_policy import derive_execution_decision

    confirmed = RequestParameter(
        name="unit_id", value="RRU-3", provenance="user", confirmation=TargetConfirmation.CONFIRMED
    )
    contract = RequestContract(
        intent=RequestIntent.ACTION,
        requested_output=RequestedOutput.ACTION,
        subject="restart RRU",
        action_requested=True,
        provided_context=[confirmed],
    )
    contract = contract.model_copy(
        update={
            "request_class": derive_request_class(
                contract.intent, contract.requested_output, contract.action_requested, contract.subject
            ),
            "run_id": "r1",
        }
    )
    decision = derive_execution_decision(contract, "r1")
    assert decision.may_execute_action is False
    assert decision.approval_required is True


# ===========================================================================
# 5. Observation -> validated interpretation -> permitted next step
# ===========================================================================


def test_user_observation_validated_interpretation_then_next_step() -> None:
    from backend.agents.incident_manager.schemas import ObservationInterpretation
    from backend.agents.team_manager.investigation_state import (
        InvestigationState,
        PrerequisiteStatus,
        StepLifecycle,
        advance_after_observation,
        may_advance_to_next_step,
        record_requested_evidence,
        record_user_observation,
        record_validated_interpretation,
    )

    state = record_requested_evidence(
        InvestigationState(objective="HW Partial Fault", prerequisite_status=PrerequisiteStatus.SATISFIED),
        "the output of the status check",
    )

    # A bare approval is captured as the observation but proves nothing.
    yes = record_user_observation(state, "yes", source_turn_id="inv-1")
    assert yes.step_lifecycle is StepLifecycle.EXECUTED
    bad, reason = record_validated_interpretation(
        yes,
        ObservationInterpretation(
            observation_reference="status: degraded", meaning="still faulty", concludes_step=True
        ),
    )
    assert reason == "observation_reference_not_found"
    assert may_advance_to_next_step(bad) is False

    # A real reported result, quoted accurately, does advance.
    observed = record_user_observation(
        state, "I ran it, output was:\nstatus: DEGRADED  rc=3", source_turn_id="inv-2", attachment_ids=["att-1"]
    )
    assert observed.observation_source_turn_id == "inv-2"
    assert observed.observation_attachment_ids == ("att-1",)

    interpreted, outcome = record_validated_interpretation(
        observed,
        ObservationInterpretation(
            observation_reference="status: DEGRADED", meaning="The unit is still faulty.", concludes_step=True
        ),
    )
    assert outcome == "accepted"
    assert interpreted.step_lifecycle is StepLifecycle.OBSERVED
    assert may_advance_to_next_step(interpreted) is True

    advanced = advance_after_observation(interpreted, next_permitted_check="check optical levels")
    assert advanced.step_index == 1
    assert advanced.received_observation is None

    # An inconclusive interpretation does NOT advance.
    inconclusive, outcome2 = record_validated_interpretation(
        observed,
        ObservationInterpretation(
            observation_reference="rc=3", meaning="Partial output only.", concludes_step=False
        ),
    )
    assert outcome2 == "inconclusive"
    assert may_advance_to_next_step(inconclusive) is False


def test_chat_service_calls_advance_from_the_orchestration_path() -> None:
    import inspect

    from backend.api import chat_service

    source = inspect.getsource(chat_service)
    assert "record_candidate_observation(" in source
    assert "record_validated_interpretation(" in source
    assert "advance_after_observation(" in source


# ===========================================================================
# 6. Retrieval outage produces the correct user-visible status
# ===========================================================================


@pytest.mark.asyncio
async def test_retrieval_outage_is_reported_as_an_outage_not_a_request_for_context() -> None:
    from backend.knowledge.shared_evidence import (
        EvidenceAvailability,
        EvidenceRequest,
        get_run_availability,
    )

    class _BoomRetrieval:
        async def retrieve(self, query):
            raise RuntimeError("database unavailable")

    class _Provenance:
        async def build_evidence_set(self, retrieval_result):
            raise AssertionError("not reached")

    from backend.knowledge.shared_evidence import SharedEvidenceService

    repository = FakeKnowledgeRepository([_doc("K1", "2.0", LifecycleStatus.APPROVED, "restart")])
    shared = SharedEvidenceService(
        repository, retrieval_service=_BoomRetrieval(), provenance_service=_Provenance()
    )
    try:
        result = await shared.acquire(
            EvidenceRequest(run_id="run-outage", query_text="restart", as_of=_NOW), _ericsson_context()
        )
        recorded = get_run_availability("run-outage")
        assert result.availability is EvidenceAvailability.RETRIEVAL_UNAVAILABLE
        assert recorded is not None and recorded.is_infrastructure_failure is True

        from backend.agents.team_manager.request_execution_policy import (
            EVIDENCE_RETRIEVAL_UNAVAILABLE_TEXT,
        )

        assert "problem on my side" in EVIDENCE_RETRIEVAL_UNAVAILABLE_TEXT
        assert "please confirm" not in EVIDENCE_RETRIEVAL_UNAVAILABLE_TEXT.lower()
    finally:
        discard_turn_evidence("run-outage")
    # And the run-scoped record is gone once the turn ends.
    assert get_run_availability("run-outage") is None


def test_chat_service_prefers_the_outage_status_over_clarification() -> None:
    import inspect

    from backend.api import chat_service

    source = inspect.getsource(chat_service)
    assert "run_evidence.is_infrastructure_failure" in source
    assert "final_response_path = \"evidence_retrieval_unavailable\"" in source
    # The outage branch comes BEFORE the clarification branch.
    assert source.index("is_infrastructure_failure") < source.index("_ResponseMode.CLARIFICATION:")


@pytest.mark.asyncio
async def test_semantic_degradation_preserves_authorized_lexical_results() -> None:
    """Drives the real `hybrid_retrieve` with a failing embedding
    provider -- authorized exact/lexical hits survive and the loss is
    reported."""
    from backend.knowledge.hybrid_retrieval.contracts import (
        ChannelHit,
        EvidenceIndexRecord,
        HybridRetrievalQuery,
        RetrievalChannel,
    )
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
            return [ChannelHit(evidence_id="e1", channel=RetrievalChannel.LEXICAL, raw_score=0.5)]

        async def semantic_search(self, ids, vec, limit, permitted_version_keys=None):
            raise AssertionError("unreachable once embedding fails")

        async def get_many(self, ids):
            return {"e1": record}

    class _BrokenEmbedding:
        async def embed(self, texts):
            raise RuntimeError("embedding provider unavailable")

    result = await hybrid_retrieve(
        HybridRetrievalQuery(
            query_text="restart", permitted_knowledge_ids=["K1"], permitted_version_keys=[("K1", "2.0")]
        ),
        _Repo(),
        _BrokenEmbedding(),
    )
    assert [c.record.evidence_id for c in result.candidates] == ["e1"]
    assert result.telemetry.semantic_channel_mode == "degraded_embedding_unavailable"
