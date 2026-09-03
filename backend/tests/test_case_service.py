"""Tests for backend/cases/service.py -- Case CRUD, membership, session
linking (Case-side bookkeeping), and the context ledger, including the
agent-analysis write restriction (instruction sections 37-40).

Each test constructs a fresh `CaseService()` (defaults to an isolated
in-memory SQLite database -- see service.py's docstring), so tests never
share state with each other.
"""
from __future__ import annotations

import pytest

from backend.cases.schemas import AGENT_ANALYSIS_KINDS, CaseMemberRole, CaseStatus, ContextItemKind
from backend.cases.service import CaseService
from backend.gateway.safe_error import SafeErrorException

ALICE = "alice"
BOB = "bob"
EVE = "eve"


# --- CASE CRUD / MEMBERSHIP (instruction section 37) ------------------------


@pytest.mark.asyncio
async def test_create_case() -> None:
    service = CaseService()
    case = await service.create_case(ALICE, "Packet loss", "Users reporting intermittent packet loss.")
    assert case.title == "Packet loss"
    assert case.problem_statement == "Users reporting intermittent packet loss."
    assert case.status == CaseStatus.OPEN
    assert case.case_id


@pytest.mark.asyncio
async def test_creator_becomes_owner() -> None:
    service = CaseService()
    case = await service.create_case(ALICE, "Title", "Problem")
    assert case.created_by_user_id == ALICE
    # Confirmed functionally: owner-only operation succeeds for alice.
    membership = await service.add_member(ALICE, case.case_id, BOB)
    assert membership.role == CaseMemberRole.MEMBER


@pytest.mark.asyncio
async def test_create_case_rejects_empty_title() -> None:
    service = CaseService()
    with pytest.raises(SafeErrorException):
        await service.create_case(ALICE, "", "Problem")


@pytest.mark.asyncio
async def test_create_case_rejects_empty_problem_statement() -> None:
    service = CaseService()
    with pytest.raises(SafeErrorException):
        await service.create_case(ALICE, "Title", "")


@pytest.mark.asyncio
async def test_case_id_is_server_generated_unique() -> None:
    service = CaseService()
    a = await service.create_case(ALICE, "A", "Problem A")
    b = await service.create_case(ALICE, "B", "Problem B")
    assert a.case_id != b.case_id


@pytest.mark.asyncio
async def test_list_only_my_cases() -> None:
    service = CaseService()
    my_case = await service.create_case(ALICE, "Mine", "Problem")
    await service.create_case(BOB, "Not mine", "Problem")

    cases = await service.list_cases(ALICE)

    assert [c.case_id for c in cases] == [my_case.case_id]


@pytest.mark.asyncio
async def test_retrieve_my_case() -> None:
    service = CaseService()
    case = await service.create_case(ALICE, "Title", "Problem")
    fetched = await service.get_case(ALICE, case.case_id)
    assert fetched.case_id == case.case_id


@pytest.mark.asyncio
async def test_update_case_metadata_and_status() -> None:
    service = CaseService()
    case = await service.create_case(ALICE, "Title", "Problem")

    updated = await service.update_case(
        ALICE, case.case_id, title="New title", status=CaseStatus.INVESTIGATING.value
    )

    assert updated.title == "New title"
    assert updated.status == CaseStatus.INVESTIGATING
    assert updated.updated_at >= case.updated_at


@pytest.mark.asyncio
async def test_owner_adds_member() -> None:
    service = CaseService()
    case = await service.create_case(ALICE, "Title", "Problem")

    membership = await service.add_member(ALICE, case.case_id, BOB, role="member")

    assert membership.user_id == BOB
    assert membership.role == CaseMemberRole.MEMBER
    assert await service.is_member(BOB, case.case_id) is True


@pytest.mark.asyncio
async def test_member_can_access_case() -> None:
    service = CaseService()
    case = await service.create_case(ALICE, "Title", "Problem")
    await service.add_member(ALICE, case.case_id, BOB)

    fetched = await service.get_case(BOB, case.case_id)

    assert fetched.case_id == case.case_id


@pytest.mark.asyncio
async def test_non_member_cannot_access_case() -> None:
    service = CaseService()
    case = await service.create_case(ALICE, "Title", "Problem")

    with pytest.raises(SafeErrorException) as exc_info:
        await service.get_case(EVE, case.case_id)
    assert exc_info.value.safe_error.error_code == "not_found"


@pytest.mark.asyncio
async def test_foreign_case_behaves_as_not_found() -> None:
    service = CaseService()

    with pytest.raises(SafeErrorException) as exc_info:
        await service.get_case(ALICE, "totally-made-up-case-id")
    assert exc_info.value.safe_error.error_code == "not_found"


@pytest.mark.asyncio
async def test_non_member_and_unknown_case_are_indistinguishable() -> None:
    service = CaseService()
    case = await service.create_case(ALICE, "Title", "Problem")

    with pytest.raises(SafeErrorException) as foreign_exc:
        await service.get_case(EVE, case.case_id)
    with pytest.raises(SafeErrorException) as unknown_exc:
        await service.get_case(EVE, "made-up-id")

    assert foreign_exc.value.safe_error.error_code == unknown_exc.value.safe_error.error_code
    assert foreign_exc.value.safe_error.user_message == unknown_exc.value.safe_error.user_message


@pytest.mark.asyncio
async def test_non_owner_member_cannot_manage_membership() -> None:
    service = CaseService()
    case = await service.create_case(ALICE, "Title", "Problem")
    await service.add_member(ALICE, case.case_id, BOB)

    with pytest.raises(SafeErrorException) as exc_info:
        await service.add_member(BOB, case.case_id, EVE)
    assert exc_info.value.safe_error.error_code == "authorization_error"

    # Eve was never actually added.
    assert await service.is_member(EVE, case.case_id) is False


@pytest.mark.asyncio
async def test_non_member_cannot_manage_membership_either() -> None:
    service = CaseService()
    case = await service.create_case(ALICE, "Title", "Problem")

    with pytest.raises(SafeErrorException) as exc_info:
        await service.add_member(EVE, case.case_id, BOB)
    # Eve isn't even a member -- same not_found as any other foreign-case access.
    assert exc_info.value.safe_error.error_code == "not_found"


# --- SESSION LINKING (Case-side bookkeeping; instruction section 38) --------


@pytest.mark.asyncio
async def test_link_session_to_accessible_case() -> None:
    service = CaseService()
    case = await service.create_case(ALICE, "Title", "Problem")

    link = await service.link_session(ALICE, case.case_id, "session-1", session_owner_user_id=ALICE)

    assert link.case_id == case.case_id
    assert link.session_id == "session-1"


@pytest.mark.asyncio
async def test_non_member_cannot_link_to_case() -> None:
    service = CaseService()
    case = await service.create_case(ALICE, "Title", "Problem")

    with pytest.raises(SafeErrorException) as exc_info:
        await service.link_session(EVE, case.case_id, "session-1", session_owner_user_id=EVE)
    assert exc_info.value.safe_error.error_code == "not_found"


@pytest.mark.asyncio
async def test_session_cannot_belong_to_two_cases_at_once() -> None:
    service = CaseService()
    case_a = await service.create_case(ALICE, "Case A", "Problem A")
    case_b = await service.create_case(ALICE, "Case B", "Problem B")
    await service.link_session(ALICE, case_a.case_id, "session-1", session_owner_user_id=ALICE)

    with pytest.raises(SafeErrorException) as exc_info:
        await service.link_session(ALICE, case_b.case_id, "session-1", session_owner_user_id=ALICE)
    assert exc_info.value.safe_error.error_code == "action_failure"

    # Still linked to case A, unaffected.
    link = await service.get_link_for_session("session-1")
    assert link.case_id == case_a.case_id


@pytest.mark.asyncio
async def test_explicit_unlink_works() -> None:
    service = CaseService()
    case = await service.create_case(ALICE, "Title", "Problem")
    await service.link_session(ALICE, case.case_id, "session-1", session_owner_user_id=ALICE)

    await service.unlink_session(ALICE, case.case_id, "session-1")

    assert await service.get_link_for_session("session-1") is None


@pytest.mark.asyncio
async def test_unlink_then_relink_to_a_different_case_succeeds() -> None:
    service = CaseService()
    case_a = await service.create_case(ALICE, "Case A", "Problem A")
    case_b = await service.create_case(ALICE, "Case B", "Problem B")
    await service.link_session(ALICE, case_a.case_id, "session-1", session_owner_user_id=ALICE)
    await service.unlink_session(ALICE, case_a.case_id, "session-1")

    link = await service.link_session(ALICE, case_b.case_id, "session-1", session_owner_user_id=ALICE)

    assert link.case_id == case_b.case_id


@pytest.mark.asyncio
async def test_session_and_case_ids_remain_independent() -> None:
    """Session ids and case ids are generated by completely different
    systems (ADK vs. this service) and never collide/overlap in shape.
    """
    service = CaseService()
    case = await service.create_case(ALICE, "Title", "Problem")
    link = await service.link_session(ALICE, case.case_id, "totally-different-shaped-session-id", session_owner_user_id=ALICE)
    assert link.session_id == "totally-different-shaped-session-id"
    assert link.case_id != link.session_id


# --- CONTEXT LEDGER (instruction section 39) --------------------------------


@pytest.mark.asyncio
async def test_user_observation_persisted() -> None:
    service = CaseService()
    case = await service.create_case(ALICE, "Title", "Problem")

    item = await service.add_user_context_item(ALICE, case.case_id, "observation", "Loss started 14:00 UTC.")

    assert item.kind == ContextItemKind.OBSERVATION
    assert item.content == "Loss started 14:00 UTC."


@pytest.mark.asyncio
async def test_evidence_and_all_kinds_round_trip() -> None:
    service = CaseService()
    case = await service.create_case(ALICE, "Title", "Problem")

    for kind in ContextItemKind:
        item = await service.add_user_context_item(ALICE, case.case_id, kind.value, f"content for {kind.value}")
        assert item.kind == kind


@pytest.mark.asyncio
async def test_source_provenance_retained() -> None:
    service = CaseService()
    case = await service.create_case(ALICE, "Title", "Problem")

    item = await service.add_user_context_item(ALICE, case.case_id, "evidence", "Something observed.")

    assert item.source_type.value == "user"
    assert item.source_author == ALICE
    assert item.created_by_user_id == ALICE


@pytest.mark.asyncio
async def test_user_author_is_derived_from_user_context_not_client_input() -> None:
    """`add_user_context_item` has no `author`/`source_type` parameter at
    all -- structurally, a caller cannot supply either.
    """
    import inspect

    params = list(inspect.signature(CaseService.add_user_context_item).parameters)
    assert "source_type" not in params
    assert "author" not in params
    assert "source_author" not in params


@pytest.mark.asyncio
async def test_full_context_survives_store_recreation_is_covered_separately() -> None:
    """See test_case_persistence.py for the real-SQLite version of this --
    this file uses fast in-memory databases that are NOT restart-safe by
    design (each `CaseService()` gets its own isolated in-memory DB), so
    restart-survival is deliberately tested there, not here.
    """


@pytest.mark.asyncio
async def test_cases_have_isolated_ledgers() -> None:
    service = CaseService()
    case_a = await service.create_case(ALICE, "Case A", "Problem A")
    case_b = await service.create_case(ALICE, "Case B", "Problem B")
    await service.add_user_context_item(ALICE, case_a.case_id, "observation", "Only in A")

    items_a = await service.get_context_items(ALICE, case_a.case_id)
    items_b = await service.get_context_items(ALICE, case_b.case_id)

    assert len(items_a) == 1
    assert len(items_b) == 0


# --- AGENT ANALYSIS STORAGE (instruction section 40) ------------------------


@pytest.mark.asyncio
async def test_hypothesis_allowed_from_agent() -> None:
    service = CaseService()
    case = await service.create_case(ALICE, "Title", "Problem")

    item = await service.record_case_analysis(ALICE, "team_manager", case.case_id, "hypothesis", "Maybe X caused it.")

    assert item.kind == ContextItemKind.HYPOTHESIS
    assert item.source_type.value == "agent"
    assert item.created_by_agent == "team_manager"


@pytest.mark.asyncio
async def test_recommendation_allowed_from_agent() -> None:
    service = CaseService()
    case = await service.create_case(ALICE, "Title", "Problem")
    item = await service.record_case_analysis(ALICE, "team_manager", case.case_id, "recommendation", "Check counters.")
    assert item.kind == ContextItemKind.RECOMMENDATION


@pytest.mark.asyncio
async def test_open_question_allowed_from_agent() -> None:
    service = CaseService()
    case = await service.create_case(ALICE, "Title", "Problem")
    item = await service.record_case_analysis(ALICE, "team_manager", case.case_id, "open_question", "Is X related?")
    assert item.kind == ContextItemKind.OPEN_QUESTION


@pytest.mark.asyncio
async def test_agent_analysis_kinds_are_exactly_the_documented_three() -> None:
    assert AGENT_ANALYSIS_KINDS == {
        ContextItemKind.HYPOTHESIS,
        ContextItemKind.RECOMMENDATION,
        ContextItemKind.OPEN_QUESTION,
    }


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "forbidden_kind",
    ["evidence", "observation", "decision", "action", "risk", "resolution"],
)
async def test_model_cannot_directly_write_disallowed_kinds(forbidden_kind: str) -> None:
    service = CaseService()
    case = await service.create_case(ALICE, "Title", "Problem")

    with pytest.raises(SafeErrorException) as exc_info:
        await service.record_case_analysis(ALICE, "team_manager", case.case_id, forbidden_kind, "content")
    assert exc_info.value.safe_error.error_code == "validation_error"

    items = await service.get_context_items(ALICE, case.case_id)
    assert items == []


@pytest.mark.asyncio
async def test_supporting_item_ids_are_validated() -> None:
    service = CaseService()
    case = await service.create_case(ALICE, "Title", "Problem")
    evidence_item = await service.add_user_context_item(ALICE, case.case_id, "evidence", "Some evidence.")

    analysis = await service.record_case_analysis(
        ALICE, "team_manager", case.case_id, "hypothesis", "Based on evidence.", supporting_item_ids=[evidence_item.item_id]
    )

    assert analysis.supporting_item_ids == [evidence_item.item_id]


@pytest.mark.asyncio
async def test_fabricated_supporting_id_is_rejected() -> None:
    service = CaseService()
    case = await service.create_case(ALICE, "Title", "Problem")

    with pytest.raises(SafeErrorException) as exc_info:
        await service.record_case_analysis(
            ALICE, "team_manager", case.case_id, "hypothesis", "content", supporting_item_ids=["fabricated-id"]
        )
    assert exc_info.value.safe_error.error_code == "validation_error"

    items = await service.get_context_items(ALICE, case.case_id)
    assert items == []


@pytest.mark.asyncio
async def test_supporting_item_from_another_case_is_rejected() -> None:
    service = CaseService()
    case_a = await service.create_case(ALICE, "Case A", "Problem A")
    case_b = await service.create_case(ALICE, "Case B", "Problem B")
    item_in_a = await service.add_user_context_item(ALICE, case_a.case_id, "evidence", "Evidence in A")

    with pytest.raises(SafeErrorException) as exc_info:
        await service.record_case_analysis(
            ALICE, "team_manager", case_b.case_id, "hypothesis", "content", supporting_item_ids=[item_in_a.item_id]
        )
    assert exc_info.value.safe_error.error_code == "validation_error"


@pytest.mark.asyncio
async def test_agent_analysis_re_verifies_membership_independently() -> None:
    """Defense in depth: even if a caller somehow invoked this with a
    user_id that is not a Case member, the domain service itself denies
    it -- never relying solely on an upstream tool-layer check.
    """
    service = CaseService()
    case = await service.create_case(ALICE, "Title", "Problem")

    with pytest.raises(SafeErrorException) as exc_info:
        await service.record_case_analysis(EVE, "team_manager", case.case_id, "hypothesis", "content")
    assert exc_info.value.safe_error.error_code == "not_found"


@pytest.mark.asyncio
async def test_no_chain_of_thought_field_exists_on_context_item_schema() -> None:
    from backend.cases.schemas import CaseContextItemDTO

    fields = set(CaseContextItemDTO.model_fields)
    for forbidden in ("reasoning", "chain_of_thought", "thoughts", "internal_reasoning", "scratchpad"):
        assert forbidden not in fields
