"""P4COLD: process-startup Gemini/Vertex model warm-up.

THE PROBLEM, VERIFIED FROM LIVE MEASUREMENT (not guessed): the first real
Team Manager request after backend startup pays a large, mostly-fixed
latency penalty (~5-22s observed) that a second/third request immediately
after does NOT -- while prompt token counts stay essentially constant
across all three. This isolates the cost to model/transport/auth
initialization, not prompt size or reasoning complexity.

ROOT CAUSE, VERIFIED AGAINST THE INSTALLED google-genai (1.75.0) SOURCE
before implementing (per instruction -- do not guess):

  - `backend.config.settings.get_shared_llm(model_name)` returns a
    process-lifetime-cached `google.adk.models.BaseLlm` (concretely
    `Gemini`, for this app's configured model) -- already a LATENCY-PASS
    fix from an earlier round, so `Gemini.__init__` itself performs no
    network/credential I/O (verified: it only assigns pydantic fields).
  - `Gemini.api_client` (`google/adk/models/google_llm.py`) is a
    `functools.cached_property` -- NOT built at `Gemini.__init__` time,
    only on first ACCESS. It is first accessed inside `generate_content_
    async`'s own body (`self.api_client.aio.models.generate_content(...)`)
    -- so simply calling `get_shared_llm(...)` and stopping does nothing;
    the actual client has to be exercised by a real request.
  - `Gemini.api_client` constructs a `google.genai.Client(...)`, whose
    `__init__` (`google/genai/client.py` -> `BaseApiClient.__init__`,
    `google/genai/_api_client.py`) synchronously resolves configuration
    (env vars, base URL) and, for the Vertex AI path with no explicit
    project/credentials given (this app's own configuration shape),
    calls `load_auth(project=None)` -- Application Default Credentials
    resolution. This is real, but comparatively cheap, LOCAL work (reading
    ADC state) -- it does NOT itself perform the token-minting HTTP
    round trip or open the HTTPS connection to the Vertex endpoint.
  - The actual expensive, network-bound work -- OAuth token
    exchange/refresh, TLS/HTTP2 connection establishment to the Vertex
    endpoint, and the real model inference round trip -- happens on the
    FIRST awaited `self.api_client.aio.models.generate_content(...)`
    call, inside `Gemini.generate_content_async`. This is exactly why the
    live evidence shows request #1 far slower than #2/#3 despite near-
    identical prompt sizes: only request #1 ever had to pay for building
    `api_client` (including ADC resolution) AND the first authenticated
    connection/token round trip.

THE FIX: issue exactly one minimal, direct request through the SAME
process-lifetime-shared `BaseLlm` instance `team_manager`/`incident_
manager` already use, at process startup (FastAPI `lifespan`, see
`backend/api/app.py`), before real user traffic arrives -- paying the
above cold cost once, upfront, off the user-facing path, so the object's
`api_client` (and its underlying auth/transport state) is already built
by the time the first real user request reaches it.

WHY `BaseLlm.generate_content_async` (never a new `google.genai.Client`,
never a throwaway `Agent`/`Runner`): `BaseLlm.generate_content_async` is
ADK's own PUBLIC, abstract API for issuing a single-turn model request
(`google/adk/models/base_llm.py`) -- the exact method `Gemini` (and every
other `BaseLlm` subclass) implements and the exact method ADK's own flow
code calls for every real agent turn. Calling it directly on the SAME
object `get_shared_llm(model_name)` returns is what makes the "same shared
client" invariant hold: `team_manager`/`incident_manager` both construct
their `Agent(model=get_shared_llm(_settings.gemini_model), ...)` with that
identical cached object (see agent.py's own "Latency pass" comment), and
`LlmAgent.canonical_model` returns a `BaseLlm` instance verbatim
(`if isinstance(self.model, BaseLlm): return self.model`) rather than
rewrapping it -- so whichever `Gemini` instance this module warms is
LITERALLY the same instance later agent calls resolve to and read
`.api_client` from, never a copy.

WHY `stream=False`: `BaseLlm.generate_content_async`'s own docstring
documents non-streaming mode as yielding EXACTLY ONE `LlmResponse`
containing the complete output -- the minimal, simplest-to-fully-consume
shape for a warm-up whose only purpose is exercising the transport/auth
path once, with no need for progressive/aggregated-chunk handling.

NEVER CREATES AN ADK SESSION/RUNNER/AGENT: this module never imports or
touches `Runner`, `SessionService`, `team_manager`, `incident_manager`, or
any `AgentTool` -- only `get_shared_llm` (config/settings.py) and ADK's
own `LlmRequest`/`google.genai.types`. There is therefore no code path by
which warm-up could reach a Teams tool, Power Automate, or persist
anything to session storage.

TEST INJECTABILITY WITHOUT SPECIAL-CASING PYTEST: `warmup_shared_model`
accepts optional `settings`/`llm` parameters (plain dependency injection,
the same pattern already used throughout this backend -- e.g. `ChatService
.__init__`'s injectable `runner`) so tests can supply a fake `Settings`/
fake `BaseLlm`-shaped object and assert against it, with zero real network
access and no test-name detection in production code.
"""
from __future__ import annotations

import asyncio
import logging
import time
from typing import Any, Optional

from backend.config.settings import Settings, get_settings, get_shared_llm

_logger = logging.getLogger("backend.perf")

_WARMUP_PROMPT_TEXT = "Reply OK."
_WARMUP_MAX_OUTPUT_TOKENS = 8

# P4COLD process-lifetime guard (instruction section 15/18): warm-up must
# run at most once per process, and never issue more than one real Vertex
# request. FastAPI's own ASGI lifespan protocol already only ever enters
# the startup phase once per app instance, so no `asyncio.Lock` is needed
# here (the instruction's own "do not add unnecessary locking" guidance) --
# this flag is a cheap, synchronous-before-any-`await` defense against a
# stray SECOND call in the same process (e.g. a future second lifespan
# wiring, or a test importing this module more than once), not a
# concurrency primitive for genuinely simultaneous callers.
_warmup_attempted = False


def _reset_warmup_guard_for_tests() -> None:
    """Test-only helper -- resets the once-per-process guard so each test
    can independently verify `warmup_shared_model` was invoked. Never
    called from production code (see this module's own docstring on
    dependency injection over test-name detection -- tests call this
    explicitly, production code has no reason to).
    """
    global _warmup_attempted
    _warmup_attempted = False


async def _run_warmup_request(llm: Any, model_name: str) -> None:
    """Builds and fully sends the minimal request, consuming the async
    generator through its terminal response (instruction section 7 -- a
    warm-up that only constructs the generator without iterating it would
    never actually reach the network).
    """
    from google.adk.models.llm_request import LlmRequest
    from google.genai import types

    request = LlmRequest(
        model=model_name,
        contents=[types.Content(role="user", parts=[types.Part.from_text(text=_WARMUP_PROMPT_TEXT)])],
        config=types.GenerateContentConfig(max_output_tokens=_WARMUP_MAX_OUTPUT_TOKENS),
    )
    async for _ in llm.generate_content_async(request, stream=False):
        pass  # No tools, no history, no system instruction, no output schema.


async def warmup_shared_model(
    *, settings: Optional[Settings] = None, llm: Optional[Any] = None
) -> None:
    """Best-effort, process-startup warm-up of the shared model client --
    see this module's own docstring for the full root-cause/design
    rationale. NEVER raises: any failure (including a timeout) is caught,
    logged as a safe diagnostic, and startup continues normally -- the
    first real user request simply pays the cold cost itself, exactly as
    it did before this pass existed (instruction section 8/16: failure
    here must never poison the shared client or block startup).

    `settings`/`llm` are optional, plain dependency-injection points for
    tests -- production code (app.py's lifespan) calls this with no
    arguments, resolving both from the real, process-wide singletons.
    """
    global _warmup_attempted
    if _warmup_attempted:
        return
    _warmup_attempted = True

    resolved_settings = settings if settings is not None else get_settings()
    if not resolved_settings.model_warmup_enabled:
        return

    _logger.info("perf stage=model_warmup_start")
    start = time.monotonic()
    try:
        resolved_llm = llm if llm is not None else get_shared_llm(resolved_settings.gemini_model)
        await asyncio.wait_for(
            _run_warmup_request(resolved_llm, resolved_settings.gemini_model),
            timeout=resolved_settings.model_warmup_timeout_seconds,
        )
    except asyncio.TimeoutError:
        duration_ms = (time.monotonic() - start) * 1000.0
        _logger.warning("perf stage=model_warmup_timeout duration_ms=%.1f", duration_ms)
        return
    except Exception as exc:
        duration_ms = (time.monotonic() - start) * 1000.0
        # Safe category only (the exception CLASS name) -- never str(exc),
        # which could embed request/response detail. A full traceback is
        # still available at DEBUG for local diagnosis, consistent with
        # this project's existing logging standards elsewhere.
        _logger.warning(
            "perf stage=model_warmup_failed duration_ms=%.1f reason=%s", duration_ms, type(exc).__name__
        )
        _logger.debug("model warm-up request failed", exc_info=True)
        return

    duration_ms = (time.monotonic() - start) * 1000.0
    _logger.info("perf stage=model_warmup_complete duration_ms=%.1f", duration_ms)
