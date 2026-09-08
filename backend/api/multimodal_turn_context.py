"""In-process, never-persisted, run-id-keyed store of the CURRENT turn's
trusted image ATTACHMENT IDENTITY -- POST-5.1 B6.

WHAT THIS IS FOR, AND WHAT IT IS DELIBERATELY NOT FOR: B6 has two
completely separate multimodal-propagation needs, solved two different
ways --

  1. Getting the trusted image `Part`s themselves into the nested
     Incident Manager delegation for the SAME turn the user attached
     them. This does NOT use this module at all -- `MultimodalAgentTool`
     (multimodal_agent_tool.py) reads them directly off
     `tool_context.user_content` (ADK's own public, per-invocation
     `ReadonlyContext.user_content` property -- verified against the
     installed 1.33.0 source: `Runner.run_async`'s `new_message` becomes
     `invocation_context.user_content` for that SAME invocation, and
     `AgentTool.run_async` receives a `tool_context` whose
     `_invocation_context` IS team_manager's own top-level one, never a
     copy). That path needs no run-id correlation, no registry, and
     nothing to clean up -- it is exactly as safe/isolated as ADK's own
     per-invocation object graph already is.

  2. Capturing which ATTACHMENTS (by id, not by already-built `Part`) a
     request's images were, at the one deterministic point
     (`teams_list_chats`'s ambiguous-with-candidates branch) that needs to
     persist them into a `PendingReadIntent`/`PendingSelection` for a
     LATER turn (after the user picks a `SelectionCard` option) to resume
     with. `Part.from_uri` objects carry only `file_uri`/`mime_type` --
     never `attachment_id` (B5's own locked "no GCS URI in text/model-
     facing data" rule means the id was never embedded in the URI either)
     -- so there is no way to recover an id from a `Part` alone. THIS
     module exists only for this second need: a plain, run-id-keyed
     mapping to the ORDERED `attachment_id`s this turn's own,
     already-B5-validated `PreparedAttachment`s belong to, so
     `list_chats.py` can read them back (via `current_run_id()`, the same
     already-proven-safe ContextVar `turn_context.py` uses for the
     identical correlation problem -- ONE binding at chat_service.py's
     own turn start, read-only from here on, propagates correctly through
     the nested AgentTool/tool-thread-pool call chain per that module's
     own module docstring) and persist them onto the `PendingReadIntent`
     it creates.

NEVER a second source of truth for the images themselves: at continuation-
resume time (a LATER, different run_id/turn), `read_continuation_
execution.py` re-resolves these ids against the authoritative
`slopanoc_chat_attachments` table (never trusts a stale `mime_type`/
`gcs_uri` this module might otherwise have cached) -- see
`backend.api.attachment_service.resolve_continuation_images`.

LIFECYCLE CONTRACT, mirroring turn_context.py's own exactly: `chat_
service.py` calls `register_run_images` once, immediately after `prepare_
attachments_for_turn` validates this turn's `attachment_ids` (before the
Runner starts), and `discard_run_images` from the SAME `finally` block
that already pops the Teams-snippet mailbox and resets the run-id
ContextVar -- so this never survives past the one turn that registered
it, on ANY exit path (success, a caught exception, or `asyncio.
CancelledError`, which a bare `except Exception:` does not catch but a
`finally` always runs on).

NEVER ADK session state, never Cloud SQL, never the frontend, never a log
line, never a SourceReference -- purely an in-process correlation aid,
exactly like `turn_context.py`'s own Teams-snippet mailbox.

  3. B7 LIVE-REGRESSION CORRECTIVE PASS -- a bounded, tools-scoped
     remediation Runner call (`governed_knowledge_completion.py`'s
     `enforce_governed_knowledge_at_completion`) that is still logically
     part of the SAME user turn, but is invoked as a bare `Runner.run_
     async` call from `chat_service.py` itself -- never through
     `AgentTool`/`MultimodalAgentTool` -- so need #1's mechanism (reading
     `tool_context.user_content`) does not apply; there is no
     `tool_context` at all. `trusted_image_parts_from_content`, below,
     gives that call site a way to extract the SAME trusted `file_data`
     Part(s) directly from `chat_service.py`'s own already-built, already-
     validated turn `Content` (the very object need #1's `Multimodal
     AgentTool._trusted_image_parts` reads indirectly via `tool_context
     .user_content`) and pass them along explicitly as a plain function
     argument -- never through this module's run-id-keyed store (need #2),
     which only ever held ATTACHMENT IDS for the unrelated SelectionCard-
     continuation need, not `Part` objects, and was never designed to
     survive a remediation call's own `bind_run_id` reassignment anyway
     (see that function's own docstring: it rebinds `current_run_id()` to
     a distinct, suffixed remediation run_id, so a registry lookup at that
     point would resolve the wrong -- or no -- entry). Passing already-
     built `Part` objects directly through a function argument, scoped to
     one Python call stack within the same turn, is strictly narrower and
     safer than adding a THIRD run-id-keyed global store for the same
     purpose -- there is nothing here for an unrelated run to ever read.
"""
from __future__ import annotations

import threading
from typing import Optional, Sequence

from google.genai import types

from backend.api.turn_context import current_run_id

_lock = threading.Lock()
_store: dict[str, tuple[str, ...]] = {}


def register_run_images(run_id: Optional[str], attachment_ids: Sequence[str]) -> None:
    """Called once by `chat_service.py`, with THIS turn's own already-B5-
    validated attachment ids, in client/draft order (the same order
    `PreparedAttachment`s were built in). A no-op for a missing `run_id`
    or an empty sequence -- never raises (mirrors `turn_context.record_
    message_texts`'s own "never fail the turn over a side channel"
    discipline).
    """
    if not run_id or not attachment_ids:
        return
    with _lock:
        _store[run_id] = tuple(attachment_ids)


def current_run_image_attachment_ids() -> tuple[str, ...]:
    """Read by `list_chats.py`'s ambiguous-branch handling, deep inside
    `incident_manager`'s own nested call chain, to capture THIS turn's
    trusted image identity onto a new `PendingSelection`. Uses `current_
    run_id()` (never a parameter the caller supplies) so this can never be
    satisfied by anything other than the real, server-bound current turn
    -- there is no argument here a tool call (model-controlled) could ever
    influence. Returns `()` outside a chat_service.py-driven turn, or for
    a turn that registered no images -- both safe, ordinary "no image
    evidence" outcomes, never an error.
    """
    run_id = current_run_id()
    if run_id is None:
        return ()
    with _lock:
        return _store.get(run_id, ())


def discard_run_images(run_id: str) -> None:
    """Backstop/normal cleanup -- see this module's own "LIFECYCLE
    CONTRACT" docstring. Safe to call whether or not an entry exists.
    """
    with _lock:
        _store.pop(run_id, None)


def trusted_image_parts_from_content(content: Optional[types.Content]) -> list[types.Part]:
    """B7 live-regression corrective pass (need #3, see this module's own
    top docstring) -- extracts, in order, every `file_data`-bearing `Part`
    from an already-trusted `Content` object, for a bounded remediation
    call site that has no `tool_context`/`user_content` to read from
    (mirrors `MultimodalAgentTool._trusted_image_parts`'s own identical
    filter predicate exactly, deliberately duplicated rather than shared
    across modules -- that class is its own narrow, ADK-version-audited
    unit per its own module docstring, and this is a plain, unrelated
    function with no ADK-internals sensitivity of its own).

    ONLY `file_data` parts are ever returned -- never a text part (the
    caller's own remediation request text stays exactly where it already
    is) and never `inline_data`/bytes (this codebase never constructs one
    for a chat image -- B5's own locked rule). Returns `[]` for `None` or
    a `Content` with no parts -- both safe, ordinary "no image evidence"
    outcomes for a text-only turn, never an error.
    """
    parts = getattr(content, "parts", None)
    if not parts:
        return []
    return [part for part in parts if getattr(part, "file_data", None) is not None]
