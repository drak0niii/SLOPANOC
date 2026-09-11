"""Run-scoped, in-process bridge that delivers Teams-retrieved hosted-
content image(s) into Incident Manager's OWN nested reasoning step as real
Gemini multimodal input -- Teams Image Vision corrective milestone,
extended by the Multiple Teams Hosted Images milestone to carry MORE THAN
ONE validated image per run, in true Teams (HTML source) order.

Lives in `backend/api/` (not `backend/agents/incident_manager/`),
mirroring `turn_context.py`/`multimodal_turn_context.py`/`activity_
queue.py`'s own placement exactly: this store is written by a `backend/
tools/teams/` tool (`get_hosted_content.py`, `get_messages.py`) and read
by a `backend/agents/` callback (registered in both `backend/agents/
incident_manager/agent.py` and `backend/agents/team_manager/direct_read_
fast_path.py`) -- `backend/tools/teams/` must never import FROM `backend/
agents/` (the reverse of the established, one-directional "agents depend
on tools" layering every other Teams tool already respects), so this
shared, cross-cutting run-scoped store lives in the same neutral,
already-established `backend/api/` location its sibling stores already
use. `MAX_HOSTED_IMAGES_PER_MESSAGE` is DEFINED here (not in `backend/
tools/teams/get_messages.py`, even though that module ALSO enforces it at
extraction time) specifically to avoid a circular import: `get_messages.py`
already needs to import `record_message_hosted_content_order` FROM this
module, so this module must never import anything back from
`get_messages.py`.

============================================================================
THE GAP, VERIFIED AGAINST THE INSTALLED ADK 1.33.0 / google-genai 1.75.0
SOURCE (not assumed) -- SINGLE-IMAGE MILESTONE, UNCHANGED BY THIS PASS
============================================================================

`teams_get_hosted_content` (get_hosted_content.py) is an ordinary
`FunctionTool`. ADK's own tool-response construction
(`google.adk.flows.llm_flows.functions.__build_response_event`) is:

    function_response_parts = None
    if isinstance(tool, ComputerUseTool):
        function_response_parts = _try_decode_computer_use_image(...)
    part_function_response = types.Part.from_function_response(
        name=tool.name, response=function_result, parts=function_response_parts,
    )

`FunctionResponse.parts` (a real, documented `google.genai.types` field
carrying `FunctionResponsePart(inline_data=FunctionResponseBlob(...))` --
exactly the mechanism `ComputerUseTool` uses to hand a screenshot back to
the model) is populated ONLY for `ComputerUseTool` -- a hardcoded
`isinstance` check with no extension point for an ordinary tool. Neither
`before_tool_callback` (can only skip the call) nor `after_tool_callback`
(can only replace the RESPONSE DICT, never the sibling `parts=` kwarg)
gives a `FunctionTool` implementer any way to attach media there. This is
NOT an ADK defect -- it is a real, closed feature scoped to one built-in
tool class -- and subclassing/impersonating `ComputerUseTool` would be
exactly the kind of "ADK-internals hack" this codebase's own standing
practice (see `multimodal_agent_tool.py`'s own module docstring) avoids.

`before_model_callback` (a public, already-used ADK extension point) is
the fix, unchanged in this pass -- see the single-image milestone's own
git history for the full rationale. This pass only changes HOW MANY
images that ONE callback invocation may attach, and in WHAT order.

============================================================================
MULTIPLE IMAGES -- WHY ARRIVAL ORDER IS NOT TRUSTED
============================================================================

ADK executes MULTIPLE function calls from the SAME model turn
CONCURRENTLY: `flows/llm_flows/functions.py::handle_function_call_list_
async` builds one `asyncio.create_task(...)` per function call and awaits
them all via `asyncio.gather(*tasks)`. If `incident_manager` calls
`teams_get_hosted_content` for two different `hosted_content_id`s in the
SAME model response, their COMPLETION order (and therefore the order
`stash_pending_hosted_content_image` would be invoked in, if this module
simply appended on arrival) is NOT guaranteed to match Teams' own true
HTML order -- verified against the installed ADK source, not assumed.
Naive append-on-arrival would therefore violate the "never reorder by
retrieval completion time" requirement under real, everyday concurrent
tool execution, not merely a contrived edge case.

FIX: `record_message_hosted_content_order` captures the message's own
TRUE, already-order-preserving `hosted_content_ids` list (from
`TeamsMessage.hosted_content_ids`, itself unchanged -- see `hosted_
content.py`) at `teams_get_messages` time, run-scoped and keyed by
`message_id`. `inject_pending_hosted_content_image` sorts whatever images
actually got stashed (in WHATEVER order they arrived) back into that
canonical order before building Gemini `Part`s -- arrival order is used
ONLY as a stable tiebreak for an image whose id is, for any reason, not
found in the recorded canonical order (never a crash, never a dropped
image).

============================================================================
LIFECYCLE / SAFETY
============================================================================

- Bytes are stashed by `teams_get_hosted_content` ONLY after successful
  decode + `validate_image_bytes` for THAT image -- never before, never on
  any error path for that image. One image's failure never affects any
  other already-stashed image for the same run (see `get_hosted_content
  .py`'s own per-call independence, unchanged).
- Keyed by `current_run_id()` (`backend.api.turn_context` -- the SAME
  ContextVar `teams_get_messages`/`activity_queue.py`/`perf_timing.py`
  already rely on, already verified to propagate through this exact
  nested `AgentTool` call chain, including through ADK's concurrent
  per-function-call tasks -- `teams_get_messages` already reads it
  successfully today from inside that same task-creation pattern). A call
  outside a bound turn (no `current_run_id()`) is a safe no-op.
- `inject_pending_hosted_content_image` POPS (reads + clears) ALL pending
  images for the run in one call, so they are attached to exactly ONE
  subsequent model call, never repeated on a later call in the same run.
- `discard_pending_hosted_content_image` is the `finally`-block backstop
  (wired into `chat_service.py`, alongside `discard_run_images`/every
  other run-scoped store this codebase already cleans up there) for the
  rare case a stash is written but no further model call ever consumes it
  (e.g. a turn that errors immediately after the tool call) -- guarantees
  no entry survives past the one turn that produced it, on any exit path.
  Clears BOTH the pending-image list and the per-message order map.
- Never logs, persists, or otherwise exposes the raw bytes themselves --
  only `mime_type`/`size_bytes`/counts (all already-safe fields) ever
  reach a log line.
- A second `stash` call for an id ALREADY stashed this run is idempotent
  (returns success, never appends a duplicate `Part`) -- covers a model
  retrying the same retrieval.

============================================================================
BOUNDS (section 9) -- DOCUMENTED, NOT INVENTED WITHOUT REASONING
============================================================================

`MAX_HOSTED_IMAGES_PER_MESSAGE = 5`: the instruction's own suggested
conservative default for one Gemini multimodal turn; no existing Teams-
specific per-message image limit existed to inherit instead. Enforced
TWICE, independently: (1) `get_messages.py` truncates `TeamsMessage
.hosted_content_ids` itself to the first 5 (true HTML order, never
reordered) and sets `hosted_content_truncated=True` so the model can
truthfully say more images existed; (2) `stash_pending_hosted_content_
image` independently refuses a 6th image for the same run even if
reached some other way -- defense in depth, never assumed to be
unreachable merely because layer (1) already bounds it.

`MAX_TOTAL_HOSTED_IMAGE_BYTES = 20_000_000` (20 MB): each individual
image is ALREADY bounded by `Settings.chat_attachment_max_bytes` (8 MiB
default, enforced in `get_hosted_content.py::_parse_hosted_content_
response`, unchanged) -- this is a SEPARATE, run-level TOTAL budget
across up to `MAX_HOSTED_IMAGES_PER_MESSAGE` images, chosen conservatively
below the worst theoretical case (5 x 8 MiB = ~42 MB) to keep one Gemini
multimodal request's total payload bounded to a sane size regardless of
how many individually-valid-sized images a message happens to carry, while
still comfortably fitting 5 realistic screenshots (typically far smaller
than the per-image ceiling in practice).

A stash rejected for either reason returns `False` -- `get_hosted_content
.py` surfaces this as `TeamsHostedContentResult.delivered_for_visual_
reasoning=False`, so the tool caller is truthfully told retrieval/
validation succeeded but the image was NOT queued for visual reasoning --
never silently dropped, never claimed as reviewed.
"""
from __future__ import annotations

import logging
import threading
import uuid
from typing import Any, NamedTuple, Optional, Sequence

from google.genai import types

from backend.api.turn_context import current_run_id

_logger = logging.getLogger(__name__)

MAX_HOSTED_IMAGES_PER_MESSAGE = 5
MAX_TOTAL_HOSTED_IMAGE_BYTES = 20_000_000


class HostedImage(NamedTuple):
    chat_id: str
    message_id: str
    hosted_content_id: str
    mime_type: str
    data: bytes


class DeliveredVisualEvidence(NamedTuple):
    """Teams Visual Evidence milestone -- one image that was ACTUALLY
    attached as a real Gemini `Part` this turn (the final category in the
    DISCOVERED -> RETRIEVED -> QUEUED -> ACTUALLY ATTACHED chain; see this
    module's own top docstring). Recorded ONLY by `inject_pending_hosted_
    content_image`, after it has already built the real `Part`s -- never
    for an image that merely reached QUEUED. Carries no bytes -- only safe
    metadata plus the real provenance triple needed to re-fetch the image
    later through the authenticated content endpoint (never exposed to the
    frontend directly; `chat_service.py` uses this to build the PUBLIC,
    opaque `SourceVisualEvidenceItemDTO` and the durable INTERNAL binding
    separately -- see turn_source_references.py).
    """

    chat_id: str
    message_id: str
    hosted_content_id: str
    mime_type: str
    size_bytes: int
    ordinal: int
    """1-based position in true Teams source-HTML order."""
    author: str
    sent_at: str


_lock = threading.Lock()
_pending: dict[str, list[HostedImage]] = {}
_message_order: dict[str, dict[str, list[str]]] = {}
_message_metadata: dict[str, dict[str, tuple[str, str]]] = {}
_delivered: dict[str, list[DeliveredVisualEvidence]] = {}


def record_message_hosted_content_order(message_id: str, hosted_content_ids: Sequence[str]) -> None:
    """Called by `teams_get_messages` (get_messages.py) with one message's
    own TRUE, already-truncated, HTML-source-order `hosted_content_ids` --
    used ONLY to sort whatever images actually get stashed back into that
    canonical order at injection time, regardless of retrieval/tool-call
    completion order (see this module's own "MULTIPLE IMAGES" docstring).
    A safe no-op outside a bound turn, or for an empty list -- never
    raises (a retrieval call must never fail because of this side
    channel, mirroring `turn_context.record_message_texts`'s own
    discipline).
    """
    run_id = current_run_id()
    if run_id is None or not hosted_content_ids:
        return
    with _lock:
        per_run = _message_order.setdefault(run_id, {})
        per_run[message_id] = list(hosted_content_ids)


def get_message_hosted_content_order(message_id: str) -> list[str]:
    """Deterministic All-Image Retrieval milestone -- read-only accessor
    for this run's own recorded canonical (true Teams HTML) order for one
    message. Used by `teams_get_all_hosted_content` (get_hosted_content.py)
    to discover the authoritative, ordered set of ids to deterministically
    expand and retrieve -- NEVER a model-supplied list; this is the exact
    same data `inject_pending_hosted_content_image` already sorts by.
    Returns `[]` outside a bound turn, or for a message this run never
    recorded order for (e.g. no hosted images, or `teams_get_messages` was
    never called for it).
    """
    run_id = current_run_id()
    if run_id is None:
        return []
    with _lock:
        return list(_message_order.get(run_id, {}).get(message_id, []))


def already_retrieved_this_run(chat_id: str, message_id: str, hosted_content_id: str) -> bool:
    """Deterministic All-Image Retrieval milestone -- `True` if this exact
    `(chat_id, message_id, hosted_content_id)` triple was already
    successfully retrieved and queued (still pending injection, in
    `_pending`) OR already delivered (already injected, in `_delivered`)
    earlier in this SAME run. Used by `teams_get_all_hosted_content`'s
    deterministic expansion loop to avoid a redundant Power Automate call
    for an id some EARLIER call (an individual `teams_get_hosted_content`
    call, or an earlier `teams_get_all_hosted_content` expansion in a
    prior model turn of the same run) already retrieved -- "no silently
    duplicated Power Automate calls" (section 7). Deliberately does NOT
    track/dedupe FAILED attempts -- a prior failure is always safe, and
    reasonable, to retry (never a correctness or security concern, only a
    minor efficiency one, and re-attempting a failure is far less
    surprising than silently skipping it).
    """
    run_id = current_run_id()
    if run_id is None:
        return False
    triple = (chat_id, message_id, hosted_content_id)
    with _lock:
        for image in _pending.get(run_id, []):
            if (image.chat_id, image.message_id, image.hosted_content_id) == triple:
                return True
        for delivered in _delivered.get(run_id, []):
            if (delivered.chat_id, delivered.message_id, delivered.hosted_content_id) == triple:
                return True
        return False


def record_message_metadata(message_id: str, author: str, sent_at: str) -> None:
    """Teams Visual Evidence milestone -- called by `teams_get_messages`
    (get_messages.py) alongside `record_message_hosted_content_order`, for
    every message that carries at least one hosted image, with that
    message's own real `author`/`sent_at` (from `TeamsMessage`, the SAME
    deterministic retrieval this codebase already trusts elsewhere --
    never from `incident_manager`'s own curated `evidence` list, which is
    a DIFFERENT, model-influenced selection that might not include the
    image-bearing message at all). Used only to fill in `SourceVisual
    EvidenceItemDTO.author`/`sent_at` for an image actually delivered to
    Gemini -- never persisted verbatim beyond that DTO construction. A
    safe no-op outside a bound turn -- never raises.
    """
    run_id = current_run_id()
    if run_id is None:
        return
    with _lock:
        per_run = _message_metadata.setdefault(run_id, {})
        per_run[message_id] = (author, sent_at)


def stash_pending_hosted_content_image(
    chat_id: str, message_id: str, hosted_content_id: str, mime_type: str, data: bytes
) -> bool:
    """Called by `teams_get_hosted_content` immediately after the decoded
    bytes pass `validate_image_bytes` for THIS image -- see this module's
    own "LIFECYCLE / SAFETY" and "BOUNDS" docstrings.

    Returns `True` if the image was queued (or was already queued -- a
    duplicate stash for the same `(message_id, hosted_content_id)` is
    idempotent), `False` if it was NOT queued because this run already
    reached `MAX_HOSTED_IMAGES_PER_MESSAGE` or `MAX_TOTAL_HOSTED_IMAGE_
    BYTES` -- `get_hosted_content.py` surfaces this as `TeamsHostedContent
    Result.delivered_for_visual_reasoning`. Also returns `False` outside a
    bound turn (no `current_run_id()`) -- there is no run to deliver into,
    so "not delivered" is the accurate answer, never a raise.
    """
    run_id = current_run_id()
    if run_id is None:
        return False
    with _lock:
        existing = _pending.setdefault(run_id, [])
        for image in existing:
            if image.message_id == message_id and image.hosted_content_id == hosted_content_id:
                return True  # already queued -- idempotent, never a duplicate Part
        if len(existing) >= MAX_HOSTED_IMAGES_PER_MESSAGE:
            return False
        current_total = sum(len(image.data) for image in existing)
        if current_total + len(data) > MAX_TOTAL_HOSTED_IMAGE_BYTES:
            return False
        existing.append(HostedImage(chat_id, message_id, hosted_content_id, mime_type, data))
        return True


def discard_pending_hosted_content_image(run_id: str) -> None:
    """`finally`-block backstop -- see this module's own "LIFECYCLE /
    SAFETY" docstring. Clears the pending-image list, the per-message
    canonical-order map, the per-message metadata map, and any delivered-
    visual-evidence records for this run. Safe to call whether or not any
    entry exists.
    """
    with _lock:
        _pending.pop(run_id, None)
        _message_order.pop(run_id, None)
        _message_metadata.pop(run_id, None)
        _delivered.pop(run_id, None)


def pop_delivered_visual_evidence(run_id: str) -> list[DeliveredVisualEvidence]:
    """Called exactly once by `chat_service.py`, from WITHIN the same
    `finally` block that already pops `message_texts_by_id`/discards this
    module's other run-scoped stores (mirrors `pop_message_texts`'s own
    "snapshot at cleanup time, use after the try/except/finally" shape) --
    removes and returns whatever `inject_pending_hosted_content_image`
    actually delivered this run, in true Teams order. Returns `[]` if the
    run never delivered anything (a non-Teams-image turn, a turn that
    retrieved images but the turn ended before any model call could
    consume them, or a run_id nobody registered). Idempotent -- a second
    call for the same `run_id` simply returns `[]`.
    """
    with _lock:
        return _delivered.pop(run_id, [])


def _sort_key(image: HostedImage, order_map: dict[str, list[str]], arrival_index: int) -> tuple[int, int]:
    """Sorts by this image's position in its OWN message's recorded
    canonical (true Teams HTML) order first; an image whose id is not
    found there (should not normally happen, but never trusted to be
    impossible) sorts after every known-ordered image, using arrival
    index as a STABLE tiebreak -- never a crash, never a silently dropped
    image.
    """
    ids_for_message = order_map.get(image.message_id, [])
    try:
        position = ids_for_message.index(image.hosted_content_id)
    except ValueError:
        position = len(ids_for_message) + 1_000_000
    return (position, arrival_index)


def inject_pending_hosted_content_image(callback_context: Any, llm_request: Any) -> None:
    """`before_model_callback` -- see this module's own top docstring for
    the full ADK-source-verified rationale. Always returns `None` (never
    short-circuits the real model call, mirrors `perf_timing.py`'s own
    "never substitute a fake model response" discipline) -- this callback
    only ever ADDS a `Content` to `llm_request.contents` when at least one
    real, already-validated image is genuinely pending for this run.

    Attaches ALL pending images for this run in ONE `Content`, one real
    `types.Part.from_bytes(...)` per image, sorted into true Teams order
    (never Base64/bytes as text) -- correct and unchanged in shape for the
    single-image case (a list of one).
    """
    run_id = current_run_id()
    if run_id is None:
        return None
    with _lock:
        pending_images = _pending.pop(run_id, [])
        if not pending_images:
            return None
        # LIVE-VALIDATION BUGFIX: `_message_order` is READ here (`.get`),
        # NEVER popped/consumed. A real live run proved the model
        # frequently calls `teams_get_hosted_content` across SEPARATE
        # model turns rather than one batched function-calling response --
        # each such turn triggers its OWN `inject_pending_hosted_content_
        # image` call, potentially delivering only a SUBSET of one
        # message's images per call. An earlier version of this function
        # popped `_message_order` on the first genuinely non-empty call
        # (to avoid an EARLIER bug -- see git history -- where a no-op
        # call before any stash would prematurely discard it); that fix
        # was itself incomplete: it correctly protected against the no-op
        # case but then discarded the order map on its own FIRST real use,
        # leaving a SECOND, later injection call in the same run with an
        # empty order map -- silently falling back to `_sort_key`'s
        # "unknown order" branch and (see the ordinal computation below)
        # producing DUPLICATE ordinals for images delivered in different
        # calls. The order map must persist for the WHOLE run, read-only,
        # and is only ever actually removed by `discard_pending_hosted_
        # content_image`'s own `finally`-block cleanup.
        order_map = _message_order.get(run_id, {})
        metadata_map = _message_metadata.get(run_id, {})

    contents = getattr(llm_request, "contents", None)
    if contents is None:
        return None

    ordered_images = [
        image
        for _, image in sorted(
            enumerate(pending_images),
            key=lambda indexed: _sort_key(indexed[1], order_map, indexed[0]),
        )
    ]

    parts = [types.Part.from_text(text="Retrieved Teams image(s) (the actual image(s) follow, in order):")]
    for image in ordered_images:
        parts.append(types.Part.from_bytes(data=image.data, mime_type=image.mime_type))
    contents.append(types.Content(role="user", parts=parts))

    total_bytes = sum(len(image.data) for image in ordered_images)
    _logger.info(
        "teams_image_parts_attached count=%d total_image_bytes=%d",
        len(ordered_images),
        total_bytes,
    )

    # Teams Visual Evidence milestone: record what was ACTUALLY attached,
    # in true order, for `chat_service.py` to build the Source drawer's
    # visual evidence from -- this is the ONLY point in the whole pipeline
    # that knows both "genuinely delivered" AND "final true order" at
    # once. `author`/`sent_at` default to "" when this run never recorded
    # metadata for that message_id (should not normally happen -- every
    # message with hosted_content_ids also gets `record_message_metadata`
    # called for it -- but never crashes either way). Appended (never
    # overwritten) so multiple injection calls within one run accumulate,
    # mirroring every other run-scoped accumulator in this codebase.
    #
    # LIVE-VALIDATION BUGFIX: `ordinal` MUST be this image's TRUE position
    # in `order_map[image.message_id]` (the canonical Teams HTML order),
    # NEVER `enumerate(ordered_images, start=1)`'s own per-call index. A
    # real live run proved the model frequently calls `teams_get_hosted_
    # content` across SEPARATE model turns rather than one batched
    # function-calling response -- each such turn triggers its OWN,
    # separate `inject_pending_hosted_content_image` invocation, and a
    # per-call `enumerate(..., start=1)` would restart numbering from 1
    # every time, producing duplicate ordinals (e.g. two delivered images
    # both showing `ordinal=1`) whenever delivery is split across more
    # than one such call within the same run. Deriving `ordinal` from the
    # SAME `order_map` already used for sorting makes it globally correct
    # and stable across any number of separate injection calls.
    with _lock:
        delivered_for_run = _delivered.setdefault(run_id, [])
        for fallback_index, image in enumerate(ordered_images):
            ids_for_message = order_map.get(image.message_id, [])
            try:
                ordinal = ids_for_message.index(image.hosted_content_id) + 1
            except ValueError:
                # Not found in the recorded canonical order (should not
                # normally happen -- mirrors `_sort_key`'s own "sorts to
                # the end, never crashes" tolerance) -- still unique and
                # deterministic within this call, never colliding with a
                # known-order ordinal (which is always <= len(ids_for_message)).
                ordinal = len(ids_for_message) + 1 + fallback_index
            author, sent_at = metadata_map.get(image.message_id, ("", ""))
            delivered_for_run.append(
                DeliveredVisualEvidence(
                    chat_id=image.chat_id,
                    message_id=image.message_id,
                    hosted_content_id=image.hosted_content_id,
                    mime_type=image.mime_type,
                    size_bytes=len(image.data),
                    ordinal=ordinal,
                    author=author,
                    sent_at=sent_at,
                )
            )
    return None


def build_visual_evidence(
    delivered_visual_evidence: list[DeliveredVisualEvidence], chat_id: Optional[str]
) -> tuple[list["SourceVisualEvidenceItemDTO"], list[dict[str, str]]]:
    """Pure function (Teams Visual Evidence milestone) -- turns this run's
    ACTUALLY-delivered images into the PUBLIC `SourceVisualEvidenceItemDTO`
    list `chat_service.py` attaches to `SourceReferenceDTO.visual_evidence`
    AND the parallel INTERNAL `visual_evidence_internal` binding list
    `turn_source_references.build_turn_source_references_delta` persists
    alongside it -- both built in the SAME pass, `image_id` values matching
    1:1 by construction (never independently minted, which could drift).

    Filters to ONLY images whose OWN `chat_id` matches the Source's actual
    `chat_id` (section 6 -- "do not attach unrelated run images to a Teams
    Source"); a `chat_id` of `None` (no Teams source resolved this turn)
    yields `([], [])` -- there is no Source to attach visual evidence to.
    Preserves `delivered_visual_evidence`'s own already-true-Teams-order
    (see `inject_pending_hosted_content_image`'s own ordering guarantee) --
    never re-sorts.

    Extracted as a pure, dependency-light function specifically so it is
    unit-testable without a real ADK Runner/ChatService turn -- mirrors
    `source_reference.build_teams_source_reference`'s and `turn_source_
    references.build_turn_source_references_delta`'s own "pure function,
    independently testable" shape.
    """
    from backend.api.schemas import SourceVisualEvidenceItemDTO

    items: list[SourceVisualEvidenceItemDTO] = []
    internal: list[dict[str, str]] = []
    for image in delivered_visual_evidence:
        if chat_id is None or image.chat_id != chat_id:
            continue
        image_id = str(uuid.uuid4())
        items.append(
            SourceVisualEvidenceItemDTO(
                image_id=image_id,
                ordinal=image.ordinal,
                mime_type=image.mime_type,
                size_bytes=image.size_bytes,
                author=image.author,
                sent_at=image.sent_at,
            )
        )
        internal.append(
            {
                "image_id": image_id,
                "chat_id": image.chat_id,
                "message_id": image.message_id,
                "hosted_content_id": image.hosted_content_id,
            }
        )
    return items, internal
