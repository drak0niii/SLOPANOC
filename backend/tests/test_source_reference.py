"""Tests for backend/api/source_reference.py -- the deterministic,
safe, structured Teams source/provenance builder (pre-4H UX/provenance
milestone).
"""
from __future__ import annotations

import pytest

import backend.api.source_reference as source_reference_module
from backend.api.source_reference import (
    MAX_EVIDENCE_ITEMS,
    TeamsSourceCapture,
    build_teams_source_reference,
    resolve_authoritative_contributors,
)
from backend.gateway import power_automate_client as pac_module
from backend.tests._api_fakes import FakeEvent, FakeFunctionResponse
from backend.tests._fakes import FakeResponse


def _evidence(count: int) -> list[dict]:
    return [{"message_id": f"m{i}", "author": f"User {i % 3}", "sent_at": f"2026-08-2{i}T09:00:00Z"} for i in range(count)]


def _texts_for(evidence: list[dict]) -> dict[str, str]:
    """The `message_texts_by_id` a real `teams_get_messages` call would
    have forwarded for every entry in `evidence` -- gives each one a
    distinct, valid, non-empty body so `_safe_snippet` succeeds."""
    return {entry["message_id"]: f"Original retrieved text for {entry['message_id']}." for entry in evidence}


# --- build_teams_source_reference -------------------------------------------


def test_builds_a_source_reference_from_evidence() -> None:
    source = build_teams_source_reference(_evidence(3), "Ops Bridge")
    assert source is not None
    assert source.source_type == "teams"
    assert source.title == "Ops Bridge"
    assert source.message_count == 3


def test_returns_none_for_empty_evidence() -> None:
    assert build_teams_source_reference([], "Ops Bridge") is None


def test_returns_none_for_no_valid_evidence_entries() -> None:
    assert build_teams_source_reference([{"message_id": "m1"}], "Ops Bridge") is None  # missing author/sent_at


def test_contributors_is_always_empty_from_the_pure_builder() -> None:
    """Contributor-accuracy fix: `contributors` is no longer derived from
    evidence authors at all -- a participant who wrote none of the
    (capped) evidence would otherwise be silently excluded. It is resolved
    separately and authoritatively; see
    test_resolve_authoritative_contributors below and
    test_chat_service_streaming.py's contributor tests for the full,
    wired-up behavior.
    """
    evidence = [
        {"message_id": "m1", "author": "Alex", "sent_at": "2026-08-26T09:00:00Z"},
        {"message_id": "m2", "author": "Priya", "sent_at": "2026-08-27T10:00:00Z"},
        {"message_id": "m3", "author": "Alex", "sent_at": "2026-08-28T11:00:00Z"},
    ]
    source = build_teams_source_reference(evidence, "Ops Bridge")
    assert source.contributors == []


def test_period_is_the_min_and_max_sent_at() -> None:
    evidence = [
        {"message_id": "m1", "author": "Alex", "sent_at": "2026-08-27T09:00:00Z"},
        {"message_id": "m2", "author": "Priya", "sent_at": "2026-08-25T10:00:00Z"},
        {"message_id": "m3", "author": "Alex", "sent_at": "2026-08-30T11:00:00Z"},
    ]
    source = build_teams_source_reference(evidence, "Ops Bridge")
    assert source.period_start == "2026-08-25T10:00:00Z"
    assert source.period_end == "2026-08-30T11:00:00Z"


def test_evidence_items_never_carry_the_raw_message_id() -> None:
    evidence = [
        {"message_id": "secret-id-alpha", "author": "Alex", "sent_at": "2026-08-20T09:00:00Z"},
        {"message_id": "secret-id-beta", "author": "Priya", "sent_at": "2026-08-21T09:00:00Z"},
    ]
    texts = {"secret-id-alpha": "First message body.", "secret-id-beta": "Second message body."}
    source = build_teams_source_reference(evidence, "Ops Bridge", texts)
    assert len(source.evidence) == 2  # sanity: items really are present
    dumped = source.model_dump(mode="json")
    assert "message_id" not in str(dumped)
    assert "secret-id-alpha" not in str(dumped) and "secret-id-beta" not in str(dumped)


def test_title_is_none_when_chat_title_is_absent() -> None:
    source = build_teams_source_reference(_evidence(1), None)
    assert source.title is None


def test_malformed_entries_are_skipped_not_fatal() -> None:
    evidence = [
        {"message_id": "m1", "author": "Alex", "sent_at": "2026-08-26T09:00:00Z"},
        "not a dict",
        {"message_id": "m2", "author": 42, "sent_at": "2026-08-27T09:00:00Z"},  # non-string author
        {"message_id": "m3"},  # missing fields
    ]
    source = build_teams_source_reference(evidence, "Ops Bridge")
    assert source.message_count == 1


def test_source_id_is_unique_per_call() -> None:
    a = build_teams_source_reference(_evidence(1), "Ops Bridge")
    b = build_teams_source_reference(_evidence(1), "Ops Bridge")
    assert a.source_id != b.source_id


def test_never_exposes_a_chat_id_or_payload_hash() -> None:
    evidence = _evidence(2)
    source = build_teams_source_reference(evidence, "Ops Bridge", _texts_for(evidence))
    dumped = str(source.model_dump(mode="json"))
    assert "chat_id" not in dumped
    assert "payload_hash" not in dumped


# --- Supporting evidence: snippet-authenticity fix + the 5-item cap --------


def test_snippet_comes_from_the_original_retrieved_message_text_not_the_model() -> None:
    """The whole point of the fix: even if a raw evidence dict carries a
    (now-nonexistent-in-the-schema, but defensively still-possible-in-a-
    malformed-payload) `snippet` key, it must be completely ignored --
    only `message_texts_by_id` is authoritative."""
    evidence = [
        {
            "message_id": "m1",
            "author": "Alex",
            "sent_at": "2026-08-26T09:00:00Z",
            "snippet": "MODEL-REPRODUCED TEXT, must never be used",
        }
    ]
    source = build_teams_source_reference(evidence, "Ops Bridge", {"m1": "The real retrieved message body."})
    assert source.evidence[0].snippet == "The real retrieved message body."
    assert "MODEL-REPRODUCED" not in source.evidence[0].snippet


def test_evidence_with_no_matching_text_is_skipped_entirely() -> None:
    evidence = _evidence(1)
    source = build_teams_source_reference(evidence, "Ops Bridge", {})  # no text forwarded for m0
    assert source is not None  # message_count/period still valid
    assert source.evidence == []


def test_evidence_snippet_collapses_internal_whitespace_and_newlines() -> None:
    source = build_teams_source_reference(
        [{"message_id": "m1", "author": "Alex", "sent_at": "2026-08-26T09:00:00Z"}],
        "Ops Bridge",
        {"m1": "Line one\n\n  Line   two"},
    )
    assert source.evidence[0].snippet == "Line one Line two"


def test_evidence_snippet_is_hard_capped_regardless_of_original_message_length() -> None:
    long_text = "x" * 500
    source = build_teams_source_reference(
        [{"message_id": "m1", "author": "Alex", "sent_at": "2026-08-26T09:00:00Z"}], "Ops Bridge", {"m1": long_text}
    )
    assert source.evidence[0].snippet is not None
    assert len(source.evidence[0].snippet) <= 140
    assert source.evidence[0].snippet.endswith("…")


def test_evidence_with_empty_or_whitespace_only_text_is_skipped() -> None:
    evidence = [
        {"message_id": "m1", "author": "Alex", "sent_at": "2026-08-26T09:00:00Z"},
        {"message_id": "m2", "author": "Priya", "sent_at": "2026-08-27T09:00:00Z"},
    ]
    source = build_teams_source_reference(evidence, "Ops Bridge", {"m1": "", "m2": "   \n\t  "})
    assert source.evidence == []
    assert source.message_count == 2  # full evidence set is still counted


def test_evidence_with_non_string_text_is_skipped() -> None:
    source = build_teams_source_reference(
        [{"message_id": "m1", "author": "Alex", "sent_at": "2026-08-26T09:00:00Z"}], "Ops Bridge", {"m1": 12345}
    )
    assert source.evidence == []


def test_evidence_is_capped_at_max_evidence_items() -> None:
    evidence = _evidence(29)
    source = build_teams_source_reference(evidence, "Ops Bridge", _texts_for(evidence))
    assert len(source.evidence) == MAX_EVIDENCE_ITEMS


def test_evidence_cap_never_shrinks_message_count_or_period() -> None:
    evidence = [
        {"message_id": f"m{i}", "author": f"User {i}", "sent_at": f"2026-08-{10 + i:02d}T09:00:00Z"}
        for i in range(9)
    ]
    source = build_teams_source_reference(evidence, "Ops Bridge", _texts_for(evidence))
    assert source.message_count == 9
    assert source.period_start == "2026-08-10T09:00:00Z"
    assert source.period_end == "2026-08-18T09:00:00Z"
    assert len(source.evidence) == MAX_EVIDENCE_ITEMS


def test_evidence_at_or_under_the_cap_is_not_truncated() -> None:
    evidence = _evidence(MAX_EVIDENCE_ITEMS)
    source = build_teams_source_reference(evidence, "Ops Bridge", _texts_for(evidence))
    assert len(source.evidence) == MAX_EVIDENCE_ITEMS


def test_builder_skips_invalid_candidates_and_keeps_scanning_past_the_first_five() -> None:
    """Exact scenario from the bug report: candidates #3 and #5 (1-indexed)
    have no usable text; the drawer must show the 5 VALID ones (#1, #2,
    #4, #6, #7), not the first five records with blank entries."""
    evidence = [
        {"message_id": f"m{i}", "author": f"User {i}", "sent_at": f"2026-08-{10 + i:02d}T09:00:00Z"}
        for i in range(1, 8)  # m1..m7
    ]
    texts = {f"m{i}": f"Message body {i}." for i in (1, 2, 4, 6, 7)}  # m3, m5 have none
    source = build_teams_source_reference(evidence, "Ops Bridge", texts)

    assert [item.snippet for item in source.evidence] == [
        "Message body 1.",
        "Message body 2.",
        "Message body 4.",
        "Message body 6.",
        "Message body 7.",
    ]
    assert len(source.evidence) == 5
    assert source.message_count == 7  # the full candidate set, unaffected


def test_fewer_than_five_are_returned_only_when_fewer_than_five_valid_examples_exist() -> None:
    evidence = [
        {"message_id": f"m{i}", "author": f"User {i}", "sent_at": f"2026-08-{10 + i:02d}T09:00:00Z"}
        for i in range(10)
    ]
    # Only 2 of the 10 retrieved/cited messages actually have usable text.
    texts = {"m0": "Body zero.", "m5": "Body five."}
    source = build_teams_source_reference(evidence, "Ops Bridge", texts)

    assert len(source.evidence) == 2
    assert source.message_count == 10  # full evidence set still counted


def test_every_returned_evidence_item_has_a_non_empty_snippet() -> None:
    evidence = [
        {"message_id": f"m{i}", "author": f"User {i}", "sent_at": f"2026-08-{10 + i:02d}T09:00:00Z"} for i in range(12)
    ]
    # A deliberately mixed bag: missing, empty, whitespace-only, and valid.
    texts = {
        "m1": "Valid body one.",
        "m2": "",
        "m3": "   ",
        "m4": "Valid body four.",
        "m6": "Valid body six.",
        "m8": "Valid body eight.",
        "m10": "Valid body ten.",
    }
    source = build_teams_source_reference(evidence, "Ops Bridge", texts)

    assert len(source.evidence) == 5
    for item in source.evidence:
        assert item.snippet
        assert item.snippet.strip() == item.snippet
        assert len(item.snippet) > 0


# --- TeamsSourceCapture.captured_chat_id (contributor-accuracy fix) --------


def test_capture_exposes_the_chat_id_backing_a_successful_response() -> None:
    capture = TeamsSourceCapture()
    capture.observe(
        FakeEvent(
            final=False,
            function_responses=[
                FakeFunctionResponse(
                    "incident_manager",
                    {"outcome": "ok", "chat_id": "c-real-1", "chat_title": "Ops Bridge", "evidence": _evidence(2)},
                )
            ],
        )
    )
    assert capture.captured_chat_id() == "c-real-1"


def test_capture_chat_id_is_none_when_nothing_was_captured() -> None:
    capture = TeamsSourceCapture()
    assert capture.captured_chat_id() is None


# --- resolve_authoritative_contributors (contributor-accuracy fix) --------


@pytest.mark.asyncio
async def test_resolve_authoritative_contributors_returns_empty_for_no_chat_id() -> None:
    assert await resolve_authoritative_contributors(None) == []
    assert await resolve_authoritative_contributors("") == []


@pytest.mark.asyncio
async def test_resolve_authoritative_contributors_returns_display_names_from_get_members(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_get_members(chat_id: str) -> dict:
        assert chat_id == "c1"
        return {
            "chat_id": chat_id,
            "members": [
                {"id": "19:member-1@thread.v2", "display_name": "Alex"},
                {"id": "19:member-2@thread.v2", "display_name": "Priya"},
                {"id": "19:member-3@thread.v2", "display_name": "Sam"},
            ],
        }

    monkeypatch.setattr(source_reference_module, "teams_get_members", fake_get_members)
    contributors = await resolve_authoritative_contributors("c1")
    assert contributors == ["Alex", "Priya", "Sam"]


@pytest.mark.asyncio
async def test_resolve_authoritative_contributors_includes_a_participant_who_sent_no_message(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The whole point of the fix: authoritative membership, not evidence
    authors -- a silent member must still appear."""

    def fake_get_members(chat_id: str) -> dict:
        return {"chat_id": chat_id, "members": [{"id": "m1", "display_name": "Silent Sam"}]}

    monkeypatch.setattr(source_reference_module, "teams_get_members", fake_get_members)
    contributors = await resolve_authoritative_contributors("c1")
    assert "Silent Sam" in contributors


@pytest.mark.asyncio
async def test_resolve_authoritative_contributors_never_exposes_a_member_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_get_members(chat_id: str) -> dict:
        return {"chat_id": chat_id, "members": [{"id": "19:super-secret-member-id@thread.v2", "display_name": "Alex"}]}

    monkeypatch.setattr(source_reference_module, "teams_get_members", fake_get_members)
    contributors = await resolve_authoritative_contributors("c1")
    assert contributors == ["Alex"]
    assert "19:super-secret-member-id@thread.v2" not in str(contributors)


@pytest.mark.asyncio
async def test_resolve_authoritative_contributors_deduplicates_order_preserving(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_get_members(chat_id: str) -> dict:
        return {
            "chat_id": chat_id,
            "members": [
                {"id": "m1", "display_name": "Alex"},
                {"id": "m2", "display_name": "Priya"},
                {"id": "m3", "display_name": "Alex"},
            ],
        }

    monkeypatch.setattr(source_reference_module, "teams_get_members", fake_get_members)
    assert await resolve_authoritative_contributors("c1") == ["Alex", "Priya"]


@pytest.mark.asyncio
async def test_resolve_authoritative_contributors_degrades_safely_on_gateway_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_get_members(chat_id: str) -> dict:
        return {"error": {"errorCode": "run_failure", "userMessage": "boom"}}

    monkeypatch.setattr(source_reference_module, "teams_get_members", fake_get_members)
    # Fails safe to an empty list -- never invents names, never raises.
    assert await resolve_authoritative_contributors("c1") == []


@pytest.mark.asyncio
async def test_resolve_authoritative_contributors_degrades_safely_on_malformed_response(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(source_reference_module, "teams_get_members", lambda chat_id: {"members": "not a list"})
    assert await resolve_authoritative_contributors("c1") == []


# --- End-to-end against the REAL gateway-normalized shape (bugfix pass) ----
# Exercises the FULL real pipeline (resolve_authoritative_contributors ->
# teams_get_members -> PowerAutomateClient.get_members -> requests.post),
# mocking only the HTTP layer -- never `teams_get_members` itself -- so
# this proves the actual parsing code against the documented
# docs/TEAMS_TOOL_CONTRACT.md #5 shape, not just a hand-shaped fixture at
# the wrong layer. Names below are synthetic placeholders, never real
# employee names.


def _member(member_id: str, display_name: str) -> dict:
    return {"id": member_id, "displayName": display_name}


@pytest.mark.asyncio
async def test_real_pipeline_all_members_are_extracted_from_the_normalized_gateway_shape(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        pac_module.requests,
        "post",
        lambda *a, **k: FakeResponse(200, [_member("m1", "Contributor One"), _member("m2", "Contributor Two")]),
    )

    contributors = await resolve_authoritative_contributors("c1")

    assert contributors == ["Contributor One", "Contributor Two"]


@pytest.mark.asyncio
async def test_real_pipeline_a_member_who_authored_no_message_still_appears(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # A full chat membership including someone who never sent a retrieved/
    # cited message -- the exact scenario the contributor-accuracy fix
    # targets, proven here against the real parsing path.
    monkeypatch.setattr(
        pac_module.requests,
        "post",
        lambda *a, **k: FakeResponse(
            200, [_member("m1", "Active Author"), _member("m2", "Silent Member")]
        ),
    )

    contributors = await resolve_authoritative_contributors("c1")

    assert "Silent Member" in contributors


@pytest.mark.asyncio
async def test_real_pipeline_duplicate_members_are_deduplicated(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        pac_module.requests,
        "post",
        lambda *a, **k: FakeResponse(
            200, [_member("m1", "Contributor One"), _member("m1-dup", "Contributor One")]
        ),
    )

    contributors = await resolve_authoritative_contributors("c1")

    assert contributors == ["Contributor One"]


@pytest.mark.asyncio
async def test_real_pipeline_empty_or_malformed_names_are_safely_ignored(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        pac_module.requests,
        "post",
        lambda *a, **k: FakeResponse(
            200,
            [
                _member("m1", "Contributor One"),
                {"id": "m2", "displayName": ""},
                {"id": "m3"},
                {"id": "m4", "displayName": 42},
            ],
        ),
    )

    contributors = await resolve_authoritative_contributors("c1")

    assert contributors == ["Contributor One"]


@pytest.mark.asyncio
async def test_real_pipeline_never_exposes_a_raw_member_id(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        pac_module.requests,
        "post",
        lambda *a, **k: FakeResponse(200, [_member("19:super-secret-member-id@thread.v2", "Contributor One")]),
    )

    contributors = await resolve_authoritative_contributors("c1")

    assert contributors == ["Contributor One"]
    assert "19:super-secret-member-id@thread.v2" not in str(contributors)


@pytest.mark.asyncio
async def test_real_pipeline_a_member_with_an_unrecognized_id_shape_still_contributes_a_name(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Bugfix: a member entry whose id field doesn't match this parser's
    expectations must not silently lose a real, valid display name."""
    monkeypatch.setattr(
        pac_module.requests,
        "post",
        lambda *a, **k: FakeResponse(200, [{"displayName": "No Recognizable Id Field"}]),
    )

    contributors = await resolve_authoritative_contributors("c1")

    assert contributors == ["No Recognizable Id Field"]


@pytest.mark.asyncio
async def test_real_pipeline_gateway_failure_degrades_to_no_contributors(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(pac_module.requests, "post", lambda *a, **k: FakeResponse(500))

    assert await resolve_authoritative_contributors("c1") == []


# --- TeamsSourceCapture ------------------------------------------------------


def test_capture_ignores_partial_events() -> None:
    capture = TeamsSourceCapture()
    capture.observe(
        FakeEvent(
            final=False,
            partial=True,
            function_responses=[FakeFunctionResponse("incident_manager", {"outcome": "ok", "evidence": _evidence(2)})],
        )
    )
    assert capture.build_source_reference() is None


def test_capture_ignores_non_incident_manager_responses() -> None:
    capture = TeamsSourceCapture()
    capture.observe(
        FakeEvent(
            final=False,
            function_responses=[FakeFunctionResponse("record_case_analysis", {"outcome": "ok", "evidence": _evidence(2)})],
        )
    )
    assert capture.build_source_reference() is None


def test_capture_ignores_non_ok_outcomes() -> None:
    capture = TeamsSourceCapture()
    for outcome in ("not_found", "selection_needed", "error", "proposed"):
        capture.observe(
            FakeEvent(
                final=False,
                function_responses=[FakeFunctionResponse("incident_manager", {"outcome": outcome, "evidence": _evidence(2)})],
            )
        )
    assert capture.build_source_reference() is None


def test_capture_ignores_ok_outcome_with_no_evidence() -> None:
    capture = TeamsSourceCapture()
    capture.observe(
        FakeEvent(
            final=False,
            function_responses=[FakeFunctionResponse("incident_manager", {"outcome": "ok", "evidence": []})],
        )
    )
    assert capture.build_source_reference() is None


def test_capture_builds_a_source_from_a_genuine_ok_response() -> None:
    capture = TeamsSourceCapture()
    capture.observe(
        FakeEvent(
            final=False,
            function_responses=[
                FakeFunctionResponse(
                    "incident_manager", {"outcome": "ok", "chat_title": "Ops Bridge", "evidence": _evidence(29)}
                )
            ],
        )
    )
    source = capture.build_source_reference()
    assert source is not None
    assert source.title == "Ops Bridge"
    assert source.message_count == 29


def test_capture_uses_the_last_successful_response_when_several_occur() -> None:
    capture = TeamsSourceCapture()
    capture.observe(
        FakeEvent(
            final=False,
            function_responses=[
                FakeFunctionResponse("incident_manager", {"outcome": "ok", "chat_title": "First Chat", "evidence": _evidence(2)})
            ],
        )
    )
    capture.observe(
        FakeEvent(
            final=False,
            function_responses=[
                FakeFunctionResponse("incident_manager", {"outcome": "ok", "chat_title": "Second Chat", "evidence": _evidence(5)})
            ],
        )
    )
    source = capture.build_source_reference()
    assert source.title == "Second Chat"
    assert source.message_count == 5
