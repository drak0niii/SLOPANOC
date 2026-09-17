"""POST-6A -- Strict validation of Power Automate WRITE responses.

THE GAP THIS CLOSES: `PowerAutomateClient._call` treats any HTTP 2xx with
a parseable JSON body as success. A Power Automate flow that reaches its
own error branch and returns **HTTP 200** with `{"success": false,
"error": ...}` -- the normal shape for a flow that caught a Graph failure
-- was therefore read as a successful write. The proposal was consumed,
the turn reported "executed", and nothing had been sent.

WHAT THIS IS: one operation-specific envelope check applied to write
responses only. Reads are untouched.

POSITIVE EVIDENCE IS REQUIRED. A response with no explicit failure is not
proof of successful execution -- an empty body is precisely what a flow
returns when it silently did nothing. So a write is CONFIRMED only when
its response carries one of:

    an explicit acknowledgement   -- `success: true`
    the produced artefact's id    -- `chatId`/`id` for createChat,
                                     `messageId`/`id` for sendMessage

`messageId` is recognized NOW, ahead of the flow returning it: when the
flow is corrected, sends become confirmed with no further code change.

THE THREE OUTCOMES ARE KEPT DISTINCT:

    BUSINESS_FAILURE -- the flow positively reported failure (`error`
                        payload, `success: false`). It did not execute.
    UNCONFIRMED      -- no failure reported, but no positive evidence
                        either. It MAY have executed. The caller records
                        UNKNOWN_OUTCOME, blocks resend and says so.
    SUCCESS          -- positive evidence present.

CONFIGURATION SELECTS A CONTRACT, NEVER A RELAXATION.
`SLOPANOC_PA_WRITE_CONTRACT` picks among supported contracts (`default`,
`ack_only`, `identifier_only`) for a flow that answers differently. There
is deliberately no setting that makes missing evidence count as success.

"""
from __future__ import annotations

from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel, ConfigDict

__all__ = [
    "WriteEnvelope",
    "WriteEnvelopeMode",
    "WriteEnvelopeOutcome",
    "validate_write_envelope",
]

class WriteResponseContract(BaseModel):
    """What positive evidence a given write operation's response must
    carry before the write may be called CONFIRMED.

    `acknowledgement_fields` -- fields that, when truthy, are an explicit
    success acknowledgement from the flow (e.g. `success: true`).
    `identifier_fields` -- fields carrying the artefact the operation
    produced, in preference order. The FIRST one present wins.

    Either kind of evidence is sufficient. Requiring a specific envelope
    shape would reject a flow that legitimately answers differently;
    requiring SOME positive evidence is what stops an empty body counting
    as a successful send.
    """

    model_config = ConfigDict(frozen=True)

    acknowledgement_fields: tuple[str, ...] = ("success",)
    identifier_fields: tuple[str, ...] = ()


_CONTRACTS: dict[str, WriteResponseContract] = {
    "teams.createChat": WriteResponseContract(identifier_fields=("chatId", "id")),
    # `messageId` is recognized NOW, before the flow returns it. The
    # moment the flow starts sending it, sends become CONFIRMED with no
    # further code change -- which is the point: the code must not be the
    # thing standing between a corrected flow and a confirmed result.
    # `id` is accepted as the same evidence under a different name.
    "teams.sendMessage": WriteResponseContract(identifier_fields=("messageId", "id")),
}
"""The response contract per write operation. `SLOPANOC_PA_WRITE_CONTRACT`
may SELECT among supported contracts (see `resolve_contract`); it can
never turn missing evidence into confirmed success."""


def resolve_contract(operation: str, selector: str = "default") -> WriteResponseContract:
    """Configuration selects a SUPPORTED contract; it never relaxes the
    requirement for positive evidence.

    `default`      -- the table above.
    `ack_only`     -- for a flow that acknowledges but returns no
                      identifier: the acknowledgement alone is evidence.
                      Still rejects an empty body.
    `identifier_only` -- for a flow that returns the artefact but no
                      acknowledgement flag.

    An unrecognized selector falls back to `default` rather than to
    anything permissive.
    """
    base = _CONTRACTS.get(operation, WriteResponseContract())
    if selector == "ack_only":
        return base.model_copy(update={"identifier_fields": ()})
    if selector == "identifier_only":
        return base.model_copy(update={"acknowledgement_fields": ()})
    return base


class WriteEnvelopeMode(BaseModel):
    """Which response contract to apply. There is deliberately NO flag
    that makes an unconfirmed write count as executed -- configuration
    selects among supported contracts, never relaxes the requirement for
    positive evidence."""

    model_config = ConfigDict(frozen=True)

    contract_selector: str = "default"


class WriteEnvelopeOutcome(str, Enum):
    SUCCESS = "success"
    """Validated: the flow reported success AND returned the required
    identifier."""

    UNCONFIRMED = "unconfirmed"
    """The dispatch may have succeeded, but the response carries no
    positive evidence that it did -- no acknowledgement, no identifier,
    an empty body.

    THIS IS NOT SUCCESS AND IT IS NOT FAILURE. The request left this
    process, so the write may well have happened; we simply cannot say.
    The caller records UNKNOWN_OUTCOME, blocks resend, and tells the user
    honestly that it could not confirm -- the one thing it must never do
    is report it as sent."""

    BUSINESS_FAILURE = "business_failure"
    """The flow ran and reported a failure -- `success: false`, or an
    `error` payload. HTTP status is irrelevant; this is not an execution."""

    MALFORMED = "malformed"
    """The response is not a shape this contract recognizes at all. Fails
    closed for the same reason as a business failure: an unrecognized
    response is not evidence of success."""


class WriteEnvelope(BaseModel):
    model_config = ConfigDict(frozen=True)

    outcome: WriteEnvelopeOutcome
    operation: str
    identifier: Optional[str] = None
    """The `chatId`/`messageId` the flow returned, when it did."""
    detail: str = ""
    """Safe diagnostic -- never the raw response body, which may carry
    tenant identifiers or a gateway URL."""

    @property
    def executed(self) -> bool:
        """The ONE question every caller should ask. Deliberately excludes
        UNVERIFIED: an unconfirmed write is not an execution."""
        return self.outcome is WriteEnvelopeOutcome.SUCCESS


def _error_present(payload: dict) -> bool:
    error = payload.get("error")
    if error is None:
        return False
    # An explicitly empty error object/string is not an error.
    return bool(error) if isinstance(error, (dict, list, str)) else True


def validate_write_envelope(
    operation: str, payload: Any, *, mode: Optional[WriteEnvelopeMode] = None
) -> WriteEnvelope:
    """Validate ONE write response. Pure; never raises."""
    mode = mode or WriteEnvelopeMode()
    if not isinstance(payload, dict):
        return WriteEnvelope(
            outcome=WriteEnvelopeOutcome.MALFORMED,
            operation=operation,
            detail="write response was not a JSON object",
        )

    if _error_present(payload):
        return WriteEnvelope(
            outcome=WriteEnvelopeOutcome.BUSINESS_FAILURE,
            operation=operation,
            detail="the flow returned an error payload",
        )

    success = payload.get("success")
    if success is False:
        return WriteEnvelope(
            outcome=WriteEnvelopeOutcome.BUSINESS_FAILURE,
            operation=operation,
            detail="the flow reported success=false",
        )

    contract = resolve_contract(operation, mode.contract_selector)

    acknowledged = any(payload.get(field) is True for field in contract.acknowledgement_fields)
    identifier = next(
        (
            value
            for field in contract.identifier_fields
            if isinstance(value := payload.get(field), str) and value.strip()
        ),
        None,
    )

    # POSITIVE EVIDENCE IS REQUIRED. A response with no explicit failure
    # is not proof of success: an empty body is exactly what a flow
    # returns when it silently did nothing at all. Either an explicit
    # acknowledgement or the returned artefact identifier will do -- but
    # one of them must be there.
    if not acknowledged and identifier is None:
        return WriteEnvelope(
            outcome=WriteEnvelopeOutcome.UNCONFIRMED,
            operation=operation,
            detail="the response carried no success acknowledgement and no identifier",
        )

    return WriteEnvelope(outcome=WriteEnvelopeOutcome.SUCCESS, operation=operation, identifier=identifier)
