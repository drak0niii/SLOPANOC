"""B7 corrective pass -- durable, turn-owned Source/Knowledge-source
provenance persistence.

THE GAP THIS CLOSES: `chat_service.py` already builds a safe, structured
`SourceReferenceDTO` (Teams) and/or `KnowledgeSourceReferenceDTO` list
(governed KM) for a turn's own `message.completed` SSE event -- but
`session_history_service.py`'s `get_session_history` (`GET /api/sessions/
{id}/history`) never persisted or re-projected either one. A live turn's
answer therefore rendered its provenance correctly, but a hard refresh /
saved-chat reopen / backend restart lost it entirely, even though the
answer text itself survived (ADK's own durable event history).

WHY ADK SESSION STATE, NOT A NEW TABLE/MIGRATION: audited first (per
instruction) -- no new persistence subsystem is needed. `chat_service.py`
already writes several OTHER plain, cross-turn session-state keys this
exact same way (`selected_teams_chat_id`, `last_teams_evidence` --
state_sync.py), and ADK's own `DatabaseSessionService` already durably
persists `session.state` (proven, repeatedly, by this project's own Cloud
SQL restart validation). Reusing that mechanism for this data needs zero
schema/migration work.

WHY THIS SURVIVES REWIND FOR FREE (verified against the installed ADK
1.33.0 source, `runners.py`'s `Runner._compute_state_delta_for_rewind`,
not assumed): rewind's own state-delta reversal is computed by REPLAYING,
in event-list ORDER (not grouped by `invocation_id`), every state_delta
ever written to a given key up to the rewind point, and diffing that
against the CURRENT value -- for a key with NO entry before the rewind
point, the whole key is cleared; for a key that already had an EARLIER,
different value before the rewind point, it is restored to exactly that
earlier value. This is why `build_turn_source_references_delta` (below)
always writes the FULL, accumulated `{turn_id: {...}, ...}` dict on every
turn that has anything to persist -- never a partial/incremental patch --
mirroring the exact same "always overwrite the whole value" discipline
`state_sync.py`'s `compute_state_updates` already uses for the same
reason. No second branch-selection mechanism is implemented here; ADK's
own, already-proven rewind machinery is the only thing that decides which
turns' entries survive.

WHAT IS PERSISTED: the exact same safe `SourceReferenceDTO`/
`KnowledgeSourceReferenceDTO` payloads already sent to the frontend this
same turn (`.model_dump(mode="json")` of the SAME object instances
`chat_service.py` already built for the live SSE event) -- never a
separately-derived "safe subset", never re-queried from current KM/Teams
state. Both DTOs already exclude `source_uri`/`gs://`/any raw internal
identifier by construction (see `knowledge_source_reference.py`/
`source_reference.py`'s own docstrings) -- this module adds no new
exposure surface, it only durably stores what was already safe to show.

WHAT IS NEVER PERSISTED: anything not already part of the safe DTO
(`source_uri`, raw `knowledge_id`/database internals beyond what the DTO
already carries, model-authored citation text, anything from `agent_
payload`). This module never re-runs `knowledge_search`/`knowledge_
select_evidence` and never reads current KM/Teams state -- it only
durably stores what THIS turn's own already-trusted, already-validated
DTO construction produced.

B7 LIVE-REGRESSION CORRECTIVE PASS -- READ-TIME EXACT-IDENTITY
NORMALIZATION: `resolve_turn_source_references` now also applies
`knowledge_source_reference.dedupe_knowledge_source_references` to the
`knowledge_sources` list it reads back, keyed EXCLUSIVELY by the
canonical `(knowledge_id, version_label, section_id)` identity (never
`title`/`section_heading`/content). This is deliberately a READ-TIME
safety net for any turn's data that was already persisted with an exact
duplicate (whether from before this fix, or from a cause this pass could
not reproduce without live Cloud SQL/Gemini access) -- it never mutates
the stored session state itself, never re-runs KM retrieval, never
re-queries current KM state, and never merges/collapses two genuinely
DISTINCT evidence identities. `chat_service.py`'s own write path (`build_
turn_source_references_delta`'s caller) ALSO applies the same
normalization before persisting a NEW turn's provenance -- see that call
site's own comment -- so this read-time pass is redundant, not load-
bearing, for any turn completed after this fix; it exists purely to keep
already-persisted historical data from continuing to display a duplicate
after a hard refresh or backend restart, without a database migration.

PHASE 6A.14A -- CANONICAL TURN RESULT (closes DEF-0031): this module's
scope widened from "Teams/governed-KM provenance persistence" to owning
the full per-turn CANONICAL RESULT -- the same, single, durably persisted
object that both the live `MESSAGE_COMPLETED` SSE event and refreshed/
reopened session history now derive from.

THE GAP THIS ADDITIONALLY CLOSES (DEF-0031): `chat_service.py` applies
several deterministic, POST-HOC corrections to a turn's raw model/
specialist output AFTER the ADK Runner has already durably appended its
own final-response event (5.1J's governed-knowledge remediation, the
KNOWLEDGE_INVENTORY override, the A5 one-command troubleshooting-guidance
rendering, the 6A.14/DEF-0028 unresolved-target-context backstop). Those
corrections were previously applied only to the in-memory `final_text`
variable feeding the LIVE SSE event -- `session_history_service.py`
reconstructed a turn's text independently, from the RAW, pre-correction
ADK-persisted event (`_extract_final_text`), so a refreshed/reopened
conversation could show a materially different answer than the one the
user actually saw live. `final_text` is now included, verbatim, in the
SAME per-turn entry this module already durably persists for Teams/KM
provenance -- ONE canonical object per turn, never a second, independently
-derived persistence structure that could drift from the first (see
`docs/INTELLIGENCE_ARCHITECTURE.md` §20b, invariant 7.7).

WHY THE SAME STATE KEY/ENTRY, NOT A NEW ONE: `TURN_SOURCE_REFERENCES_
STATE_KEY`'s existing `{turn_id: {...}}` shape, single write-before-
`MESSAGE_COMPLETED` ordering, and rewind-reversal correctness (this
module's own top docstring) are already exactly what a canonical text
result needs -- introducing a second, sibling state key would require a
second write (no longer atomic relative to the first, in the sense that
one could persist while the other fails) and a second rewind-correctness
argument to re-derive. Folding `final_text` into the SAME entry means
there really is one canonical object per turn, not two that must be kept
in sync by convention. `TURN_CANONICAL_RESULT_STATE_KEY` is a plain
alias for the SAME constant/string, for callers whose concern is the
canonical result rather than provenance specifically.

CANONICALIZATION BOUNDARY: `chat_service.py` builds this entry (via
`build_turn_source_references_delta`) only AFTER every existing
deterministic correction has already run (RequestContract validation +
execution policy, the KNOWLEDGE_INVENTORY override, `Troubleshooting
Guidance` enforcement, the unresolved-target-context backstop, and
Teams/governed-KM provenance assembly) and PERSISTS it BEFORE emitting
`MESSAGE_COMPLETED` -- see that call site's own comment for the exact
ordering. No later step may rewrite `final_text` after this point.

LEGACY SESSIONS: a turn completed before this milestone has no `final_
text` key in its persisted entry (or no entry at all) -- `resolve_
canonical_turn_result` returns `None` for it, and `session_history_
service.py`'s own get_session_history falls back to its pre-existing
raw-ADK-event-text + `resolve_turn_source_references` path exactly as it
already did before this milestone. No historical session is rewritten,
backfilled, or deleted.

CONFLICT/IDEMPOTENCY: the existing per-session lock
(`ApiSessionService.lock_for`, held for the FULL duration of
`_run_turn_events`) already serializes every turn -- write -- for one
session, so two turns can never race this same dict merge concurrently.
Each completed turn also has its own fresh ADK `invocation_id`, so no
natural "the same turn completes twice" path exists in this codebase.
`build_turn_source_references_delta` still defends against it directly,
as a belt-and-suspenders invariant, never assumed safe merely because no
caller currently exercises it: persisting the exact same COMPLETE
normalized canonical payload (`schema_version`, `final_text`, `source`,
`knowledge_sources`, `visual_evidence_internal` -- 6A.14A HARDENING PASS,
never `final_text` alone) for a `turn_id` that already has one is a
no-op (idempotent); persisting ANY material difference -- different
text, a changed/added/removed Teams source, a changed/added/removed
Knowledge source (including a changed `knowledge_id`/`version_label`/
`section_id`), a schema-version change, or a changed visual-evidence
binding -- raises `CanonicalTurnResultConflictError` rather than
silently overwriting one authoritative answer with another.

6A.14A HARDENING PASS -- FAIL-OPEN HISTORY GAP CLOSED: this module's own
per-turn `build_turn_failure_marker_delta`/`is_turn_marked_failed` were
found insufficient on their own -- if a turn's canonical-result
persistence failed AND the best-effort failure-marker write ALSO failed,
history's old "absence means legacy" rule could not distinguish that
double-failure from a genuinely pre-6A.14A turn, letting the ADK
Runner's own already-durably-appended raw final-response event resurface
through the legacy fallback. Closed with a POSITIVE, durable, session-
level marker (`CANONICAL_RESULT_ENFORCEMENT_STATE_KEY`, established
BEFORE any Runner call for the turn -- see that constant's own docstring
for the full design) plus a single classification function (`resolve_
canonical_turn_state`) `session_history_service.py` now consults for
every canonical-required turn -- absence of a valid result for such a
turn is ALWAYS a fail-closed exclusion now, never a silent fallback to
raw text, regardless of whether the failure marker itself ever
successfully persisted.
"""
from __future__ import annotations

from typing import Any, NamedTuple, Optional

from pydantic import BaseModel, ConfigDict, Field

from backend.api.knowledge_source_reference import dedupe_knowledge_source_references
from backend.api.schemas import KnowledgeSourceReferenceDTO, SourceReferenceDTO

TURN_SOURCE_REFERENCES_STATE_KEY = "turn_source_references"
"""Plain (never `temp:`-prefixed) session-state key -- must survive past
the one turn that wrote it, unlike the `temp:` scratch values elsewhere in
this codebase. Value shape: `{turn_id: {"schema_version"?: str,
"final_text"?: str, "source"?: <SourceReferenceDTO JSON>,
"knowledge_sources"?: [<KnowledgeSourceReferenceDTO JSON>, ...]}}`.
`turn_id` is the ADK `invocation_id` -- the SAME identity `session_
history_service.py`'s `SessionHistoryMessageDTO.turn_id` already uses,
never a newly-invented one.
"""

TURN_CANONICAL_RESULT_STATE_KEY = TURN_SOURCE_REFERENCES_STATE_KEY
"""Alias for `TURN_SOURCE_REFERENCES_STATE_KEY` -- the identical
persisted session-state key, named for 6A.14A callers whose concern is
the canonical turn result (text + provenance) rather than provenance
alone. Never a second, independently-written key."""

CANONICAL_TURN_RESULT_SCHEMA_VERSION = "1.0"

CANONICAL_RESULT_ENFORCEMENT_STATE_KEY = "canonical_result_enforcement_active"
"""6A.14A HARDENING PASS -- the positive, durable, session-level marker
that closes the fail-open history gap a plain per-turn failure-marker
(`build_turn_failure_marker_delta`, still useful as additional
diagnostics) could not close on its own: IF the failure-marker write
ITSELF also fails after canonical-result persistence already failed, the
turn would previously have no entry of any kind under `TURN_SOURCE_
REFERENCES_STATE_KEY` -- indistinguishable, by the old "absence means
legacy" rule, from a genuinely pre-6A.14A turn that never even attempted
the canonical-result contract.

WHY SESSION-LEVEL, NOT PER-TURN: the turn's own ADK `invocation_id` is
not known until the Runner's first event (`chat_service.py`'s own
`first_event_seen` capture) -- by which point the Runner is already
actively producing events for this SAME invocation, and writing session
state at that point is a PROVEN-UNSAFE pattern in this codebase (see
`ChatService._finalize_user_turn_activity`'s own B4B incident docstring:
an external `append_event` call while the Runner's own generator is
still active corrupts ADK's session-revision tracking out from under the
Runner's own internal session handle). This marker is therefore written
ONCE per session, BEFORE any Runner call of any kind for the turn even
begins (`chat_service.py`'s own `_run_turn_events`, immediately after the
turn's own session is first loaded) -- an ordinary, already-proven-safe
pre-Runner state write, exactly like `pending_read_continuation`'s own
pop-then-repersist a few lines later in that same method. Once durably
`True`, EVERY later turn in that session is governed by the canonical-
result contract, permanently -- this key is never cleared/reset.

WHY POSITION IN `session.events`, NEVER A TIMESTAMP: `session_history_
service.py`'s own event walk (`_project_turns`) observes every event,
including this marker's own state-delta event, in the SAME real,
ADK-durable append ORDER rewind itself already treats as authoritative
(this module's own top docstring). A turn is CANONICAL-REQUIRED if and
only if this marker's own event was already observed, in that walk,
before the turn's own first event -- a genuinely legacy turn (this
marker's event does not yet exist, or exists only later in the event
list) is completely unaffected. No clock, no wall-time comparison, no
process-local memory -- purely durable event order, safe under restart,
clock skew, and an arbitrarily old session.

IF THIS MARKER ITSELF CANNOT BE ESTABLISHED (the one write fails):
`chat_service.py` fails the turn closed BEFORE invoking any Runner at
all -- no specialist/model call, no raw assistant final-response event
can ever be appended for that attempt, so there is structurally nothing
for a later "legacy fallback" to find."""


class CanonicalTurnResultConflictError(RuntimeError):
    """Raised by `build_turn_source_references_delta` when a caller
    attempts to persist a DIFFERENT `final_text` for a `turn_id` that
    already has a persisted canonical result -- see this module's own
    "CONFLICT/IDEMPOTENCY" docstring section. `chat_service.py` treats
    this identically to any other canonical-persistence failure: the
    turn fails closed, `MESSAGE_COMPLETED` is never emitted, and a safe
    `ERROR` event is yielded instead."""


class CanonicalTurnResult(BaseModel):
    """The one authoritative, typed, durably persisted result for a
    successfully completed turn (6A.14A/DEF-0031) -- the SAME object the
    live `MESSAGE_COMPLETED` SSE event and refreshed/reopened session
    history both derive from. Contains only validated, user-visible
    projection data plus the minimum identity/version information needed
    to use it safely -- never thought parts, raw tool payloads, secrets,
    or untrusted model metadata (none of those fields exist on this
    model, structurally)."""

    model_config = ConfigDict(frozen=True)

    schema_version: str = CANONICAL_TURN_RESULT_SCHEMA_VERSION
    turn_id: str
    text: str
    source: Optional[SourceReferenceDTO] = None
    knowledge_sources: list[KnowledgeSourceReferenceDTO] = Field(default_factory=list)


def build_turn_source_references_delta(
    existing: Any,
    turn_id: str,
    source: Optional[SourceReferenceDTO],
    knowledge_sources: list[KnowledgeSourceReferenceDTO],
    visual_evidence_internal: Optional[list[dict[str, Any]]] = None,
    final_text: Optional[str] = None,
) -> Optional[dict[str, Any]]:
    """Returns the FULL updated value to persist for `TURN_SOURCE_
    REFERENCES_STATE_KEY`, or `None` when this turn has nothing worth
    persisting at all (no Teams source, no selected KM evidence, no
    canonical text) -- a turn with nothing to persist must never
    fabricate/write an empty placeholder entry.

    `existing` is the CURRENT raw value of the state key (already a
    `{turn_id: {...}}` dict in normal operation, but defensively accepted
    as `Any` and treated as empty if it is not actually a dict -- a
    single corrupted/foreign value must never crash a turn that otherwise
    completed successfully).

    `final_text` (6A.14A/DEF-0031) -- this turn's own already fully
    corrected, canonical response text (the SAME value the live
    `MESSAGE_COMPLETED` event carries), or `None` for a turn that has
    nothing canonical to persist (e.g. the pre-6A.14A call shape some
    tests still exercise directly). When provided, this is what makes
    the persisted entry a genuine 6A.14A canonical result rather than
    provenance-only data -- see `resolve_canonical_turn_result` below.

    6A.14A HARDENING PASS -- FULL CANONICAL-PAYLOAD IMMUTABILITY: when
    `final_text` is provided and `turn_id` already has a persisted
    canonical entry, the COMPLETE normalized entry (`schema_version`,
    `final_text`, `source`, `knowledge_sources`, `visual_evidence_
    internal` -- every field this function itself persists, never only
    `final_text`) is compared against the existing one. Identical on
    every field -> safe idempotent no-op. ANY material difference --
    different text, a source added/removed/changed, a Knowledge source
    added/removed/changed (including a changed `knowledge_id`/`version_
    label`/`section_id`), a schema-version change, a changed visual-
    evidence binding -- raises `CanonicalTurnResultConflictError` rather
    than silently overwriting one authoritative answer with another.
    `source`/`knowledge_sources` arrive HERE already normalized by the
    caller (`chat_service.py` already applies `dedupe_knowledge_source_
    references` before calling this function, the SAME ordering/identity
    rule used everywhere else in this codebase -- see that module's own
    call-site comment) -- this function performs no additional fuzzy/
    heuristic normalization of its own; plain structural equality on the
    already-canonical JSON shape is the entire comparison, deliberately
    never comparing only titles/counts/section headings.

    `visual_evidence_internal` (Teams Visual Evidence milestone) -- a
    plain list of `{"image_id", "chat_id", "message_id",
    "hosted_content_id"}` dicts, ONE per image `source.visual_evidence`
    (the PUBLIC DTO) also carries this turn, same order, `image_id`
    values matching 1:1. Stored under a SEPARATE key
    (`visual_evidence_internal`) in the SAME per-turn entry as `source`,
    so it inherits the exact same durability/rewind-reversal guarantee
    for free (see this module's own top docstring) -- but
    `resolve_turn_source_references` below NEVER reads it back; only
    `resolve_visual_evidence_binding` does, and that function is used
    exclusively by the authenticated Source visual-evidence HTTP content
    endpoint (`source_images.py`), never by the history projection that
    builds the PUBLIC DTO the frontend receives. Omitted when empty/None
    -- a turn with no delivered images writes no such key, exactly
    mirroring `knowledge_sources`'s own "omit when empty" discipline.
    """
    if source is None and not knowledge_sources and final_text is None:
        return None

    base = existing if isinstance(existing, dict) else {}

    entry: dict[str, Any] = {}
    if final_text is not None:
        entry["schema_version"] = CANONICAL_TURN_RESULT_SCHEMA_VERSION
        entry["final_text"] = final_text
    if source is not None:
        entry["source"] = source.model_dump(mode="json")
    if knowledge_sources:
        entry["knowledge_sources"] = [item.model_dump(mode="json") for item in knowledge_sources]
    if visual_evidence_internal:
        entry["visual_evidence_internal"] = list(visual_evidence_internal)

    if final_text is not None:
        existing_entry = base.get(turn_id)
        if isinstance(existing_entry, dict) and isinstance(existing_entry.get("final_text"), str):
            # 6A.14A HARDENING PASS -- `entry` (built above) and
            # `existing_entry` are built by the exact same code path, so
            # a direct structural comparison IS the complete normalized-
            # payload comparison (schema_version, final_text, source,
            # knowledge_sources, visual_evidence_internal all included,
            # each already omitted identically by both when empty/None).
            # This branch is structurally unreachable for a failure-
            # marker entry (`build_turn_failure_marker_delta` never sets
            # a real string `final_text`), so `"failed"` never appears on
            # either side of this comparison.
            if existing_entry != entry:
                raise CanonicalTurnResultConflictError(
                    f"turn {turn_id!r} already has a persisted canonical result with a "
                    "different normalized payload; refusing to silently overwrite it"
                )

    return {**base, turn_id: entry}


def resolve_canonical_turn_result(state: Any, turn_id: str) -> Optional[CanonicalTurnResult]:
    """6A.14A/DEF-0031 -- returns the durably persisted canonical result
    for one turn (text + provenance, all from the SAME entry `build_turn_
    source_references_delta` wrote), or `None` when this turn has no
    persisted canonical text -- either a legacy (pre-6A.14A) session, or a
    turn whose canonical persistence itself failed closed (in which case
    `chat_service.py` never announced the turn as completed in the first
    place -- see that module's own "PERSIST BEFORE ANNOUNCE" comment).
    Callers needing a text fallback for a legacy turn must fall back to
    raw ADK-event-derived text themselves (see `session_history_service
    .py`'s own `get_session_history`) -- this function never fabricates a
    canonical result that was not actually persisted, and never re-derives
    text from anything other than the exact stored value.

    Defensive throughout, mirroring `resolve_turn_source_references`'s own
    tolerance exactly: a missing key, a missing entry for this `turn_id`,
    a non-string `final_text`, or a value that fails `CanonicalTurnResult`
    construction all resolve to `None`, never raised.
    """
    all_entries = state.get(TURN_SOURCE_REFERENCES_STATE_KEY) if hasattr(state, "get") else None
    if not isinstance(all_entries, dict):
        return None
    entry = all_entries.get(turn_id)
    if not isinstance(entry, dict):
        return None

    raw_text = entry.get("final_text")
    if not isinstance(raw_text, str):
        return None

    source: Optional[SourceReferenceDTO] = None
    raw_source = entry.get("source")
    if isinstance(raw_source, dict):
        try:
            source = SourceReferenceDTO.model_validate(raw_source)
        except Exception:
            source = None

    knowledge_sources: list[KnowledgeSourceReferenceDTO] = []
    raw_knowledge_sources = entry.get("knowledge_sources")
    if isinstance(raw_knowledge_sources, list):
        for raw_item in raw_knowledge_sources:
            if not isinstance(raw_item, dict):
                continue
            try:
                knowledge_sources.append(KnowledgeSourceReferenceDTO.model_validate(raw_item))
            except Exception:
                continue

    raw_schema_version = entry.get("schema_version")
    schema_version = raw_schema_version if isinstance(raw_schema_version, str) else CANONICAL_TURN_RESULT_SCHEMA_VERSION

    try:
        return CanonicalTurnResult(
            schema_version=schema_version,
            turn_id=turn_id,
            text=raw_text,
            source=source,
            knowledge_sources=dedupe_knowledge_source_references(knowledge_sources),
        )
    except Exception:
        return None


def build_turn_failure_marker_delta(existing: Any, turn_id: str) -> dict[str, Any]:
    """6A.14A -- best-effort marker written when a turn's OWN canonical-
    result persistence attempt itself failed (`chat_service.py`'s own
    persist-before-announce failure path, `ChatService._best_effort_mark_
    turn_failed`), so a legacy-fallback history projection can never
    display this turn's raw, uncorrected ADK event text as though it were
    a real completed answer -- DEF-0031's own "no contradictory history"
    requirement, extended to the persistence-failure case specifically
    (the ADK Runner's own final-response event is already durably
    appended by the time this module's own write ever runs, independent
    of whether THIS write succeeds).

    Always overwrites any existing entry for this `turn_id` -- a turn
    that reaches this path never had a successful canonical result to
    protect (persistence for THIS turn already failed by construction, so
    there is nothing valid under this key to preserve).
    """
    base = existing if isinstance(existing, dict) else {}
    return {**base, turn_id: {"schema_version": CANONICAL_TURN_RESULT_SCHEMA_VERSION, "failed": True}}


def is_canonical_result_enforcement_marker_event(event: Any) -> bool:
    """True for the ONE state-delta event `chat_service.py` writes, once
    per session, BEFORE any Runner call, to durably establish
    `CANONICAL_RESULT_ENFORCEMENT_STATE_KEY = True` -- see that
    constant's own docstring for the full design. `session_history_
    service.py`'s own event walk uses this to positively distinguish a
    genuinely legacy turn (this event does not yet exist, or exists only
    LATER in `session.events`) from a canonical-required one (this event
    already exists EARLIER), by real, durable event ORDER -- never a
    timestamp, never process-local memory. Defensive: any event lacking
    `.actions.state_delta` (the overwhelming majority -- ordinary
    content-bearing events) safely returns `False`, never raises."""
    actions = getattr(event, "actions", None)
    state_delta = getattr(actions, "state_delta", None) if actions is not None else None
    if not isinstance(state_delta, dict):
        return False
    return state_delta.get(CANONICAL_RESULT_ENFORCEMENT_STATE_KEY) is True


class CanonicalTurnStatus:
    """Closed classification vocabulary for `resolve_canonical_turn_
    state` -- meaningful ONLY for a turn the caller has already
    determined, by event order (`is_canonical_result_enforcement_marker_
    event`), to be canonical-required. Never applied to a genuinely
    legacy turn, which takes an entirely separate, unaffected code path
    in `session_history_service.py`."""

    VALID = "valid"
    """A real, successfully persisted, structurally valid canonical
    result exists -- render it."""
    FAILED = "failed"
    """Explicitly marked failed (`build_turn_failure_marker_delta`) --
    never render raw ADK text; exclude this turn's assistant message."""
    ABSENT = "absent"
    """Canonical-required, but NEITHER a valid result NOR a failure
    marker exists (the exact double-persistence-failure gap this
    hardening pass closes) -- fail closed, never render raw ADK text."""
    MALFORMED = "malformed"
    """An entry exists but cannot be parsed into a valid result and is
    not explicitly marked failed (corrupted/unexpected persisted shape)
    -- fail closed, log safely, never render raw ADK text."""
    CONFLICTING = "conflicting"
    """An entry claims BOTH a resolvable `final_text` AND `failed=True`
    simultaneously -- a structural contradiction that must never occur
    from this module's own writers (each overwrites the whole entry, never
    merges) but is checked defensively regardless -- fail closed, log
    safely, never render raw ADK text."""


class CanonicalTurnState(NamedTuple):
    """The ONE classification result `session_history_service.py` acts
    on for a canonical-required turn -- `result` is populated only when
    `status == CanonicalTurnStatus.VALID`."""

    status: str
    result: Optional[CanonicalTurnResult]


def resolve_canonical_turn_state(state: Any, turn_id: str) -> CanonicalTurnState:
    """6A.14A HARDENING PASS -- the single, deterministic classification
    function for a turn already known to be canonical-required. Combines
    what `resolve_canonical_turn_result`/`is_turn_marked_failed` already
    read into ONE decision (valid / failed / absent / malformed /
    conflicting) so this logic lives in exactly one place, never
    distributed as ad hoc conditionals across `session_history_
    service.py` and any future caller.
    """
    all_entries = state.get(TURN_SOURCE_REFERENCES_STATE_KEY) if hasattr(state, "get") else None
    entry = all_entries.get(turn_id) if isinstance(all_entries, dict) else None
    if not isinstance(entry, dict):
        return CanonicalTurnState(CanonicalTurnStatus.ABSENT, None)

    is_failed = entry.get("failed") is True
    raw_text = entry.get("final_text")
    has_text = isinstance(raw_text, str)

    if is_failed and has_text:
        return CanonicalTurnState(CanonicalTurnStatus.CONFLICTING, None)
    if is_failed:
        return CanonicalTurnState(CanonicalTurnStatus.FAILED, None)
    if not has_text:
        return CanonicalTurnState(CanonicalTurnStatus.MALFORMED, None)

    result = resolve_canonical_turn_result(state, turn_id)
    if result is None:
        return CanonicalTurnState(CanonicalTurnStatus.MALFORMED, None)
    return CanonicalTurnState(CanonicalTurnStatus.VALID, result)


def is_turn_marked_failed(state: Any, turn_id: str) -> bool:
    """True only for a turn explicitly marked via `build_turn_failure_
    marker_delta` -- i.e. a turn whose own canonical-result persistence
    attempt failed. `session_history_service.py`'s `get_session_history`
    uses this to exclude such a turn from the projected transcript
    entirely, rather than falling back to its raw, uncorrected ADK event
    text. Defensive throughout: any malformed/missing data resolves to
    `False`, never raised -- a genuinely legacy (pre-6A.14A) turn, which
    never has ANY entry under this key, also correctly resolves `False`
    here (it takes the ordinary legacy-fallback path instead, unaffected
    by this function)."""
    all_entries = state.get(TURN_SOURCE_REFERENCES_STATE_KEY) if hasattr(state, "get") else None
    if not isinstance(all_entries, dict):
        return False
    entry = all_entries.get(turn_id)
    if not isinstance(entry, dict):
        return False
    return entry.get("failed") is True


class VisualEvidenceBinding(NamedTuple):
    """The real, durable provenance triple a public, opaque `image_id`
    resolves to -- NEVER sent to the frontend (see `source_images.py`,
    the sole consumer of `resolve_visual_evidence_binding`)."""

    chat_id: str
    message_id: str
    hosted_content_id: str


def resolve_visual_evidence_binding(state: Any, source_id: str, image_id: str) -> Optional[VisualEvidenceBinding]:
    """Teams Visual Evidence milestone -- scans every turn currently
    present in `TURN_SOURCE_REFERENCES_STATE_KEY` (already naturally
    rewind-correct: a discarded turn's entry is simply absent, per ADK's
    own state-delta reversal -- see this module's own top docstring) for
    one whose persisted `source.source_id` matches `source_id`, then looks
    up `image_id` within that SAME turn's own `visual_evidence_internal`
    list. Returns `None` for an unknown `source_id`, an unknown
    `image_id`, an `image_id` that belongs to a DIFFERENT source/turn, or
    any malformed data at any level -- never raises, mirrors `resolve_
    turn_source_references`'s own defensive tolerance exactly. The caller
    (`source_images.py`) is responsible for turning a `None` result into a
    generic, anti-enumeration-safe `not_found` -- this function itself
    never distinguishes "wrong source_id" from "wrong image_id" in its
    return value, by construction (both are simply `None`).
    """
    all_entries = state.get(TURN_SOURCE_REFERENCES_STATE_KEY) if hasattr(state, "get") else None
    if not isinstance(all_entries, dict):
        return None

    for entry in all_entries.values():
        if not isinstance(entry, dict):
            continue
        raw_source = entry.get("source")
        if not isinstance(raw_source, dict) or raw_source.get("source_id") != source_id:
            continue
        raw_bindings = entry.get("visual_evidence_internal")
        if not isinstance(raw_bindings, list):
            return None
        for raw_binding in raw_bindings:
            if not isinstance(raw_binding, dict):
                continue
            if raw_binding.get("image_id") != image_id:
                continue
            chat_id = raw_binding.get("chat_id")
            message_id = raw_binding.get("message_id")
            hosted_content_id = raw_binding.get("hosted_content_id")
            if not (isinstance(chat_id, str) and isinstance(message_id, str) and isinstance(hosted_content_id, str)):
                return None
            return VisualEvidenceBinding(chat_id=chat_id, message_id=message_id, hosted_content_id=hosted_content_id)
        return None

    return None


def resolve_turn_source_references(
    state: Any, turn_id: str
) -> tuple[Optional[SourceReferenceDTO], list[KnowledgeSourceReferenceDTO]]:
    """Reads back exactly one turn's own persisted provenance from a
    session's state, for `session_history_service.py`'s history
    projection. Safe/defensive throughout -- a missing key, a missing
    entry for this `turn_id`, or a malformed value at any level is
    treated as "nothing persisted for this turn" (returns `(None, [])`),
    never raised -- mirrors `backend.selection.service.load_active_
    selection`'s own tolerance for exactly the same class of "this is
    trusted backend data, but never trust its shape blindly on the way
    back out" defensiveness.
    """
    all_entries = state.get(TURN_SOURCE_REFERENCES_STATE_KEY) if hasattr(state, "get") else None
    if not isinstance(all_entries, dict):
        return None, []
    entry = all_entries.get(turn_id)
    if not isinstance(entry, dict):
        return None, []

    source: Optional[SourceReferenceDTO] = None
    raw_source = entry.get("source")
    if isinstance(raw_source, dict):
        try:
            source = SourceReferenceDTO.model_validate(raw_source)
        except Exception:
            source = None

    knowledge_sources: list[KnowledgeSourceReferenceDTO] = []
    raw_knowledge_sources = entry.get("knowledge_sources")
    if isinstance(raw_knowledge_sources, list):
        for raw_item in raw_knowledge_sources:
            if not isinstance(raw_item, dict):
                continue
            try:
                knowledge_sources.append(KnowledgeSourceReferenceDTO.model_validate(raw_item))
            except Exception:
                continue

    return source, dedupe_knowledge_source_references(knowledge_sources)
