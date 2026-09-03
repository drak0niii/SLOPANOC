"""The single Power Automate HTTP client abstraction.

Per docs/AGENT_CONTRACT.md #13/#16 and docs/TEAMS_TOOL_CONTRACT.md #1, this
is the ONLY component in the backend that constructs an HTTP request to the
Power Automate gateway. No Teams tool builds its own client. No Microsoft
Graph call is made anywhere in this stack -- every Teams operation is a
call to this gateway.

The gateway URL is a secret (it typically embeds a SAS signature). It is
resolved from backend configuration (config/settings.py) inside `_call`,
on every request -- never cached on `self`, never logged, and never
included in any exception message or return value this module produces.
Upstream `requests` exceptions are deliberately not stringified into a
SafeError message, because their default messages frequently echo the
request URL.

RESPONSE SHAPE: the live gateway's proven behavior for `teams.listChats` /
`teams.getMessages` is a bare top-level JSON array on success (not a
`{"chats": [...]}`-style envelope, which was an earlier, unverified
assumption). `_call` therefore accepts either a JSON array or a JSON
object as a structurally valid response and returns it unchanged; per-
operation interpretation of *which* shape it is (raw array vs. a wrapped
envelope) is done by `extract_items` below, used by the Teams tools in
backend/tools/teams/.
"""
from __future__ import annotations

import logging
import time
import uuid
from typing import Any, Optional, Union

import requests

from ..config.settings import ConfigurationError, Settings, get_settings
from .safe_error import (
    internal_error,
    rate_limited,
    run_failure,
    validation_error,
)

_API_VERSION = "1.0"

GatewayPayload = Union[list, dict]

# Performance pass (pre-4H latency investigation) -- see
# backend/api/perf_timing.py's module docstring for the safety contract
# this shares: only `operation` (a fixed, non-secret string already known
# statically, e.g. "teams.getMessages"), `duration_ms`, and a coarse
# `outcome` are ever logged here -- never the gateway URL, never a
# chat_id/payload, never a raw response body.
_perf_logger = logging.getLogger("backend.perf")

# LATENCY PASS -- INVESTIGATED, DELIBERATELY NOT CHANGED: `requests.post(
# ...)` (the bare module-level call used below) constructs a brand-new,
# connectionless `Session` internally on every single call (`requests.api
# .post` -> `request()` -> `with sessions.Session() as session: return
# session.request(...)`), so no TCP/TLS connection is reused across
# back-to-back Teams operations (e.g. listChats then getMessages then
# getMembers in one turn). A shared, process-lifetime `requests.Session`
# would fix that -- but this entire test suite's own established,
# documented contract (`backend/tests/conftest.py`'s own module
# docstring: "`requests.post` is monkeypatched per-test") monkeypatches
# `pac_module.requests.post` directly, across 20+ test files; switching
# this call site to a `Session` instance method would silently stop
# being intercepted by every one of those tests (a `Session`'s `.post`
# is a different code path from the module-level function a patched
# `requests.post` replaces), attempting real network calls instead.
# TCP/TLS handshake reuse saves on the order of tens of milliseconds per
# call on a healthy network -- not the tens-of-seconds-to-minutes scale
# of the latencies reported for this pass. Given that, and the
# instruction's own "do not change the HTTP stack unnecessarily if
# measurements show it is insignificant" -- deferred pending a live
# measurement (see this pass's final report) that actually shows PA
# connection setup, not something else, dominates a Teams-heavy turn's
# time. Revisit via `power_automate_gateway` perf lines (this module's
# own `_log_gateway_duration`) once real numbers are available.


class PowerAutomateClient:
    """Deterministic client for the Teams Power Automate gateway.

    Every method sends `{"version", "requestId", "operation", ...}` and
    returns the parsed JSON response body on success, or raises a
    `SafeErrorException` (see gateway/safe_error.py) on any failure.
    """

    def __init__(self, settings: Optional[Settings] = None) -> None:
        self._settings = settings or get_settings()

    def list_chats(self) -> GatewayPayload:
        """Call `teams.listChats`. Takes no filter parameters -- the
        gateway returns whatever single page of chats it has; see the
        pagination limitation documented in
        backend/tools/teams/list_chats.py.
        """
        return self._call("teams.listChats")

    def get_messages(
        self, chat_id: str, before: Optional[str] = None
    ) -> GatewayPayload:
        """Call `teams.getMessages` for one chat id.

        `before` is an optional ISO-8601 timestamp cursor. The live
        gateway flow is configured with Top=50 / OrderBy=createdDateTime
        desc / paging toggle off; passing `before` applies
        `createdDateTime lt <before>` server-side and returns the next
        older page (proven live). The `before` field is only included in
        the request body when not `None` -- omitting it entirely (rather
        than sending `"before": null`) is what gets the newest page.
        Deterministic multi-page retrieval built on top of this
        single-page call lives in backend/tools/teams/get_messages.py.
        """
        if not chat_id or not chat_id.strip():
            raise validation_error(
                "A Teams chat id is required to retrieve messages."
            )
        payload: dict[str, Any] = {"chatId": chat_id}
        if before is not None:
            payload["before"] = before
        return self._call("teams.getMessages", payload)

    def get_members(self, chat_id: str) -> GatewayPayload:
        """Call `teams.getMembers` for one chat id (docs/TEAMS_TOOL_CONTRACT.md
        #5) -- read-only, no confirmation required. Deterministic
        retrieval built on top of this single call lives in
        backend/tools/teams/get_members.py.
        """
        if not chat_id or not chat_id.strip():
            raise validation_error(
                "A Teams chat id is required to retrieve members."
            )
        return self._call("teams.getMembers", {"chatId": chat_id})

    def create_chat(self, title: str, members: list[str]) -> GatewayPayload:
        """Call `teams.createChat`.

        `title`/`members` are sent exactly as given -- this client performs
        no validation or normalization of its own (that is
        backend/tools/teams/write_validation.py's job, applied before a
        payload is ever hashed into an `ActionProposal` or reaches this
        method; see docs on the approval framework's exact-payload-binding
        requirement). The connection owner is added by the Power Automate
        flow itself; `members` here is exactly the other participants list,
        never including the owner (per instruction).
        """
        return self._call("teams.createChat", {"title": title, "members": members})

    def send_message(self, chat_id: str, message: str) -> GatewayPayload:
        """Call `teams.sendMessage`. See `create_chat`'s docstring on why
        this performs no validation/normalization itself.
        """
        return self._call("teams.sendMessage", {"chatId": chat_id, "message": message})

    def _call(
        self, operation: str, extra_payload: Optional[dict[str, Any]] = None
    ) -> GatewayPayload:
        try:
            gateway_url = self._settings.resolve_power_automate_gateway_url()
        except ConfigurationError:
            # A deployment/setup problem, not a per-call runtime failure --
            # still routed through SafeError so it can never surface a raw
            # exception to an agent or the frontend.
            raise internal_error(
                "The Teams connector is not configured on this backend."
            ) from None

        body: dict[str, Any] = {
            "version": _API_VERSION,
            "requestId": str(uuid.uuid4()),
            "operation": operation,
        }
        if extra_payload:
            body.update(extra_payload)

        request_started_at = time.monotonic()
        try:
            response = requests.post(
                gateway_url,
                json=body,
                timeout=self._settings.request_timeout_seconds,
            )
        except requests.exceptions.Timeout:
            self._log_gateway_duration(operation, request_started_at, "timeout")
            raise run_failure(
                "The Teams connector timed out. Please try again."
            ) from None
        except requests.exceptions.RequestException:
            # Deliberately not str(exc): requests' own exception messages
            # frequently echo the request URL, which would leak the
            # gateway secret. See the module docstring.
            self._log_gateway_duration(operation, request_started_at, "network_error")
            raise run_failure(
                "The Teams connector could not be reached. Please try again."
            ) from None

        if response.status_code == 429:
            self._log_gateway_duration(operation, request_started_at, "rate_limited")
            raise rate_limited()
        if response.status_code >= 400:
            self._log_gateway_duration(operation, request_started_at, f"http_{response.status_code}")
            raise run_failure(
                "The Teams connector could not complete this request."
            )
        self._log_gateway_duration(operation, request_started_at, "ok")

        try:
            payload = response.json()
        except ValueError:
            raise internal_error(
                "The Teams connector returned an unreadable response."
            ) from None

        if not isinstance(payload, (list, dict)):
            raise internal_error(
                "The Teams connector returned an unexpected response shape."
            )

        return payload

    @staticmethod
    def _log_gateway_duration(operation: str, started_at: float, outcome: str) -> None:
        """Developer-only diagnostic (pre-4H latency investigation) --
        isolates actual network/gateway round-trip time (the `requests.post`
        call only) from URL resolution and JSON parsing, so a slow Teams
        operation can be attributed to the gateway itself rather than to
        this backend's own code. Never affects the return value/exception
        raised -- purely a log side effect at every exit point of the
        `requests.post` call above.
        """
        duration_ms = (time.monotonic() - started_at) * 1000.0
        _perf_logger.info(
            "perf stage=power_automate_gateway operation=%s outcome=%s duration_ms=%.1f",
            operation,
            outcome,
            duration_ms,
        )


def extract_items(payload: GatewayPayload, wrapper_key: str) -> list[Any]:
    """Normalize a gateway response (as returned by `PowerAutomateClient`)
    into a plain list of raw item dicts, regardless of which shape the
    gateway used for this call. Accepted shapes, in order:

      1. A bare top-level JSON array -- the current, proven shape for
         `teams.listChats` / `teams.getMessages`.
      2. `{"<wrapper_key>": [...]}`, e.g. `{"chats": [...]}` -- a
         previously-supported wrapped shape, preserved here for forward
         compatibility even though the live gateway does not use it today.
      3. `{"success": bool, "data": [...]}` -- a generic envelope some
         Power Automate flows use; preserved for forward compatibility.
         `"success": false` is treated as a run failure, not a shape
         error.

    Raises `SafeErrorException` (`internal_error`) for any other shape.
    Does not validate individual items -- that remains each tool's own
    responsibility (docs/TEAMS_TOOL_CONTRACT.md #4-#7's "do not weaken
    validation of individual items").
    """
    if isinstance(payload, list):
        return payload

    if isinstance(payload, dict):
        if "success" in payload:
            if not payload["success"]:
                raise run_failure(
                    "The Teams connector reported that the request did "
                    "not succeed."
                )
            data = payload.get("data")
            if isinstance(data, list):
                return data
            raise internal_error(
                "The Teams connector returned an unexpected response shape."
            )

        wrapped = payload.get(wrapper_key)
        if isinstance(wrapped, list):
            return wrapped

    raise internal_error(
        "The Teams connector returned an unexpected response shape."
    )
