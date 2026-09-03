"""P2 diagnostics-correctness regression tests: the model-call perf
instrumentation (perf_timing.py) must produce exactly one correlated
`model_call_start`/`model_call_end` pair per REAL model invocation, never
one per streamed callback fragment.

Root cause (see perf_timing.py's own "P2 ROOT CAUSE" comment): ADK's
`after_model_callback` fires once per streamed `LlmResponse` chunk, not
once per request -- the fix uses `LlmResponse.partial` (ADK's own,
verified terminal-chunk signal) to log exactly once, on the first
non-partial chunk, and silently ignore anything after.

Uses synthetic `LlmRequest`/`LlmResponse`-shaped fakes throughout -- no
real Gemini network needed (per instruction section 13).
"""
from __future__ import annotations

import logging
from typing import Any, Optional

import pytest

from backend.api.perf_timing import (
    after_model_call,
    before_model_call,
    discard_model_call_tracking,
)
from backend.api.turn_context import bind_run_id, reset_run_id


class _FakePart:
    def __init__(self, text: Any = None, function_call: Any = None) -> None:
        self.text = text
        self.function_call = function_call


class _FakeContent:
    def __init__(self, parts: list[Any]) -> None:
        self.parts = parts


class _FakeUsage:
    def __init__(self, prompt_tokens: Optional[int] = None, output_tokens: Optional[int] = None) -> None:
        self.prompt_token_count = prompt_tokens
        self.candidates_token_count = output_tokens


class _FakeLlmRequest:
    def __init__(self, contents: Optional[list[Any]] = None) -> None:
        self.contents = contents or []


class _FakeLlmResponse:
    def __init__(
        self,
        content: Any = None,
        usage_metadata: Any = None,
        partial: bool = False,
    ) -> None:
        self.content = content
        self.usage_metadata = usage_metadata
        self.partial = partial


def _model_call_log_lines(caplog: pytest.LogCaptureFixture, stage: str) -> list[str]:
    return [r.message for r in caplog.records if f"stage={stage}" in r.message]


def _field(line: str, name: str) -> str:
    for token in line.split():
        if token.startswith(f"{name}="):
            return token.split("=", 1)[1]
    raise AssertionError(f"field {name!r} not found in log line: {line!r}")


@pytest.fixture()
def _run(monkeypatch: pytest.MonkeyPatch):
    run_id = "p2-test-run"
    token = bind_run_id(run_id)
    yield run_id
    reset_run_id(token)
    discard_model_call_tracking(run_id)


# --- A. Non-streaming model response ----------------------------------------


def test_a_non_streaming_response_produces_exactly_one_pair(_run: str, caplog: pytest.LogCaptureFixture) -> None:
    before = before_model_call("team_manager")
    after = after_model_call("team_manager")

    with caplog.at_level(logging.INFO, logger="backend.perf"):
        before(None, _FakeLlmRequest(contents=[object()]))
        after(
            None,
            _FakeLlmResponse(
                content=_FakeContent([_FakePart(text="hello")]),
                usage_metadata=_FakeUsage(10, 5),
                partial=False,
            ),
        )

    starts = _model_call_log_lines(caplog, "model_call_start")
    ends = _model_call_log_lines(caplog, "model_call_end")
    assert len(starts) == 1
    assert len(ends) == 1
    assert _field(starts[0], "index") == _field(ends[0], "index")
    assert _field(ends[0], "index") != "-1"
    assert float(_field(ends[0], "duration_ms")) >= 0.0
    assert _field(ends[0], "kind") == "text"
    assert _field(ends[0], "prompt_tokens") == "10"
    assert _field(ends[0], "output_tokens") == "5"


# --- B. Streaming text response with progressive callbacks -----------------


def test_b_streaming_text_with_progressive_partials_yields_exactly_one_end(
    _run: str, caplog: pytest.LogCaptureFixture
) -> None:
    before = before_model_call("team_manager")
    after = after_model_call("team_manager")

    with caplog.at_level(logging.INFO, logger="backend.perf"):
        before(None, _FakeLlmRequest(contents=[]))
        # Several intermediate streaming fragments -- must never be logged.
        after(None, _FakeLlmResponse(content=_FakeContent([_FakePart(text="Hel")]), partial=True))
        after(None, _FakeLlmResponse(content=_FakeContent([_FakePart(text="lo ")]), partial=True))
        after(None, _FakeLlmResponse(content=_FakeContent([_FakePart(text="there")]), partial=True))
        # The one, real, aggregated terminal chunk.
        after(
            None,
            _FakeLlmResponse(
                content=_FakeContent([_FakePart(text="Hello there")]),
                usage_metadata=_FakeUsage(20, 8),
                partial=False,
            ),
        )

    assert len(_model_call_log_lines(caplog, "model_call_start")) == 1
    ends = _model_call_log_lines(caplog, "model_call_end")
    assert len(ends) == 1  # never one per streamed fragment
    assert _field(ends[0], "index") != "-1"
    assert _field(ends[0], "duration_ms") != "-1.0"
    assert _field(ends[0], "kind") == "text"
    assert _field(ends[0], "prompt_tokens") == "20"
    assert _field(ends[0], "output_tokens") == "8"


# --- C. Streaming function_call response ------------------------------------


def test_c_streaming_function_call_response_classified_correctly(
    _run: str, caplog: pytest.LogCaptureFixture
) -> None:
    before = before_model_call("team_manager")
    after = after_model_call("team_manager")

    with caplog.at_level(logging.INFO, logger="backend.perf"):
        before(None, _FakeLlmRequest(contents=[]))
        # Streaming function-call argument fragments (partial).
        after(None, _FakeLlmResponse(content=_FakeContent([_FakePart(function_call=object())]), partial=True))
        after(
            None,
            _FakeLlmResponse(
                content=_FakeContent([_FakePart(function_call=object())]),
                usage_metadata=_FakeUsage(15, 3),
                partial=False,
            ),
        )

    ends = _model_call_log_lines(caplog, "model_call_end")
    assert len(ends) == 1
    assert _field(ends[0], "kind") == "function_call"
    assert _field(ends[0], "index") != "-1"


# --- D. Nested agent model calls --------------------------------------------


def test_d_nested_agent_calls_share_one_incrementing_index_sequence(
    _run: str, caplog: pytest.LogCaptureFixture
) -> None:
    """team_manager index=1, index=2, then incident_manager index=3..5,
    then team_manager index=6 -- one chronological per-run sequence,
    never reset per agent (instruction section 12).
    """
    tm_before = before_model_call("team_manager")
    tm_after = after_model_call("team_manager")
    im_before = before_model_call("incident_manager")
    im_after = after_model_call("incident_manager")

    with caplog.at_level(logging.INFO, logger="backend.perf"):
        tm_before(None, _FakeLlmRequest(contents=[]))
        tm_after(None, _FakeLlmResponse(content=_FakeContent([_FakePart(function_call=object())]), partial=False))

        tm_before(None, _FakeLlmRequest(contents=[]))
        tm_after(None, _FakeLlmResponse(content=_FakeContent([_FakePart(function_call=object())]), partial=False))

        im_before(None, _FakeLlmRequest(contents=[]))
        im_after(None, _FakeLlmResponse(content=_FakeContent([_FakePart(function_call=object())]), partial=False))

        im_before(None, _FakeLlmRequest(contents=[]))
        im_after(None, _FakeLlmResponse(content=_FakeContent([_FakePart(function_call=object())]), partial=False))

        im_before(None, _FakeLlmRequest(contents=[]))
        im_after(None, _FakeLlmResponse(content=_FakeContent([_FakePart(text="summary")]), partial=False))

        tm_before(None, _FakeLlmRequest(contents=[]))
        tm_after(None, _FakeLlmResponse(content=_FakeContent([_FakePart(text="final answer")]), partial=False))

    starts = _model_call_log_lines(caplog, "model_call_start")
    ends = _model_call_log_lines(caplog, "model_call_end")
    assert len(starts) == 6
    assert len(ends) == 6

    agents = [_field(line, "agent") for line in ends]
    indexes = [int(_field(line, "index")) for line in ends]
    assert agents == [
        "team_manager",
        "team_manager",
        "incident_manager",
        "incident_manager",
        "incident_manager",
        "team_manager",
    ]
    assert indexes == [1, 2, 3, 4, 5, 6]  # one continuous per-run sequence


# --- E. Cancellation during a model invocation ------------------------------


def test_e_cancellation_before_terminal_callback_leaves_no_leaked_record(caplog: pytest.LogCaptureFixture) -> None:
    run_id = "p2-cancel-run"
    token = bind_run_id(run_id)
    before = before_model_call("team_manager")
    after = after_model_call("team_manager")

    with caplog.at_level(logging.INFO, logger="backend.perf"):
        before(None, _FakeLlmRequest(contents=[]))
        # One partial fragment arrives, then the run is cancelled --
        # the terminal chunk never arrives at all.
        after(None, _FakeLlmResponse(content=_FakeContent([_FakePart(text="partial output")]), partial=True))

    assert len(_model_call_log_lines(caplog, "model_call_start")) == 1
    assert len(_model_call_log_lines(caplog, "model_call_end")) == 0  # no premature/fake end

    # chat_service.py's own finally-block cleanup for a cancelled run.
    reset_run_id(token)
    discard_model_call_tracking(run_id)

    # A later, unrelated turn reusing the same process must never see
    # this leaked pending record or log a stray end for it.
    later_token = bind_run_id("p2-later-run")
    with caplog.at_level(logging.INFO, logger="backend.perf"):
        after(None, _FakeLlmResponse(content=_FakeContent([_FakePart(text="unrelated")]), partial=False))
    reset_run_id(later_token)

    assert len(_model_call_log_lines(caplog, "model_call_end")) == 0


# --- F. After-callback fired multiple times for the SAME invocation --------


def test_f_duplicate_terminal_callback_is_ignored_not_logged_again(
    _run: str, caplog: pytest.LogCaptureFixture
) -> None:
    before = before_model_call("team_manager")
    after = after_model_call("team_manager")

    with caplog.at_level(logging.INFO, logger="backend.perf"):
        before(None, _FakeLlmRequest(contents=[]))
        after(
            None,
            _FakeLlmResponse(
                content=_FakeContent([_FakePart(text="done")]), usage_metadata=_FakeUsage(7, 2), partial=False
            ),
        )
        # A genuine duplicate terminal callback for the SAME invocation
        # (e.g. an ADK edge case, or a defensive re-fire) -- must be
        # silently ignored, never a second log line, never index=-1.
        after(
            None,
            _FakeLlmResponse(content=_FakeContent([_FakePart(text="done again")]), partial=False),
        )
        after(None, _FakeLlmResponse(content=_FakeContent([_FakePart(text="done a third time")]), partial=False))

    ends = _model_call_log_lines(caplog, "model_call_end")
    assert len(ends) == 1
    assert _field(ends[0], "index") != "-1"
    assert _field(ends[0], "prompt_tokens") == "7"


# --- Concurrency: two runs, no cross-run pairing ----------------------------


def test_concurrent_runs_never_cross_pair_indexes_or_agents(caplog: pytest.LogCaptureFixture) -> None:
    before = before_model_call("team_manager")
    after = after_model_call("team_manager")

    run_a = "p2-concurrent-run-a"
    run_b = "p2-concurrent-run-b"
    token_a = bind_run_id(run_a)
    before(None, _FakeLlmRequest(contents=[]))  # run A's index=1, start pending
    reset_run_id(token_a)

    token_b = bind_run_id(run_b)
    before(None, _FakeLlmRequest(contents=[]))  # run B's OWN index=1, independent counter
    with caplog.at_level(logging.INFO, logger="backend.perf"):
        after(None, _FakeLlmResponse(content=_FakeContent([_FakePart(text="B done")]), partial=False))
    reset_run_id(token_b)

    ends = _model_call_log_lines(caplog, "model_call_end")
    assert len(ends) == 1
    assert _field(ends[0], "run_id") == run_b  # only B's invocation completed

    # Run A's own pending record is untouched by B's completion.
    token_a2 = bind_run_id(run_a)
    with caplog.at_level(logging.INFO, logger="backend.perf"):
        after(None, _FakeLlmResponse(content=_FakeContent([_FakePart(text="A done")]), partial=False))
    reset_run_id(token_a2)

    ends_after_a = _model_call_log_lines(caplog, "model_call_end")
    assert len(ends_after_a) == 2
    a_end = next(line for line in ends_after_a if _field(line, "run_id") == run_a)
    assert _field(a_end, "index") == "1"  # run A's own independent index sequence

    discard_model_call_tracking(run_a)
    discard_model_call_tracking(run_b)


def test_streaming_team_manager_plus_non_streaming_incident_manager_in_one_run(
    _run: str, caplog: pytest.LogCaptureFixture
) -> None:
    """Mixed shape within one turn: team_manager streams (several partial
    chunks), incident_manager (invoked as a plain, non-streaming nested
    call) returns a single terminal response directly.
    """
    tm_before = before_model_call("team_manager")
    tm_after = after_model_call("team_manager")
    im_before = before_model_call("incident_manager")
    im_after = after_model_call("incident_manager")

    with caplog.at_level(logging.INFO, logger="backend.perf"):
        tm_before(None, _FakeLlmRequest(contents=[]))
        tm_after(None, _FakeLlmResponse(content=_FakeContent([_FakePart(function_call=object())]), partial=True))
        tm_after(None, _FakeLlmResponse(content=_FakeContent([_FakePart(function_call=object())]), partial=False))

        im_before(None, _FakeLlmRequest(contents=[]))
        im_after(
            None,
            _FakeLlmResponse(content=_FakeContent([_FakePart(text="specialist result")]), partial=False),
        )

    ends = _model_call_log_lines(caplog, "model_call_end")
    assert len(ends) == 2
    assert [_field(line, "agent") for line in ends] == ["team_manager", "incident_manager"]
    assert [_field(line, "index") for line in ends] == ["1", "2"]
