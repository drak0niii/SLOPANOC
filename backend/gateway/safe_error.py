"""The SafeError contract.

Mirrors docs/implementation-handoff/02_INTERFACE_CONTRACTS.md #23 and the
retryability table locked in
docs/implementation-handoff/05_STAGE0_ARCHITECTURE_DECISIONS.md #9. Every
failure that could reach an agent (and, eventually, the frontend) must be
translated into this shape -- never a raw exception message, which could
contain the Power Automate gateway URL or other sensitive detail. See
docs/AGENT_CONTRACT.md #13 and docs/TEAMS_TOOL_CONTRACT.md #8.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Literal, Optional

ErrorCode = Literal[
    "validation_error",
    "authentication_error",
    "authorization_error",
    "connector_unavailable",
    "knowledge_insufficient",
    "run_failure",
    "upload_failure",
    "action_failure",
    "not_found",
    "rate_limited",
    "payload_too_large",
    "unsupported_media_type",
    "internal_error",
]

_RETRYABLE: dict[ErrorCode, bool] = {
    "validation_error": False,
    "authentication_error": False,
    "authorization_error": False,
    "connector_unavailable": True,
    "knowledge_insufficient": False,
    "run_failure": True,
    "upload_failure": True,
    "action_failure": False,
    "not_found": False,
    "rate_limited": True,
    # Neither is retryable as-is: the exact same file will always be
    # too large / the wrong format again -- the client must change what
    # it sends (a different/smaller file), not merely retry.
    "payload_too_large": False,
    "unsupported_media_type": False,
    "internal_error": True,
}


@dataclass(frozen=True)
class SafeError:
    """A calm, pre-written, never-raw-exception-text error payload.

    `retryable` is derived from `error_code` (the fixed table above), not
    settable independently -- there is exactly one correct retryability
    per code.

    `reason` (Phase 4G) is an OPTIONAL, closed-vocabulary machine-readable
    classifier -- e.g. an `ApprovalDenialReason` value -- for callers that
    need to distinguish between several distinct denial causes that all
    share one `error_code` (every "this proposal cannot be
    approved/rejected/executed right now" case is `action_failure`, but a
    frontend may need to tell "expired" apart from "stale"/"already
    consumed" WITHOUT parsing `user_message` prose, which is free text and
    must never be pattern-matched for semantics). Deliberately optional
    and omitted from `to_dict()` entirely when unset, so every existing
    caller/consumer of the 4-field `{errorCode, userMessage, retryable,
    correlationId}` contract is completely unaffected.
    """

    error_code: ErrorCode
    user_message: str
    retryable: bool = field(init=False)
    correlation_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    reason: Optional[str] = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "retryable", _RETRYABLE[self.error_code])

    def to_dict(self) -> dict[str, object]:
        body: dict[str, object] = {
            "errorCode": self.error_code,
            "userMessage": self.user_message,
            "retryable": self.retryable,
            "correlationId": self.correlation_id,
        }
        if self.reason is not None:
            body["reason"] = self.reason
        return body


class SafeErrorException(Exception):
    """Wraps a SafeError so tool/gateway code can raise and catch it
    uniformly.

    The exception's own message is deliberately just the error code --
    never `str(original_exception)`. Upstream HTTP-library exceptions
    (timeouts, connection errors) frequently echo the request URL in their
    own message, which would leak the Power Automate gateway secret if it
    were ever propagated verbatim. Always construct the `user_message`
    from a fixed, pre-written string, never from an upstream exception's
    text.
    """

    def __init__(self, safe_error: SafeError) -> None:
        super().__init__(safe_error.error_code)
        self.safe_error = safe_error


def not_found(
    message: str = "The requested Teams chat could not be found.",
) -> SafeErrorException:
    return SafeErrorException(SafeError(error_code="not_found", user_message=message))


def run_failure(
    message: str = "The Teams request could not be completed. Please try again.",
) -> SafeErrorException:
    return SafeErrorException(SafeError(error_code="run_failure", user_message=message))


def rate_limited(
    message: str = "Teams is temporarily rate-limiting requests. Please try again shortly.",
) -> SafeErrorException:
    return SafeErrorException(SafeError(error_code="rate_limited", user_message=message))


def connector_unavailable(message: str) -> SafeErrorException:
    """A downstream connector this API depends on (Teams/Power Automate,
    or -- POST-5.1 B2 -- private GCS attachment storage) is not
    currently reachable/configured. `message` is required (no Teams-
    specific default) since this is now used by more than one connector.
    """
    return SafeErrorException(SafeError(error_code="connector_unavailable", user_message=message))


def validation_error(message: str) -> SafeErrorException:
    return SafeErrorException(SafeError(error_code="validation_error", user_message=message))


def internal_error(
    message: str = "Something went wrong handling this Teams request.",
) -> SafeErrorException:
    return SafeErrorException(SafeError(error_code="internal_error", user_message=message))


def payload_too_large(
    message: str = "The uploaded file is too large.",
) -> SafeErrorException:
    return SafeErrorException(SafeError(error_code="payload_too_large", user_message=message))


def unsupported_media_type(
    message: str = "The uploaded file is not a supported image type.",
) -> SafeErrorException:
    return SafeErrorException(SafeError(error_code="unsupported_media_type", user_message=message))
