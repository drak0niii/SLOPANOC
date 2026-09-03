"""Shared pytest fixtures for the SLOPANOC backend test suite.

All tests here are fully offline: the real Power Automate gateway is never
called. `requests.post` is monkeypatched per-test. See
backend/tests/manual/ for the separate, non-collected live-gateway check
that the automated suite never runs.
"""
from __future__ import annotations

import pytest

FAKE_GATEWAY_URL = (
    "https://prod-00.eastus.logic.azure.com:443/workflows/FAKE/triggers/manual/"
    "paths/invoke?api-version=2016-10-01&sp=%2Ftriggers%2Fmanual%2Frun"
    "&sv=1.0&sig=THIS_IS_A_FAKE_TEST_SECRET_VALUE_1234567890"
)
"""A realistic-shaped but entirely fake Power Automate URL, including a
fake `sig=` value. Tests assert this exact string -- and the fake secret
specifically -- never appears in any tool/agent-facing output."""


@pytest.fixture(autouse=True)
def power_automate_gateway_url(monkeypatch: pytest.MonkeyPatch) -> str:
    """Every test gets a configured (fake) gateway URL by default, so a
    missing-configuration `internal_error` never accidentally masks the
    behavior a test is actually checking. Tests that specifically want to
    exercise missing configuration delete the env var themselves.
    """
    monkeypatch.setenv("SLOPANOC_POWER_AUTOMATE_GATEWAY_URL", FAKE_GATEWAY_URL)
    monkeypatch.delenv("SLOPANOC_POWER_AUTOMATE_SECRET_RESOURCE", raising=False)
    return FAKE_GATEWAY_URL


@pytest.fixture(autouse=True)
def disable_model_warmup_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    """P4COLD: plain config injection (`SLOPANOC_MODEL_WARMUP_ENABLED`),
    never production code detecting pytest by name (instruction section
    11) -- keeps the ENTIRE automated suite offline/deterministic even for
    a test that happens to instantiate the real FastAPI app (whose
    `lifespan` would otherwise attempt a real warm-up on entry). Tests
    that specifically exercise warm-up behavior (test_model_warmup.py)
    override this via their own explicit `monkeypatch.setenv`/injected
    `settings`/`llm`, exactly like `power_automate_gateway_url` above is
    itself sometimes overridden by tests that need the opposite.
    """
    monkeypatch.setenv("SLOPANOC_MODEL_WARMUP_ENABLED", "false")


@pytest.fixture(autouse=True)
def clear_direct_fast_path_registries() -> None:
    """P4B.3 direct-unique fast path (direct_read_fast_path.py) keeps two
    small, run-id-keyed, module-level dicts/sets (`_pending_trusted_
    result_by_run`, `_trust_validation_failed_runs`) as the bridge between
    `incident_manager`'s nested AgentTool turn and team_manager's own next
    turn -- see that module's own docstring for why (a `ContextVar` was
    tried first and found to leak across the `asyncio.create_task`
    boundary ADK wraps every tool call in).

    Found, by direct reproduction, that a unit test exercising just the
    WRITE side (`_fast_path_before_model_callback`) in isolation -- never
    also driving the READ side (`_present_fast_path_result_via_trusted_
    pipeline`) that would normally `.pop()` the entry -- leaves it behind
    in the shared module-level dict for the rest of the process, silently
    failing a LATER, unrelated test's own "no cross-run leakage" assertion
    (test_p4b3_source_provenance.py's `test_run_b_does_not_inherit_run_a_
    source`/`test_cancellation_leaves_no_leaked_provenance_state`, several
    hundred tests later in collection order). Clearing both before AND
    after every test -- same "cheap, global, autouse" pattern as the two
    fixtures above -- makes every test in this suite immune to this class
    of cross-test leakage, not just the ones that happened to already
    trip over it.
    """
    from backend.agents.team_manager.direct_read_fast_path import (
        _pending_trusted_result_by_run,
        _trust_validation_failed_runs,
    )

    _pending_trusted_result_by_run.clear()
    _trust_validation_failed_runs.clear()
    yield
    _pending_trusted_result_by_run.clear()
    _trust_validation_failed_runs.clear()
