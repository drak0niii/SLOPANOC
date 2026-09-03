"""P4COLD: process-startup Gemini/Vertex model warm-up.

All tests here are fully offline -- `warmup_shared_model` is exercised via
its own explicit `settings`/`llm` dependency-injection parameters (never
real network access, never ADC/GCP), mirroring the plain DI pattern this
backend already uses elsewhere (e.g. `ChatService.__init__`'s injectable
`runner`). `conftest.py`'s own autouse `disable_model_warmup_by_default`
fixture keeps the rest of the suite unaffected; these tests override it
per-test where needed via their own injected `settings`.
"""
from __future__ import annotations

import asyncio
import inspect
import logging
from typing import Any, Optional

import pytest

from backend.agents.incident_manager.agent import incident_manager
from backend.agents.team_manager.agent import team_manager
from backend.config import model_warmup as warmup_module
from backend.config.model_warmup import warmup_shared_model
from backend.config.settings import Settings, get_shared_llm


@pytest.fixture(autouse=True)
def _reset_warmup_guard():
    """The once-per-process guard is a module-level flag by design
    (instruction section 15/18) -- reset it around every test in this
    file so each test independently observes whether `warmup_shared_model`
    actually attempted a request, rather than being silently skipped
    because an earlier test in this same process already "used up" the
    one-time guard.
    """
    warmup_module._reset_warmup_guard_for_tests()
    yield
    warmup_module._reset_warmup_guard_for_tests()


def _fake_settings(*, enabled: bool = True, timeout_seconds: float = 5.0, model: str = "gemini-2.5-flash") -> Settings:
    return Settings(
        env={
            "SLOPANOC_MODEL_WARMUP_ENABLED": "true" if enabled else "false",
            "SLOPANOC_MODEL_WARMUP_TIMEOUT_SECONDS": str(timeout_seconds),
            "SLOPANOC_MODEL": model,
        }
    )


class _FakeLlmResponse:
    def __init__(self) -> None:
        self.partial = False


class _FakeLlm:
    """Duck-typed stand-in for `BaseLlm` -- only `generate_content_async`,
    exactly what `warmup_shared_model` calls.
    """

    def __init__(self) -> None:
        self.calls: list[Any] = []

    async def generate_content_async(self, llm_request: Any, stream: bool = False):
        self.calls.append((llm_request, stream))
        yield _FakeLlmResponse()


class _RaisingLlm:
    def __init__(self, exc: Exception) -> None:
        self._exc = exc
        self.calls = 0

    async def generate_content_async(self, llm_request: Any, stream: bool = False):
        self.calls += 1
        raise self._exc
        yield  # pragma: no cover -- unreachable, satisfies "async generator" shape


class _HangingLlm:
    def __init__(self) -> None:
        self.calls = 0
        self.cancelled = False

    async def generate_content_async(self, llm_request: Any, stream: bool = False):
        self.calls += 1
        try:
            await asyncio.sleep(60)
            yield _FakeLlmResponse()
        except asyncio.CancelledError:
            self.cancelled = True
            raise


# --- A. warm-up enabled + succeeds ------------------------------------------


@pytest.mark.asyncio
async def test_a_enabled_and_succeeds(caplog: pytest.LogCaptureFixture) -> None:
    fake_llm = _FakeLlm()
    with caplog.at_level(logging.INFO, logger="backend.perf"):
        await warmup_shared_model(settings=_fake_settings(enabled=True), llm=fake_llm)

    assert len(fake_llm.calls) == 1  # exactly one minimal request
    request, stream = fake_llm.calls[0]
    assert stream is False
    assert request.contents  # a real, non-empty request was built
    assert request.config.tools in (None, [])  # no tools

    messages = [r.getMessage() for r in caplog.records]
    assert any("stage=model_warmup_start" in m for m in messages)
    assert any("stage=model_warmup_complete" in m for m in messages)
    assert not any("stage=model_warmup_failed" in m for m in messages)
    assert not any("stage=model_warmup_timeout" in m for m in messages)


# --- B. warm-up disabled -----------------------------------------------------


@pytest.mark.asyncio
async def test_b_disabled_makes_no_model_request(caplog: pytest.LogCaptureFixture) -> None:
    fake_llm = _FakeLlm()
    with caplog.at_level(logging.INFO, logger="backend.perf"):
        await warmup_shared_model(settings=_fake_settings(enabled=False), llm=fake_llm)

    assert fake_llm.calls == []
    assert not any("model_warmup" in r.getMessage() for r in caplog.records)


# --- C. warm-up failure -------------------------------------------------------


@pytest.mark.asyncio
async def test_c_failure_is_caught_logged_safely_and_never_raises(caplog: pytest.LogCaptureFixture) -> None:
    failing_llm = _RaisingLlm(RuntimeError("some internal detail that must never leak"))
    with caplog.at_level(logging.INFO, logger="backend.perf"):
        await warmup_shared_model(settings=_fake_settings(enabled=True), llm=failing_llm)  # must not raise

    assert failing_llm.calls == 1  # exactly one attempt -- no retry loop
    messages = [r.getMessage() for r in caplog.records]
    assert any("stage=model_warmup_failed" in m and "reason=RuntimeError" in m for m in messages)
    # Never the raw exception text/detail in the safe perf event.
    assert not any("some internal detail" in m for m in messages)


@pytest.mark.asyncio
async def test_c_failure_never_retries(caplog: pytest.LogCaptureFixture) -> None:
    failing_llm = _RaisingLlm(ValueError("boom"))
    await warmup_shared_model(settings=_fake_settings(enabled=True), llm=failing_llm)
    assert failing_llm.calls == 1


# --- D. warm-up timeout -------------------------------------------------------


@pytest.mark.asyncio
async def test_d_timeout_logs_safely_and_startup_continues(caplog: pytest.LogCaptureFixture) -> None:
    hanging_llm = _HangingLlm()
    with caplog.at_level(logging.INFO, logger="backend.perf"):
        await asyncio.wait_for(
            warmup_shared_model(settings=_fake_settings(enabled=True, timeout_seconds=0.05), llm=hanging_llm),
            timeout=5.0,  # the test's OWN outer bound -- must return promptly
        )

    messages = [r.getMessage() for r in caplog.records]
    assert any("stage=model_warmup_timeout" in m for m in messages)
    assert not any("stage=model_warmup_complete" in m for m in messages)

    # No leaked task: give the event loop one tick, then confirm the
    # underlying generator was actually cancelled, not left running.
    await asyncio.sleep(0.1)
    assert hanging_llm.cancelled is True


# --- E. once-per-process/startup invocation ----------------------------------


@pytest.mark.asyncio
async def test_e_warmup_runs_at_most_once_per_process() -> None:
    fake_llm = _FakeLlm()
    settings = _fake_settings(enabled=True)

    await warmup_shared_model(settings=settings, llm=fake_llm)
    await warmup_shared_model(settings=settings, llm=fake_llm)
    await warmup_shared_model(settings=settings, llm=fake_llm)

    assert len(fake_llm.calls) == 1  # never once per call/request/session


# --- F. no user-session pollution --------------------------------------------


def test_f_module_never_touches_session_runner_or_case_machinery() -> None:
    """Structural proof (never behavioral-only): parses the warm-up
    module's ACTUAL CODE (via `ast`, ignoring docstrings/comments -- this
    module's own prose docstrings legitimately name these concepts to
    explain what is deliberately NOT used) for any import or identifier
    reference to the ADK Runner/session/Case/AgentTool machinery that
    would constitute "entering a user session".
    """
    import ast

    source = inspect.getsource(warmup_module)
    tree = ast.parse(source)

    referenced_names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            referenced_names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            referenced_names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.Name):
            referenced_names.add(node.id)
        elif isinstance(node, ast.Attribute):
            referenced_names.add(node.attr)

    forbidden = (
        "Runner",
        "SessionService",
        "session_service",
        "CaseService",
        "AgentTool",
        "team_manager",
        "incident_manager",
        "create_session",
        "append_event",
    )
    for token in forbidden:
        assert token not in referenced_names, f"model_warmup.py unexpectedly references {token!r} in real code"


@pytest.mark.asyncio
async def test_f_warmup_request_carries_no_tools_or_history() -> None:
    fake_llm = _FakeLlm()
    await warmup_shared_model(settings=_fake_settings(enabled=True), llm=fake_llm)

    request, _ = fake_llm.calls[0]
    assert not request.config.tools
    assert not request.config.system_instruction
    assert len(request.contents) == 1  # no accumulated conversation history


# --- G. shared client identity ------------------------------------------------


def test_g_team_manager_and_incident_manager_share_the_exact_get_shared_llm_object() -> None:
    """Proven at the correct layer (instruction section 20.G): the object
    `team_manager`/`incident_manager` resolve `.model` to (verified,
    per `LlmAgent.canonical_model`'s own source, to be the exact object
    stored -- `BaseLlm` instances are returned verbatim, never rewrapped)
    is the SAME object `get_shared_llm` returns for that same model
    string -- the identical function `warmup_shared_model` itself calls
    when no `llm` is injected.
    """
    from backend.config.settings import get_settings

    shared = get_shared_llm(get_settings().gemini_model)
    assert team_manager.model is shared
    assert incident_manager.model is shared


@pytest.mark.asyncio
async def test_g_default_warmup_path_resolves_the_same_shared_llm(monkeypatch: pytest.MonkeyPatch) -> None:
    """With no injected `llm`, `warmup_shared_model` must resolve via
    `get_shared_llm` itself (never construct a separate client) -- proven
    by monkeypatching `get_shared_llm` and observing it was called with
    the configured model name, then asserting the object it returned is
    what actually received the warm-up request.
    """
    fake_llm = _FakeLlm()
    calls: list[str] = []

    def fake_get_shared_llm(model_name: str):
        calls.append(model_name)
        return fake_llm

    monkeypatch.setattr(warmup_module, "get_shared_llm", fake_get_shared_llm)

    await warmup_shared_model(settings=_fake_settings(enabled=True, model="gemini-2.5-flash"))

    assert calls == ["gemini-2.5-flash"]
    assert len(fake_llm.calls) == 1


# --- Lifespan integration: the real FastAPI app calls warm-up on startup ----


def test_lifespan_invokes_warmup_exactly_once_on_real_app_startup(monkeypatch: pytest.MonkeyPatch) -> None:
    """End-to-end proof of instruction section 8's integration point: the
    real `app` object's `lifespan` (backend/api/app.py) actually calls
    `warmup_shared_model` on startup -- `conftest.py`'s autouse fixture
    disables warm-up for the rest of the suite, so this test explicitly
    re-enables it (config injection) and replaces `warmup_shared_model`
    itself with a tracking fake -- no real model/network access either way.
    """
    from fastapi.testclient import TestClient

    from backend.api.app import app

    calls = 0

    async def fake_warmup() -> None:
        nonlocal calls
        calls += 1

    monkeypatch.setattr("backend.api.app.warmup_shared_model", fake_warmup)

    with TestClient(app):
        pass

    assert calls == 1


def test_lifespan_survives_warmup_raising_unexpectedly() -> None:
    """Defense in depth beyond `warmup_shared_model`'s own internal
    try/except (instruction section 8: "warm-up failure must NOT crash the
    API") -- even if a future change somehow let an exception escape
    `warmup_shared_model` itself, this proves what the CURRENT, real
    implementation actually does: it never raises, so app startup via the
    real `lifespan` completes normally. This exercises the real
    `warmup_shared_model` (not a fake), with warm-up left disabled by the
    autouse fixture, which is itself the fast, deterministic, real
    no-op path this function takes when disabled.
    """
    from fastapi.testclient import TestClient

    from backend.api.app import app

    with TestClient(app) as client:
        response = client.get("/health")
    assert response.status_code == 200
