"""ADK tool: `teams_get_hosted_content` -- Teams Rich Content milestone,
extended by the Multiple Teams Hosted Images milestone to support several
images from one message (each retrieved via its OWN call to this tool).

Retrieves ONE already-discovered inline/pasted Teams image's bytes per
call, validates them, and returns a PROVIDER-NEUTRAL, model/frontend-safe
result -- `incident_manager` calls this once per relevant `hosted_content_
id` when a message carries more than one. This is the ONLY place in
`backend/tools/teams/` that reaches
across the Microsoft integration/provider boundary for hosted content --
everything above this module (Incident Manager, `TeamsMessage`,
provenance) deals exclusively in Teams-domain concepts
(`chat_id`/`message_id`/`hosted_content_id`/`content_type`/`size_bytes`),
never in Power-Automate-specific transport shape (`"teams.getHostedContent"`,
`contentBase64`, PA `requestId` semantics). A future Microsoft Graph
adapter could replace `PowerAutomateClient.get_hosted_content` entirely
without this tool's own signature, return shape, or callers changing at
all -- see `power_automate_client.py`'s own docstring on that method.

TEAMS IMAGE VISION corrective milestone: this tool's return value
(`TeamsHostedContentResult`) STILL carries no image bytes/Base64 at all
-- it remains a safe SUMMARY proving retrieval and validation succeeded
(`content_type`/`size_bytes`), never a vehicle for raw binary content
into a model prompt or a frontend DTO. The validated bytes ARE, however,
now delivered into Incident Manager's OWN next model call as real Gemini
multimodal input -- via a SEPARATE, narrow side channel
(`backend.api.hosted_content_vision_context.stash_pending_hosted_content_
image`, consumed by that module's own `before_model_callback`), never by
widening this function's own return shape. See that module's own
docstring for the full ADK-source-verified rationale (why `FunctionTool`
responses cannot carry media directly, and why `before_model_callback` is
the correct, already-used-elsewhere extension point instead).

PROVENANCE ENFORCEMENT: `hosted_content_id` may only be retrieved using
the EXACT `(chat_id, message_id, hosted_content_id)` triple a real
`teams_get_messages` call for that SAME `chat_id` actually discovered
earlier in this same turn -- enforced against `KNOWN_HOSTED_CONTENT_IDS_
STATE_KEY` (get_messages.py), mirroring `known_message_ids`'s own "no
model-asserted identifier may authorize retrieval" discipline exactly. A
model can never invent, guess, or reuse a hosted_content_id from a
different message/chat/turn -- and (full provenance binding corrective
pass) supplying the correct `message_id`/`hosted_content_id` alongside a
WRONG `chat_id` is rejected by this backend's own deterministic check,
BEFORE Power Automate is ever called -- never relying on the gateway/
Microsoft Graph to reject a cross-chat combination on our behalf.
"""
from __future__ import annotations

import base64
import binascii
import logging
from typing import Any, NamedTuple, Optional

from google.adk.tools import ToolContext

from backend.api.hosted_content_vision_context import (
    already_retrieved_this_run,
    get_message_hosted_content_order,
    stash_pending_hosted_content_image,
)
from backend.attachments.validation import InvalidImageError, validate_image_bytes
from backend.config.settings import get_settings
from backend.gateway.power_automate_client import GatewayPayload, PowerAutomateClient
from backend.gateway.safe_error import (
    SafeErrorException,
    internal_error,
    payload_too_large,
    unsupported_media_type,
    validation_error,
)
from backend.tools.teams.get_messages import read_known_hosted_content_ids
from backend.tools.teams.schemas import TeamsGetAllHostedContentResult, TeamsHostedContentResult

_logger = logging.getLogger(__name__)


def _error_result(exc: SafeErrorException) -> dict[str, Any]:
    return {"error": exc.safe_error.to_dict()}


def _validation_error_result(message: str) -> dict[str, Any]:
    return _error_result(validation_error(message))


def _echoed_value_mismatches(raw: dict[str, Any], key: str, expected: str) -> bool:
    """True only if `raw[key]` is PRESENT and does not match `expected` --
    an absent/unechoed field is never treated as a mismatch (the live
    contract does not guarantee every field is echoed back; see this
    module's own docstring).
    """
    echoed = raw.get(key)
    return echoed is not None and echoed != expected


class HostedContentBytes(NamedTuple):
    """Validated, decoded hosted-content bytes plus the confirmed-actual
    (never merely declared) content type -- the shared return shape both
    `teams_get_hosted_content` (ADK tool) and the authenticated Source
    visual-evidence HTTP endpoint (`source_images.py`) build on top of.
    Never persisted, logged, or returned past its one immediate caller.
    """

    content_type: str
    data: bytes


def _parse_hosted_content_bytes(
    raw: GatewayPayload, chat_id: str, message_id: str, hosted_content_id: str
) -> HostedContentBytes | dict[str, Any]:
    """Converts the Power-Automate-shaped gateway response into validated,
    decoded `HostedContentBytes`, or a safe `{"error": ...}` dict on any
    failure. Never raises -- every failure path here is a deliberate, safe
    classification, never an uncaught exception.

    This is the ONLY function in this codebase that ever reads
    `contentBase64`/`contentType`/PA's own `success` field -- once past
    this function, nothing downstream ever sees them again.

    Source Visual Evidence milestone (section 8 -- "reuse validation, do
    not duplicate it"): this is the SAME gateway-response parsing, Base64
    decoding, size-budget check, and real-image-format validation
    `teams_get_hosted_content` has always used -- split out of that
    function, unchanged in behavior, so `fetch_and_validate_hosted_content`
    below (and therefore the HTTP content endpoint) applies IDENTICAL
    validation, never a second, independently-maintained (and potentially
    weaker) path.
    """
    if not isinstance(raw, dict):
        return _validation_error_result(
            "The Teams connector returned an unexpected response shape for hosted content."
        )
    if raw.get("success") is not True:
        return _error_result(
            internal_error("The Teams connector reported that hosted-content retrieval did not succeed.")
        )

    # Cross-check every echoed identifier against exactly what was
    # requested -- a provider response naming a DIFFERENT chat/message/
    # content than requested is never trusted, regardless of why (a
    # gateway bug, a race, or something adversarial) -- see docs on
    # destination binding (docs/TEAMS_TOOL_CONTRACT.md #8), the same
    # discipline extended to hosted content here.
    if (
        _echoed_value_mismatches(raw, "chatId", chat_id)
        or _echoed_value_mismatches(raw, "messageId", message_id)
        or _echoed_value_mismatches(raw, "hostedContentId", hosted_content_id)
    ):
        return _error_result(
            internal_error(
                "The Teams connector returned hosted content for a different chat, message, or content id than requested."
            )
        )

    content_type = raw.get("contentType")
    if not isinstance(content_type, str) or not content_type.strip():
        return _error_result(
            internal_error("The Teams connector did not return a content type for this hosted content.")
        )

    content_base64 = raw.get("contentBase64")
    if not isinstance(content_base64, str) or not content_base64.strip():
        return _error_result(internal_error("The Teams connector did not return content for this hosted content."))

    try:
        decoded = base64.b64decode(content_base64, validate=True)
    except (binascii.Error, ValueError):
        return _error_result(internal_error("The Teams connector returned malformed hosted content."))

    if not decoded:
        return _error_result(internal_error("The Teams connector returned empty hosted content."))

    max_bytes = get_settings().chat_attachment_max_bytes
    if len(decoded) > max_bytes:
        return _error_result(
            payload_too_large("This Teams image exceeds the maximum supported size and could not be retrieved.")
        )

    try:
        # Never trust `contentType` merely because Power Automate/Teams
        # declared it -- `validate_image_bytes` decodes the bytes and
        # confirms the ACTUAL format, exactly the same discipline B2's
        # own direct-upload path already enforces (never loosened here).
        validate_image_bytes(decoded, content_type)
    except InvalidImageError as exc:
        return _error_result(unsupported_media_type(str(exc)))

    return HostedContentBytes(content_type=content_type, data=decoded)


def fetch_and_validate_hosted_content(
    chat_id: str, message_id: str, hosted_content_id: str
) -> HostedContentBytes | dict[str, Any]:
    """Fetches ONE hosted-content item via Power Automate and returns
    validated, decoded bytes -- the single reused fetch+validate path
    (Source Visual Evidence milestone, section 8). Deliberately does
    NOT:

      - check the transient per-turn agent provenance registry
        (`known_hosted_content_ids`) -- that is `teams_get_hosted_content`'s
        own, ADDITIONAL concern (the model must only ever retrieve an id it
        actually discovered this turn); the authenticated Source visual-
        evidence HTTP endpoint (`source_images.py`) instead authorizes its
        OWN call to this function from a durable, server-persisted binding
        established at the ORIGINAL turn -- a completely different, and
        for that caller, sufficient, authorization boundary.
      - stash anything for Gemini delivery (`hosted_content_vision_
        context.stash_pending_hosted_content_image`) -- that only makes
        sense for a live, model-driven turn.
      - log a "teams_hosted_content_retrieved" line -- each caller logs
        its own, differently-shaped, safe diagnostic.

    Returns `HostedContentBytes` on success, or a safe `{"error": ...}`
    dict on failure (missing/empty input, a gateway failure, an
    inconsistent provider response, or content that fails image
    validation). Never raises.
    """
    if not chat_id or not chat_id.strip():
        return _validation_error_result("A Teams chat id is required to retrieve hosted content.")
    if not message_id or not message_id.strip():
        return _validation_error_result("A Teams message id is required to retrieve hosted content.")
    if not hosted_content_id or not hosted_content_id.strip():
        return _validation_error_result("A Teams hosted content id is required to retrieve hosted content.")

    client = PowerAutomateClient()
    try:
        raw = client.get_hosted_content(chat_id, message_id, hosted_content_id)
    except SafeErrorException as exc:
        # Safe by construction -- see get_members.py's own identical
        # logging discipline (error_code/retryable only, never a raw
        # payload, id, or the gateway URL).
        _logger.warning(
            "fetch_and_validate_hosted_content: gateway call failed (error_code=%s, retryable=%s)",
            exc.safe_error.error_code,
            exc.safe_error.retryable,
        )
        return _error_result(exc)

    return _parse_hosted_content_bytes(raw, chat_id, message_id, hosted_content_id)


def teams_get_hosted_content(
    chat_id: str,
    message_id: str,
    hosted_content_id: str,
    tool_context: Optional[ToolContext] = None,
) -> dict[str, Any]:
    """Retrieve and validate ONE inline/pasted Teams image already
    discovered on a specific message.

    `hosted_content_id` MUST be a value that a real `teams_get_messages`
    call already returned this turn, on `TeamsMessage.hosted_content_ids`
    for the SAME `message_id` given here -- never a value the model
    invents, guesses, remembers from an earlier unrelated turn, or copies
    from a different message. `chat_id`/`message_id` must likewise be
    values already established this turn via real Teams tool results
    (docs/TEAMS_TOOL_CONTRACT.md #8's destination-binding rule).

    Args:
      chat_id: The Teams chat id the message belongs to -- the same
        `matched_chat.chat_id` (or already-resolved destination) already
        governing this turn's Teams retrieval.
      message_id: The id of the specific retrieved message that carried
        this hosted content (see `TeamsMessage.id`).
      hosted_content_id: One entry of that message's own
        `TeamsMessage.hosted_content_ids`.
      tool_context: Auto-injected by ADK in real use (never supplied by
        the model). Used to enforce that `hosted_content_id` was actually
        discovered for `message_id` this turn -- safe to omit in tests
        (provenance enforcement is then skipped, exactly like every other
        Teams tool's own `tool_context`-optional contract), never omitted
        in a real agent-driven turn.

    Returns:
      On success, `TeamsHostedContentResult` (dumped to a plain dict) --
      `chat_id`/`message_id`/`hosted_content_id`/`content_type`/
      `size_bytes` only, NEVER image bytes or Base64. On failure --
      missing/invalid input, unproven provenance, a gateway failure, an
      inconsistent provider response, or content that fails image
      validation -- a dict with a single `error` key holding a SafeError.
    """
    if not chat_id or not chat_id.strip():
        return _validation_error_result("A Teams chat id is required to retrieve hosted content.")
    if not message_id or not message_id.strip():
        return _validation_error_result("A Teams message id is required to retrieve hosted content.")
    if not hosted_content_id or not hosted_content_id.strip():
        return _validation_error_result("A Teams hosted content id is required to retrieve hosted content.")

    if tool_context is not None:
        known = read_known_hosted_content_ids(tool_context.state)
        if hosted_content_id not in known.get(chat_id, {}).get(message_id, set()):
            return _validation_error_result(
                "This hosted content was not discovered for the specified chat and message through a trusted "
                "Teams retrieval this turn."
            )

    fetched = fetch_and_validate_hosted_content(chat_id, message_id, hosted_content_id)
    if isinstance(fetched, dict):
        return fetched

    # Teams Image Vision corrective milestone, extended by the Multiple
    # Teams Hosted Images milestone: stash the validated bytes for
    # `inject_pending_hosted_content_image` (a `before_model_callback`) to
    # attach as real Gemini multimodal input on the NEXT model call -- see
    # hosted_content_vision_context.py's own module docstring. Only
    # reached after successful decode + validation above; the bytes
    # themselves never appear in this function's return value, a log
    # line, or anywhere else. `delivered` is `False` only when this run
    # already reached the per-message image-count or total-byte budget
    # (see that module's own "BOUNDS" docstring) -- retrieval/validation
    # still succeeded, but the image was not queued for visual reasoning;
    # surfaced truthfully via `delivered_for_visual_reasoning` rather than
    # silently dropped.
    delivered = stash_pending_hosted_content_image(
        chat_id, message_id, hosted_content_id, fetched.content_type, fetched.data
    )
    _logger.info(
        "teams_hosted_content_retrieved mime_type=%s size_bytes=%d delivered_for_visual_reasoning=%s",
        fetched.content_type,
        len(fetched.data),
        delivered,
    )

    return TeamsHostedContentResult(
        chat_id=chat_id,
        message_id=message_id,
        hosted_content_id=hosted_content_id,
        content_type=fetched.content_type,
        size_bytes=len(fetched.data),
        delivered_for_visual_reasoning=delivered,
    ).model_dump(mode="json")


def teams_get_all_hosted_content(
    chat_id: str,
    message_id: str,
    tool_context: Optional[ToolContext] = None,
) -> dict[str, Any]:
    """Deterministic All-Image Retrieval milestone: retrieves and validates
    EVERY eligible hosted-content image already discovered for this EXACT
    message by a real `teams_get_messages` call earlier this turn -- the
    single, deterministic BACKEND expansion over `TeamsMessage.hosted_
    content_ids`, replacing what would otherwise require the model to call
    `teams_get_hosted_content` once per image itself.

    WHY THIS EXISTS (root cause of the corrected defect): real live-stack
    validation proved that when a model is expected to call `teams_get_
    hosted_content` once per image itself, it does not reliably do so --
    it sometimes retrieves only some of a message's images even when the
    user explicitly asked for all of them and every image was well within
    the documented limits. That is model-driven, nondeterministic
    iteration over a set of ids -- exactly the kind of "agent vs. tool"
    boundary violation docs/AGENT_CONTRACT.md #5 already warns against
    ("a deterministic capability must never become an agent-driven loop").
    The fix is architectural, not a stronger prompt: `incident_manager`'s
    OWN semantic judgment is now expressed ENTIRELY by WHICH TOOL it
    calls -- this one (deterministic, exhaustive) when the user's request
    requires reviewing all of a message's images, or `teams_get_hosted_
    content` (single, selective) when only one specific image is needed --
    never by the model manually enumerating individual `hosted_content_id`
    values itself, which this tool's own signature does not even accept.

    AUTHORITATIVE, NEVER MODEL-SUPPLIED, EXPANSION SOURCE: the set of ids
    retrieved is read EXCLUSIVELY from `hosted_content_vision_context.get_
    message_hosted_content_order` -- the SAME run-scoped, already-
    truncated (`MAX_HOSTED_IMAGES_PER_MESSAGE`), true-HTML-order list
    `teams_get_messages` recorded for this `message_id` and `inject_
    pending_hosted_content_image` already sorts by. There is no `hosted_
    content_ids` parameter on this function at all -- a model cannot pass
    a fabricated/reordered/partial list even if it tried.

    PER-IMAGE PROVENANCE UNCHANGED: each id from that authoritative order
    is independently cross-checked against `known_hosted_content_ids`
    (`read_known_hosted_content_ids`, the EXACT SAME registry `teams_get_
    hosted_content` itself checks) for this SAME `(chat_id, message_id)`
    before ever being retrieved -- the batch expansion does not bypass or
    weaken per-image provenance in any way; an id present in the recorded
    order but somehow absent from the trusted registry is silently
    excluded, never retrieved.

    IDEMPOTENT: an id `already_retrieved_this_run` (already queued or
    already delivered earlier in this SAME run, whether by an earlier
    individual `teams_get_hosted_content` call or an earlier call to this
    same function) is never re-fetched -- no duplicate Power Automate
    call.

    BEST-EFFORT ACROSS SIBLINGS: one image failing retrieval/validation
    never stops the remaining images from being attempted -- see `failed_
    ordinals` below.

    Returns a `TeamsGetAllHostedContentResult` (dumped to a plain dict) --
    counts and 1-based `failed_ordinals` ONLY, never a `hosted_content_id`
    (this tool's own result, like `teams_get_hosted_content`'s, is model-
    visible internal data, never sent to the frontend) -- or a `{"error":
    ...}` dict if `chat_id`/`message_id` are missing/empty, or if NO
    eligible images were discovered for the given chat/message at all.
    """
    if not chat_id or not chat_id.strip():
        return _validation_error_result("A Teams chat id is required to retrieve hosted content.")
    if not message_id or not message_id.strip():
        return _validation_error_result("A Teams message id is required to retrieve hosted content.")

    known_ids_for_message: set[str] = set()
    if tool_context is not None:
        known = read_known_hosted_content_ids(tool_context.state)
        known_ids_for_message = known.get(chat_id, {}).get(message_id, set())

    ordered_ids = get_message_hosted_content_order(message_id)
    eligible_ids = [
        content_id for content_id in ordered_ids if tool_context is None or content_id in known_ids_for_message
    ]

    if not eligible_ids:
        return _validation_error_result(
            "No hosted images were discovered for the specified chat and message through a trusted Teams "
            "retrieval this turn."
        )

    attempted_count = 0
    delivered_count = 0
    failed_ordinals: list[int] = []
    for ordinal, hosted_content_id in enumerate(eligible_ids, start=1):
        attempted_count += 1

        if already_retrieved_this_run(chat_id, message_id, hosted_content_id):
            delivered_count += 1
            continue

        fetched = fetch_and_validate_hosted_content(chat_id, message_id, hosted_content_id)
        if isinstance(fetched, dict):
            failed_ordinals.append(ordinal)
            continue

        delivered = stash_pending_hosted_content_image(
            chat_id, message_id, hosted_content_id, fetched.content_type, fetched.data
        )
        _logger.info(
            "teams_hosted_content_retrieved mime_type=%s size_bytes=%d delivered_for_visual_reasoning=%s "
            "ordinal=%d batch=all",
            fetched.content_type,
            len(fetched.data),
            delivered,
            ordinal,
        )
        if delivered:
            delivered_count += 1
        else:
            # Retrieval/validation succeeded, but the per-message image-
            # count or total-byte budget was already reached -- not
            # delivered, so it belongs in `failed_ordinals` for this
            # summary's own truthful "which ordinals did not make it"
            # accounting, even though it is not a retrieval "failure" in
            # the gateway/validation sense.
            failed_ordinals.append(ordinal)

    return TeamsGetAllHostedContentResult(
        chat_id=chat_id,
        message_id=message_id,
        discovered_count=len(ordered_ids),
        attempted_count=attempted_count,
        delivered_count=delivered_count,
        failed_ordinals=failed_ordinals,
    ).model_dump(mode="json")
