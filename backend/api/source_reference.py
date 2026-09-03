"""Deterministic construction of safe, structured Teams source/provenance
data for the frontend's source drawer (pre-4H UX/provenance milestone).

REPLACES PROSE PROVENANCE, NEVER PARSES IT: previously, team_manager's own
answer text carried a provenance footer ("This information is based on
messages sent by... between..."). That footer is now removed from the
prompt contract (see team_manager/prompts.py step 4's "ok" case) in favor
of this structured object, which the frontend renders as a separate
"Source" chip/drawer. This module NEVER inspects the model's own final
answer text to build it -- it is built entirely from the same already-
validated, already-safe structured fields `activity_translator.py`
already independently inspects for status/trace purposes (the
`incident_manager` tool response's `evidence`/`chat_title`), mirroring
that module's own "only tool results are facts" boundary.

BOUNDARY: `TeamsSourceCapture` is the ONE place (alongside
`StatusTranslator`/`RunTraceTranslator`) that inspects raw ADK events for
this purpose -- it independently re-observes the same event stream rather
than threading a new dependency through those existing translators,
mirroring their own established "each helper inspects what it needs"
pattern (see activity_translator.py's module docstring).

UX POLISH PASS (post pre-4H milestone): the drawer now also shows a small,
curated "supporting evidence" sample with short message snippets, not just
author/timestamp. This module is still the ONLY place that builds the
source reference, and still never parses the assistant's own answer text.
`evidence` itself is capped at `MAX_EVIDENCE_ITEMS`; `message_count`/
`period_start`/`period_end` are always derived from the full, uncapped set.

SNIPPET-AUTHENTICITY FIX (this pass): a snippet is no longer trusted from
the model at all -- `TeamsEvidence` no longer even has a `snippet` field.
Instead, `build_teams_source_reference` is given `message_texts_by_id`: a
`{message_id: text}` mapping of the ACTUAL retrieved Teams messages,
forwarded (in-memory, per-turn only, never persisted) from
`teams_get_messages` itself via `TEAMS_MESSAGE_TEXT_BY_ID_STATE_KEY` (see
that key's own docstring in get_messages.py for the full ADK `temp:`-state
mechanism this relies on -- verified against the installed ADK source, not
a new/invented mechanism). For each evidence candidate, this module looks
up its `message_id` in that mapping and deterministically builds the
excerpt (`_safe_snippet`) from that ORIGINAL text -- never from anything
the model reproduces. A candidate whose message_id has no usable text
(missing/empty/system-filtered/never retrieved) is never displayed, and
this module keeps scanning the remaining candidates -- in original order
-- until `MAX_EVIDENCE_ITEMS` valid, snippet-bearing examples are found or
the candidates are exhausted. `message_id` is used ONLY for this lookup;
it is discarded before a `SourceEvidenceItem` is ever constructed and
never reaches `SourceReferenceDTO`.

CONTRIBUTOR-ACCURACY FIX (this pass): `contributors` is NO LONGER derived
from `evidence` authors -- a chat participant who wrote none of the (at
most `MAX_EVIDENCE_ITEMS`, display-capped) cited evidence entries would
otherwise never appear, even though they are a real member of the chat.
`contributors` is now resolved separately and authoritatively, from
`teams_get_members` (docs/TEAMS_TOOL_CONTRACT.md #5) -- see
`resolve_authoritative_contributors` below, called by chat_service.py
AFTER `build_teams_source_reference` returns, using the same `chat_id`
`TeamsSourceCapture` already captured internally for this purpose (never
exposed in the DTO itself -- see `SourceReferenceDTO`'s own docstring).
Still never inferred from evidence authors, message senders, or the
assistant's own prose; still never a second agent, and never built by
parsing model output -- `teams_get_members` is a deterministic tool call,
exactly like `teams_get_messages`.
"""
from __future__ import annotations

import asyncio
import logging
import uuid
from typing import Any, Optional

from backend.api.schemas import SourceEvidenceItem, SourceReferenceDTO
from backend.tools.teams.get_members import teams_get_members

_logger = logging.getLogger(__name__)

TEAMS_SOURCE_LABEL = "Teams conversation"

_INCIDENT_MANAGER_TOOL_NAME = "incident_manager"

# UX polish pass: the drawer shows a small, curated sample, never the full
# retrieved set -- "supporting evidence is the proof, so it should feel
# useful," not an unbounded dump. `message_count`/`contributors`/
# `period_start`/`period_end` are still derived from the FULL, uncapped
# set (see `build_teams_source_reference`) -- only the displayed `evidence`
# examples themselves are capped.
MAX_EVIDENCE_ITEMS = 5

# Deterministic re-trim applied to `TeamsEvidence.snippet` regardless of
# what the model set -- the same "don't trust the model's own formatting
# further than necessary" posture already applied to `message_id`
# membership (evidence.py's `validate_evidence`). A short excerpt only;
# never long enough to functionally reconstruct a message.
_MAX_SNIPPET_LENGTH = 140


def _safe_snippet(raw: Any) -> Optional[str]:
    """Collapse whitespace/newlines and hard-cap length; `None` for
    anything that is not a non-empty string once collapsed (never
    fabricates a snippet, never lets one enter the drawer unbounded).
    """
    if not isinstance(raw, str):
        return None
    collapsed = " ".join(raw.split())
    if not collapsed:
        return None
    if len(collapsed) <= _MAX_SNIPPET_LENGTH:
        return collapsed
    return collapsed[: _MAX_SNIPPET_LENGTH - 1].rstrip() + "…"


def build_teams_source_reference(
    evidence: list[Any],
    chat_title: Optional[str],
    message_texts_by_id: Optional[dict[str, str]] = None,
) -> Optional[SourceReferenceDTO]:
    """Pure function: given `incident_manager`'s own already-validated
    `evidence` list (each entry already re-checked against the actually-
    retrieved message ids -- see evidence.py's `strip_unverified_evidence`
    -- before it ever reached this far), `chat_title`, and
    `message_texts_by_id` (the `{message_id: text}` mapping of the turn's
    actually-retrieved Teams messages -- see this module's own docstring,
    "SNIPPET-AUTHENTICITY FIX"; defaults to `{}` so a caller with no
    forwarded text -- e.g. an older/bare invocation -- still gets a valid
    result with simply no displayable evidence examples), returns the
    safe, structured source reference for this turn, or `None` if there
    is no real evidence at all to show (never fabricates a source with no
    basis).

    `message_count`/`period_start`/`period_end` are all deterministically
    derived from the FULL, uncapped set of VALID entries (author/sent_at
    present) -- never a separate, independently-fabricated count, and
    never affected by which of those entries also had a displayable
    snippet. `period_start`/`period_end` are the min/max of `sent_at` by
    plain string ordering -- safe because every `sent_at` value already
    comes from the same consistently-formatted Teams timestamp source (see
    `SourceEvidenceItem`'s own docstring); never re-parsed or re-derived
    beyond that ordering.

    `evidence` (the DISPLAYED examples) is built by a separate pass: scan
    every valid entry, IN ORDER, look up its `message_id` in
    `message_texts_by_id`, and keep it only if `_safe_snippet` produces a
    non-empty excerpt from that ORIGINAL retrieved text -- skipping (never
    padding with a blank snippet) any candidate whose message is missing,
    empty, was filtered out of the retrieved set, or was otherwise never
    captured. Scanning continues past the first `MAX_EVIDENCE_ITEMS`
    candidates for as long as needed to find up to `MAX_EVIDENCE_ITEMS`
    valid ones -- "up to N valid examples," never "the first N records."
    Fewer than `MAX_EVIDENCE_ITEMS` are returned only when fewer than
    `MAX_EVIDENCE_ITEMS` valid, snippet-bearing candidates actually exist.

    `contributors` is always returned empty here -- it is NOT derived from
    `evidence` (see this module's docstring, "CONTRIBUTOR-ACCURACY FIX").
    The caller (chat_service.py) fills it in afterward via
    `resolve_authoritative_contributors`, using the same turn's captured
    `chat_id`.
    """
    if message_texts_by_id is None:
        message_texts_by_id = {}

    valid_entries: list[dict[str, Any]] = []
    for entry in evidence:
        if not isinstance(entry, dict):
            continue
        author = entry.get("author")
        sent_at = entry.get("sent_at")
        if isinstance(author, str) and author and isinstance(sent_at, str) and sent_at:
            valid_entries.append(entry)

    if not valid_entries:
        return None

    displayed: list[SourceEvidenceItem] = []
    for entry in valid_entries:
        if len(displayed) >= MAX_EVIDENCE_ITEMS:
            break
        message_id = entry.get("message_id")
        original_text = message_texts_by_id.get(message_id) if isinstance(message_id, str) else None
        snippet = _safe_snippet(original_text)
        if snippet is None:
            continue
        displayed.append(SourceEvidenceItem(author=entry["author"], sent_at=entry["sent_at"], snippet=snippet))

    sent_ats = sorted(entry["sent_at"] for entry in valid_entries)

    return SourceReferenceDTO(
        source_id=str(uuid.uuid4()),
        source_type="teams",
        label=TEAMS_SOURCE_LABEL,
        title=chat_title if isinstance(chat_title, str) and chat_title else None,
        message_count=len(valid_entries),
        period_start=sent_ats[0],
        period_end=sent_ats[-1],
        contributors=[],
        evidence=displayed,
    )


async def resolve_authoritative_contributors(chat_id: Optional[str]) -> list[str]:
    """The Teams `Source` drawer's `contributors` list, sourced
    authoritatively from `teams_get_members` -- NEVER inferred from
    evidence authors, message senders, or the assistant's own prose (see
    this module's docstring, "CONTRIBUTOR-ACCURACY FIX"). Returns display
    names only, de-duplicated, in the order the gateway returned them --
    `teams_get_members`/`TeamsMember` already exclude anything besides
    `id`/`display_name`, and `id` is dropped here too, before this value
    ever reaches `SourceReferenceDTO`.

    `chat_id` is the internal-only value `TeamsSourceCapture` captured for
    this turn (see `TeamsSourceCapture.captured_chat_id`) -- never a value
    parsed from model output beyond the same trust boundary already
    applied to `chat_title`/`author`/`sent_at` elsewhere in this module.

    FAILS SAFE: returns `[]` for a missing `chat_id`, a gateway failure, or
    a malformed response -- never falls back to evidence authors or any
    other inferred source, and never raises past this boundary. An empty
    result simply means the frontend's Contributors section is omitted
    (`SourceChip.tsx` already does this for an empty list -- no frontend
    change needed).

    `teams_get_members` performs a real, synchronous (`requests`-based)
    HTTP call -- run via `asyncio.to_thread` so it never blocks
    `chat_service.py`'s async event loop (the same reason ADK itself runs
    synchronous Teams tool functions in a worker thread for the
    model-driven path; see chat_service.py's module docstring).
    """
    if not chat_id:
        # Diagnostically useful, never a payload/content leak (no chat_id
        # value is logged, only the fact that one was absent) -- this is
        # the single most likely reason a Source drawer ever shows no
        # Contributors: the turn produced evidence/a summary but no
        # `chat_id` could be resolved for it (see chat_service.py's own
        # call site for where this is now sourced from, and the fallback
        # chain that reduces how often this fires).
        _logger.info("resolve_authoritative_contributors: no chat_id available, skipping membership lookup")
        return []

    result = await asyncio.to_thread(teams_get_members, chat_id)
    if "error" in result:
        # teams_get_members already logged the underlying gateway failure
        # detail (safely) -- this line just marks where in the pipeline
        # that failure surfaced as "Contributors omitted."
        _logger.info("resolve_authoritative_contributors: teams_get_members reported an error, omitting Contributors")
        return []

    members = result.get("members")
    if not isinstance(members, list):
        _logger.warning("resolve_authoritative_contributors: teams_get_members returned a malformed response shape")
        return []

    names: list[str] = []
    for member in members:
        if isinstance(member, dict):
            name = member.get("display_name")
            if isinstance(name, str) and name:
                names.append(name)
    return list(dict.fromkeys(names))


class TeamsSourceCapture:
    """Stateful, per-run capture of the LAST successful (`outcome ==
    "ok"`, non-empty `evidence`) `incident_manager` response observed
    during this turn -- fed one ADK event at a time via `observe`,
    exactly like `StatusTranslator`/`RunTraceTranslator`. "Last" (not
    "first"/"all") mirrors `state_sync.py`'s own `selected_teams_chat_id`
    update semantics: the most recent successful retrieval is the one
    this turn's final answer is actually grounded in.
    """

    def __init__(self) -> None:
        self._chat_id: Optional[str] = None
        self._chat_title: Optional[str] = None
        self._evidence: Optional[list[Any]] = None

    def observe(self, event: Any) -> None:
        if getattr(event, "partial", False):
            return
        function_responses = event.get_function_responses()
        if not function_responses:
            return
        for response in function_responses:
            if getattr(response, "name", None) != _INCIDENT_MANAGER_TOOL_NAME:
                continue
            result = getattr(response, "response", None)
            if not isinstance(result, dict) or result.get("outcome") != "ok":
                continue
            evidence = result.get("evidence")
            if isinstance(evidence, list) and evidence:
                self._chat_id = result.get("chat_id")
                self._chat_title = result.get("chat_title")
                self._evidence = evidence

    def build_source_reference(
        self, message_texts_by_id: Optional[dict[str, str]] = None
    ) -> Optional[SourceReferenceDTO]:
        """`message_texts_by_id` -- the turn's `{message_id: text}` map,
        typically read by the caller from `refreshed_session.state.get(
        TEAMS_MESSAGE_TEXT_BY_ID_STATE_KEY, {})` (see get_messages.py) --
        is passed straight through to `build_teams_source_reference`;
        see this module's own docstring for why this is the ONLY source
        of a displayed snippet's text.
        """
        if not self._evidence:
            return None
        return build_teams_source_reference(self._evidence, self._chat_title, message_texts_by_id)

    def captured_chat_id(self) -> Optional[str]:
        """Internal-only -- the `chat_id` backing the currently captured
        evidence, if any. NEVER exposed to the frontend (see
        `SourceReferenceDTO`'s docstring); used only to authoritatively
        resolve `contributors` via `resolve_authoritative_contributors`.
        """
        return self._chat_id
