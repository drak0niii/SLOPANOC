"""DEF-0026 corrective pass -- Governed Knowledge Follow-Up Identity
Continuity.

THE GAP THIS CLOSES (see the Phase 6A Live Acceptance audit that
preceded this pass for the full live trace): `backend/api/chat_service
.py`'s deterministic governed-knowledge completion remediation
(`enforce_governed_knowledge_at_completion`,
`backend/agents/team_manager/governed_knowledge_completion.py`) builds a
fresh `IncidentManagerRequest` from `_remediation_question(message_text)`
-- the raw current-turn user text, VERBATIM, with no chat/procedure
context. For an ordinary, richly-worded request this is fine. For a
short, context-poor follow-up ("give me the first cmd") that names no
procedure by itself, this bypasses team_manager's own "GOVERNED-
KNOWLEDGE FOLLOW-UP CONTINUITY" prompt paragraph entirely (that
paragraph only has a chance to run during team_manager's OWN first
delegation attempt -- never during this deterministic, Python-authored
remediation, which exists precisely because that first attempt already
failed to leave verifiable evidence behind). Empirically reproduced
twice against the real corpus: the remediation's own incident_manager
sub-run either (a) fails closed after its own bounded compliance retry
exhausts itself (the "Governed knowledge could not be validated"
symptom), or (b) confidently selects a real, genuinely grounded command
from the WRONG sibling procedure -- DEF-0024's own procedure-scoped
command-grounding safeguard correctly does not strip it, because it IS
verbatim-grounded in the section that was (wrongly) selected. Neither
outcome is a provenance/grounding defect; both are a lost-topic-identity
defect at this one specific remediation boundary.

THE ONE THING THIS MODULE DOES: lets that ONE remediation boundary
recover the STABLE IDENTITY (never the prose) of governed Knowledge
evidence a PRIOR, genuinely successful turn in this SAME session actually
selected -- so a context-poor follow-up can be scoped to that same real,
revalidated procedure instead of a blind, topic-free search across an
entire governed document. This is explicitly NOT: canonical TELCO
Context, Experience Memory, a general conversational-memory subsystem,
or a second competing selection mechanism. SEARCH RESULT != EVIDENCE
USED remains completely intact -- this module never selects evidence
itself, never fabricates a `knowledge_select_evidence` call, and never
lets stored identity bypass a real `knowledge_search`/`knowledge_select_
evidence` round trip; it only ever changes what QUESTION TEXT the
remediation's own real incident_manager sub-run receives, and separately,
decides (deterministically, from real repository state) when a stored
identity is even still safe to reference at all.

STATE SHAPE, MIRRORING `state_sync.py`'s OWN "PLAIN, OVERWRITABLE VALUE"
PATTERN (`selected_teams_chat_id`/`selected_teams_chat_topic`) -- NOT
`turn_source_references.py`'s own per-turn ACCUMULATING dict, since this
value is conceptually "the current governed-evidence continuity anchor",
not a durable per-turn history record: `LAST_SELECTED_GOVERNED_EVIDENCE_
STATE_KEY` holds a plain JSON list of already-existing, closed-schema
`KnowledgeEvidenceSelectionKey` dicts (`knowledge_id`/`version_label`/
`section_id` only -- the SAME minimum-identity contract 5.1I/5.1J already
established; no new identity shape is invented here). Reused directly,
never duplicated or widened.

AUTHORITATIVE WRITE BOUNDARY: `build_last_selected_governed_evidence_
state_update` is a pure function over `selected_knowledge_evidence` --
the SAME trusted, run-scoped, already-provenance-validated list `chat_
service.py` already snapshots via `snapshot_selected_knowledge_evidence`
and already uses to build `knowledge_sources` for the live SSE event
(see chat_service.py's own "Phase 5.1J correction pass (Part C)"
comment). Never populated from available-but-unselected evidence, model
prose, or a failed/remediation-only turn -- an empty `selected_knowledge_
evidence` list (the only way either of those failure modes could look
from this function's own input) returns `{}` (an explicit "leave prior
state exactly as it is" no-op), mirroring `state_sync.compute_state_
updates`'s own "empty dict means nothing to change" contract exactly --
a failed/non-governed turn can never blank out a previously valid
continuity anchor.

REVALIDATION, NEVER BLIND TRUST: `revalidate_prior_governed_evidence`
re-fetches each stored `(knowledge_id, version_label, section_id)` from
the REAL, live `KnowledgeRepository` and re-runs the REAL, unmodified
5.1E `resolve_current_version` before ever treating it as usable --
confirms the `KnowledgeObject` still exists, is still `APPROVED`, is
STILL the currently-resolved version for its own `knowledge_id` (not
superseded since it was stored), and that `section_id` still names a
real section within it. A version/section that fails ANY of these checks
is silently dropped from the result (fails closed for THAT one entry,
never for the whole list) -- this module never raises merely because
prior identity has gone stale; it simply stops trusting it.

DEDUPLICATION AND AMBIGUITY: results are deduplicated by the exact
`(knowledge_id, version_label, section_id)` identity (never by heading/
title text, which can legitimately collide). Zero surviving entries means
"no valid prior identity" (never a guess). Exactly one surviving entry is
the single, safe scoping anchor. More than one DISTINCT surviving entry
is genuinely ambiguous -- `build_ambiguous_procedure_clarification`
returns a deterministic clarification built ONLY from each entry's own
real, governed `heading`/`title` (never a model-invented procedure name),
and the caller must never guess between them.

EXPLICIT TOPIC CHANGE MUST WIN, STRUCTURALLY, NOT ONLY VIA PROMPT
COMPLIANCE: `detect_explicit_sibling_topic_override` performs one plain,
case-insensitive VERBATIM SUBSTRING check of the raw current-turn
question against every OTHER real section heading already known to exist
in the SAME governed `KnowledgeObject` (collected during revalidation,
see `RevalidatedGovernedProcedure.sibling_headings`) -- never the model's
own suggestion, never a keyword/intent list, never `re`. When a sibling
heading is found verbatim in the current question, this module reports
the override and the caller skips scoping entirely, letting the ordinary,
unscoped remediation path run exactly as it always has -- a real, live
example: "how do i handle HW Partial Fault?" verbatim-contains the real
sibling heading "HW Partial Fault" already present in `A5-VALIDATION-
DOCUMENT1`'s own governed structure, so a stored VSWR-procedure identity
is never used to scope that turn. `build_scoped_remediation_question`'s
own deterministic template ALSO tells the model, in fixed wording, that
an explicitly-named different procedure in the CURRENT question always
takes priority over the appended continuity note -- a second, prompt-
level layer of the same rule, never the only one.

NO NLP/KEYWORD ROUTING: no `re` import, no keyword/intent-phrase list,
no `.lower()` call on raw free-form conversation prose for routing
purposes anywhere in this module -- every substring check here compares
the current question only against REAL, governed section headings
already retrieved from the authoritative repository, never against a
fixed vocabulary this module invents.

===================================================================
6A.14 ACTIVE PROCEDURE CONTINUITY CORRECTION (added after the above,
does not modify any of it)
===================================================================

THE GAP THIS CLOSES: the DEF-0026/DEF-0027 machinery above already
tracks every distinct governed-evidence identity a turn selected
(`LAST_SELECTED_GOVERNED_EVIDENCE_STATE_KEY`), but treats them all as
equally authoritative for continuity -- a turn that selects one ACTIVE
procedure (e.g. "HW Partial Fault", genuinely named by the user) ALONGSIDE
a merely SUPPORTING sibling section (e.g. "HW Fault", consulted for
context) persists BOTH as equal candidates. A later, genuinely
unambiguous follow-up ("it's an RRU", or even the EXACT heading "HW
Partial Fault" again) then hits `len(effective_candidates) > 1` and is
asked to disambiguate between two options the user already, in effect,
resolved once. Live-reproduced and traced in the corresponding audit.

THE NEW CONCEPT: `ACTIVE_GOVERNED_PROCEDURE_STATE_KEY` persists exactly
ONE `KnowledgeEvidenceSelectionKey` -- never a list -- the single,
authoritative governed procedure a continuation should anchor to, kept
SEPARATE from the existing, unmodified `LAST_SELECTED_GOVERNED_EVIDENCE_
STATE_KEY` (which continues to hold every distinct selected identity,
supporting evidence included, for provenance/history purposes -- nothing
about that key's own meaning or consumers changes).

WRITE BOUNDARY (`compute_fresh_active_procedure_anchor`): reuses
`backend.agents.incident_manager.evidence.resolve_active_section_id`
(DEF-0024/0027's own existing, unmodified, pure turn-local resolver) --
never a second, parallel algorithm -- applied to THIS turn's own fresh,
trusted `selected_knowledge_evidence` and THIS turn's own raw current
question text. Returns `None` (never overwrite the existing anchor)
whenever zero or more than one section was selected and the question does
not uniquely resolve one -- mirrors `build_last_selected_governed_
evidence_state_update`'s own "absent/ambiguous result is a no-op, never a
destructive overwrite" contract exactly. A turn that never established a
new, uniquely-resolved active procedure simply leaves whatever anchor a
PRIOR turn already validly established untouched -- this is precisely
what lets "it's an RRU" (which cannot itself resolve an active procedure
from its own text alone) continue to rely on the anchor Turn 1 already
set, without needing to re-establish it.

READ BOUNDARY / PRECEDENCE (`resolve_active_candidate_among_ambiguous`,
consumed by `governed_knowledge_completion.enforce_governed_knowledge_at_
completion` ONLY when the pre-existing DEF-0026 revalidation above already
found more than one genuinely ambiguous candidate): a three-step,
purely-deterministic narrowing chain, each step operating on the SAME
already-revalidated candidate set the ambiguity check itself already
computed -- never a fabricated/different identity, never fuzzy/semantic
matching:

  1. EXPLICIT CURRENT USER SUBJECT (`resolve_explicit_current_candidate`)
     -- does the current turn's own raw text verbatim, case-insensitively
     name exactly ONE of the already-ambiguous candidates' own real
     headings? Deliberately a SEPARATE function from `detect_explicit_
     sibling_topic_override` above, whose own job is the opposite:
     finding a heading OUTSIDE the candidate set to justify abandoning
     all of them. This one looks WITHIN the set, letting the user
     directly pick among options the system itself is presenting.
  2. VALIDATED CURRENT-TURN `RequestContract.subject` (6A.13, already
     provenance-verified by `validate_and_persist_request_contract`
     before it ever reaches this module) -- does it deterministically
     match exactly one candidate's own heading (exact match, or the real
     heading text appearing verbatim inside the subject)?
  3. EXISTING, RE-VALIDATED ACTIVE PROCEDURE ANCHOR -- is the caller-
     supplied, already-revalidated `active_anchor` itself one of the
     candidates?

  None of the three resolving -> the request remains genuinely
  ambiguous, and the pre-existing `build_ambiguous_procedure_
  clarification` path is used exactly as before this correction.

PRECEDENCE INVARIANT: supporting evidence never outranks an established
active procedure or an explicit current signal -- it is consulted only
implicitly, as one of the (now-narrowed-away) ambiguous candidates,
never as an independent resolution source of its own.

NOT A SECOND SELECTION MECHANISM: none of this reads/writes Approved
governed content, calls `knowledge_search`/`knowledge_select_evidence`,
or changes what evidence a future turn's own real `incident_manager`
execution may select -- it only ever changes (a) which durable identity
this module treats as the authoritative continuation anchor, and (b)
which QUESTION TEXT a remediation attempt is scoped to. SEARCH RESULT !=
EVIDENCE USED remains completely intact.

DOES NOT CLAIM MULTI-ACTIVE-PROCEDURE SUPPORT: exactly one active
procedure is tracked per continuation chain, matching this milestone's
own explicit Phase-6A scope -- a future architecture that genuinely
needs more than one simultaneously active procedure (e.g. a multi-Case
concurrent-fault scenario) is out of scope here and must fail safely
(the existing clarification path) rather than silently pick one.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Optional, Sequence

from backend.knowledge.domain.enums import LifecycleStatus
from backend.knowledge.domain.models import KnowledgeObject
from backend.knowledge.governance.versioning import resolve_current_version
from backend.knowledge.provenance.contracts import KnowledgeEvidenceItem, KnowledgeEvidenceSelectionKey
from backend.knowledge.repository.contracts import KnowledgeRepository

_logger = logging.getLogger(__name__)

LAST_SELECTED_GOVERNED_EVIDENCE_STATE_KEY = "last_selected_governed_evidence"


def _evidence_identity(item: KnowledgeEvidenceItem) -> tuple[str, str, Optional[str]]:
    return (item.reference.knowledge_id, item.reference.version_label, item.reference.section_id)


def build_last_selected_governed_evidence_state_update(
    selected_evidence: Sequence[KnowledgeEvidenceItem],
) -> dict[str, Any]:
    """Pure function: `selected_evidence` is the SAME trusted, already-
    provenance-validated list `chat_service.py` snapshots for THIS turn
    (win or lose -- an empty list correctly covers both "nothing selected
    this turn" and "this turn didn't need governed knowledge at all").
    Returns `{}` (no state change) whenever `selected_evidence` is empty
    -- a failed or non-governed turn NEVER overwrites a previously valid
    continuity anchor. Deduplicates by exact identity, preserving first-
    seen order (mirrors `dedupe_knowledge_source_references`'s own
    established discipline).
    """
    if not selected_evidence:
        return {}

    seen: set[tuple[str, str, Optional[str]]] = set()
    keys: list[KnowledgeEvidenceSelectionKey] = []
    for item in selected_evidence:
        identity = _evidence_identity(item)
        if identity in seen:
            continue
        seen.add(identity)
        keys.append(
            KnowledgeEvidenceSelectionKey(
                knowledge_id=item.reference.knowledge_id,
                version_label=item.reference.version_label,
                section_id=item.reference.section_id,
            )
        )

    if not keys:
        return {}
    return {LAST_SELECTED_GOVERNED_EVIDENCE_STATE_KEY: [key.model_dump(mode="json") for key in keys]}


def parse_last_selected_governed_evidence(raw: Any) -> list[KnowledgeEvidenceSelectionKey]:
    """Tolerant, fail-closed parse of the raw session-state value back
    into typed keys -- absent/malformed/wrong-shaped data all resolve to
    an empty list (never a raised exception, never a partially-trusted
    guess), exactly like every other session-state reader in this
    codebase.
    """
    if not isinstance(raw, list):
        return []
    keys: list[KnowledgeEvidenceSelectionKey] = []
    for entry in raw:
        try:
            keys.append(KnowledgeEvidenceSelectionKey.model_validate(entry))
        except Exception:
            continue
    return keys


@dataclass(frozen=True)
class RevalidatedGovernedProcedure:
    """One stored identity that was successfully re-confirmed, just now,
    against the real, live `KnowledgeRepository` -- never a cached or
    assumed fact. `heading`/`title` are real, governed text (never
    model-invented) safe to use in a deterministic clarification or a
    scoped remediation question. `sibling_headings` are every OTHER real
    section heading already present in the SAME `KnowledgeObject` -- used
    only for the structural "explicit topic change" override check
    below, never displayed on their own.
    """

    knowledge_id: str
    version_label: str
    section_id: Optional[str]
    title: str
    heading: Optional[str]
    sibling_headings: tuple[str, ...]


async def revalidate_prior_governed_evidence(
    keys: Sequence[KnowledgeEvidenceSelectionKey], repository: KnowledgeRepository
) -> list[RevalidatedGovernedProcedure]:
    """Re-confirms each stored identity against real, current repository
    state before it may be used for anything. An entry is dropped
    (never raises) when: the `KnowledgeObject` no longer exists; its
    `lifecycle_status` is no longer `APPROVED`; it is no longer the
    version `resolve_current_version` (the real, unmodified 5.1E
    function) actually resolves as current for its own `knowledge_id`
    (i.e. it has been superseded since it was stored); or its
    `section_id` no longer names a real section within that version.
    Deduplicated by exact `(knowledge_id, version_label, section_id)`
    identity, first-seen order preserved.
    """
    results: list[RevalidatedGovernedProcedure] = []
    seen: set[tuple[str, str, Optional[str]]] = set()
    as_of = datetime.now(timezone.utc)

    for key in keys:
        identity = (key.knowledge_id, key.version_label, key.section_id)
        if identity in seen:
            continue

        knowledge_object = await repository.get(key.knowledge_id, key.version_label)
        if knowledge_object is None:
            continue
        if knowledge_object.lifecycle_status is not LifecycleStatus.APPROVED:
            continue

        family = await repository.list_versions(key.knowledge_id)
        resolution = resolve_current_version(family, as_of=as_of)
        if resolution.current is None or resolution.current.version.label != key.version_label:
            continue  # superseded/no-longer-current since it was stored -- stale, do not use

        heading: Optional[str] = None
        sibling_headings: list[str] = []
        if key.section_id is not None:
            matching_section = None
            for section in knowledge_object.sections:
                if section.section_id == key.section_id:
                    matching_section = section
                elif section.heading:
                    sibling_headings.append(section.heading)
            if matching_section is None:
                continue  # section no longer present in this (still-current) version
            heading = matching_section.heading
        else:
            sibling_headings = [section.heading for section in knowledge_object.sections if section.heading]

        seen.add(identity)
        results.append(
            RevalidatedGovernedProcedure(
                knowledge_id=key.knowledge_id,
                version_label=key.version_label,
                section_id=key.section_id,
                title=knowledge_object.title,
                heading=heading,
                sibling_headings=tuple(sibling_headings),
            )
        )

    return results


def detect_explicit_sibling_topic_override(
    question: str, candidates: Sequence[RevalidatedGovernedProcedure]
) -> Optional[str]:
    """Structural, identity-based override check (never NLP/keyword
    routing): does `question` verbatim-contain (case-insensitive) a REAL
    section heading from the SAME governed document(s) that is NOT the
    candidate's own heading? If so, returns that sibling heading -- the
    caller must treat this as an explicit topic change and skip scoping
    entirely, letting the ordinary, unscoped remediation path run.
    Returns `None` when no such sibling heading is found (scoping may
    proceed).
    """
    if not question:
        return None
    question_lower = question.lower()
    own_headings = {candidate.heading for candidate in candidates if candidate.heading}
    for candidate in candidates:
        for sibling in candidate.sibling_headings:
            if sibling in own_headings:
                continue
            if sibling.lower() in question_lower:
                return sibling
    return None


def detect_governed_evidence_anchor_mismatch(
    question: str,
    anchor: RevalidatedGovernedProcedure,
    current_selected_evidence: Sequence[KnowledgeEvidenceItem],
) -> bool:
    """DEF-0027 FINAL corrective pass -- extends this module's own
    revalidation/override machinery to the NORMAL (non-remediation)
    delegation completion boundary. Closes a real, live-observed gap:
    the pre-existing remediation call site
    (`backend.agents.team_manager.governed_knowledge_completion
    .enforce_governed_knowledge_at_completion`) only ever ran when a
    turn's own trusted `selected_knowledge_evidence` was EMPTY -- if the
    turn selected SOMETHING, even something from an entirely unrelated
    governed document, this module's own continuity machinery was never
    consulted at all. Real reproduction: a genuinely successful "HW
    Partial Fault" turn, followed by "it's a SupportUnit" -- incident_
    manager's own fresh retrieval selected evidence from an unrelated
    Rogers Resource Timeout MOP instead of continuing on Document1, and
    because SOME evidence was selected (just the wrong evidence), the
    empty-evidence remediation gate never fired.

    Returns `True` when `current_selected_evidence` is INCONSISTENT with
    `anchor` (a single, already-revalidated prior governed procedure
    identity -- the caller is responsible for only calling this when
    exactly one candidate survived `revalidate_prior_governed_evidence`;
    this function itself does not re-check that cardinality) AND nothing
    in `question` explicitly justifies the switch. The caller
    (`chat_service.py`) should then treat this exactly like the
    pre-existing empty-evidence case: invoke `enforce_governed_
    knowledge_at_completion` with the SAME `prior_governed_evidence` to
    recover the correct procedure (or fail closed via its own existing
    ambiguity/failure clarifications) -- never a new state system, the
    SAME `KnowledgeEvidenceSelectionKey`/revalidation/override machinery
    this module already provides.

    CONSISTENT (returns `False`, the common case -- no action needed):
      - `current_selected_evidence` is empty (the pre-existing empty-
        evidence gate already covers this case; this function's own job
        is narrower -- the NON-empty-but-wrong case);
      - `anchor.knowledge_id` IS represented among `current_selected_
        evidence` (the anchor's own governed document was genuinely
        consulted again this turn -- any ADDITIONAL evidence from
        another document alongside it is a separate, already-covered
        concern, see `evidence.py`'s own whole-guidance cross-document
        boundary, `_guidance_scope_established`);
      - `question` verbatim, case-insensitively contains the real
        section heading of at least one currently-selected item that
        belongs to a DIFFERENT `knowledge_id` than `anchor.knowledge_id`
        -- an EXPLICIT textual basis for this turn's own different
        selection, mirroring `detect_explicit_sibling_topic_override`'s
        own "compare only against real, already-retrieved headings,
        never invent a vocabulary" discipline (e.g. "how do I handle
        Resource Activation Timeout?" explicitly names a real heading of
        whatever got selected for it -- allowed to leave the prior
        anchor).

    MISMATCH (returns `True`): `current_selected_evidence` is entirely
    from a DIFFERENT `knowledge_id` than `anchor`, and nothing in
    `question` explains why.
    """
    if not current_selected_evidence:
        return False

    if any(item.reference.knowledge_id == anchor.knowledge_id for item in current_selected_evidence):
        return False

    question_lower = question.lower() if question else ""
    for item in current_selected_evidence:
        heading = item.section.heading
        if heading and heading.lower() in question_lower:
            return False

    return True


def build_scoped_remediation_question(question: str, procedure: RevalidatedGovernedProcedure) -> str:
    """Deterministic, identity-derived question augmentation -- a fixed
    template combined with real, revalidated governed `heading`/`title`
    text, never model-generated prose and never arbitrary prior-
    conversation text concatenation. Explicitly instructs the model that
    a different, explicitly-named procedure in `question` itself always
    takes priority -- the prompt-level half of the same rule `detect_
    explicit_sibling_topic_override` already enforces structurally.
    """
    procedure_label = procedure.heading or procedure.title
    return (
        f"{question}\n\n"
        f"(Continuity note: if this question does not itself name a specific governed "
        f'procedure, it concerns the previously discussed governed procedure "{procedure_label}" '
        f'in "{procedure.title}". If this question clearly names a different procedure, '
        f"disregard this note and use the procedure it names instead.)"
    )


def build_ambiguous_procedure_clarification(candidates: Sequence[RevalidatedGovernedProcedure]) -> str:
    """Deterministic, Python-authored clarification listing only real,
    governed section headings/titles -- never a model-invented name,
    never an arbitrary/highest-scoring pick among the candidates.
    """
    labels = [candidate.heading or candidate.title for candidate in candidates]
    joined = " or ".join(labels) if len(labels) <= 2 else ", ".join(labels[:-1]) + f", or {labels[-1]}"
    return f"Do you mean the {joined} procedure? Please confirm which one you mean so I can give you the correct governed guidance."


def build_scoped_failure_clarification(procedure: RevalidatedGovernedProcedure) -> str:
    """Deterministic fallback wording for when even a scoped remediation
    attempt did not produce a usable governed answer -- more useful than
    the generic failure text because it truthfully names the real,
    revalidated procedure this attempt was scoped to, never a fabricated
    fact.
    """
    procedure_label = procedure.heading or procedure.title
    return (
        f'I attempted to continue with the previously discussed "{procedure_label}" procedure, '
        "but could not produce a validated governed answer. Please confirm which alarm or "
        "governed procedure you mean."
    )


GENERIC_MISSING_PROCEDURE_CLARIFICATION = (
    "I need to know which specific alarm or governed procedure you mean before I can answer. "
    "Please name the alarm or procedure explicitly."
)
"""Deterministic, Python-authored fallback used only when NO valid prior
governed evidence identity exists at all AND the ordinary remediation
attempt still failed -- per instruction, preferable to a flat
"could not be validated" message whenever the actual problem may be a
missing procedure identity, never a guess at which procedure is meant.
"""


ACTIVE_GOVERNED_PROCEDURE_STATE_KEY = "active_governed_procedure"
"""6A.14 Active Procedure Continuity Correction -- holds exactly ONE
`KnowledgeEvidenceSelectionKey` (never a list), the single authoritative
governed procedure a continuation should anchor to. Separate, narrower
concept from `LAST_SELECTED_GOVERNED_EVIDENCE_STATE_KEY` above, which
continues to hold every distinct selected identity (active + supporting)
unchanged."""


def parse_active_governed_procedure(raw: Any) -> Optional[KnowledgeEvidenceSelectionKey]:
    """Tolerant, fail-closed parse of the raw session-state value back
    into a typed key -- absent/malformed/wrong-shaped data resolves to
    `None` (never a raised exception), exactly like `parse_last_selected_
    governed_evidence` above."""
    if not isinstance(raw, dict):
        return None
    try:
        return KnowledgeEvidenceSelectionKey.model_validate(raw)
    except Exception:
        return None


def compute_fresh_active_procedure_anchor(
    selected_evidence: Sequence[KnowledgeEvidenceItem], question: Optional[str]
) -> Optional[KnowledgeEvidenceSelectionKey]:
    """6A.14 Active Procedure Continuity Correction -- reuses `backend.
    agents.incident_manager.evidence.resolve_active_section_id`
    (DEF-0024/0027's own existing, unmodified, pure, turn-local resolver)
    against THIS turn's own fresh, trusted `selected_evidence` and THIS
    turn's own current question text. Returns `None` -- meaning "leave any
    existing anchor exactly as it is" -- whenever zero or more than one
    section was selected and the question does not uniquely resolve one.
    Local import to avoid a module-level import cycle with `backend.
    agents.incident_manager.evidence` (which does not, and must not, import
    this module).
    """
    from backend.agents.incident_manager.evidence import resolve_active_section_id

    if not selected_evidence:
        return None

    section_ids: list[str] = []
    headings_by_id: dict[str, Optional[str]] = {}
    key_by_section_id: dict[str, KnowledgeEvidenceSelectionKey] = {}
    for item in selected_evidence:
        section_id = item.section.section_id
        if section_id in key_by_section_id:
            continue
        section_ids.append(section_id)
        headings_by_id[section_id] = item.section.heading
        key_by_section_id[section_id] = KnowledgeEvidenceSelectionKey(
            knowledge_id=item.reference.knowledge_id,
            version_label=item.reference.version_label,
            section_id=item.reference.section_id,
        )

    active_id = resolve_active_section_id(question, section_ids, headings_by_id)
    if active_id is None:
        return None
    return key_by_section_id[active_id]


def build_active_governed_procedure_state_update(anchor: Optional[KnowledgeEvidenceSelectionKey]) -> dict[str, Any]:
    """Only ever SETS a new anchor -- never explicitly clears an existing
    one merely because this turn's own fresh resolution was ambiguous or
    absent (mirrors `build_last_selected_governed_evidence_state_update`'s
    own "empty/unresolved means no state change" contract exactly).
    """
    if anchor is None:
        return {}
    return {ACTIVE_GOVERNED_PROCEDURE_STATE_KEY: anchor.model_dump(mode="json")}


def resolve_explicit_current_candidate(
    question: str, candidates: Sequence[RevalidatedGovernedProcedure]
) -> Optional[RevalidatedGovernedProcedure]:
    """6A.14 Active Procedure Continuity Correction -- structural,
    identity-based match: does `question` verbatim, case-insensitively
    contain exactly ONE candidate's own real, already-retrieved section
    heading? Deliberately a SEPARATE function from `detect_explicit_
    sibling_topic_override` above (never reused/repurposed for this) --
    that function's own job is finding a heading OUTSIDE the candidate
    set to justify abandoning all of them; this one looks WITHIN the set,
    letting the user directly pick which of several already-ambiguous
    candidates they mean. Zero matches -> `None` (no resolution here,
    caller tries the next precedence step). Exactly one match -> that
    candidate. More than one match -> `None` (genuinely still ambiguous
    -- e.g. the question happens to verbatim-contain more than one
    candidate's own heading). No fuzzy/semantic matching of any kind.
    """
    if not question:
        return None
    question_lower = question.lower()
    matches = [candidate for candidate in candidates if candidate.heading and candidate.heading.lower() in question_lower]
    if len(matches) == 1:
        return matches[0]
    return None


def _subject_matches_heading(candidate: RevalidatedGovernedProcedure, subject: str) -> bool:
    """Deterministic, non-fuzzy: an exact case-insensitive (trimmed)
    match, or the candidate's own real heading appearing verbatim inside
    `subject` -- deliberately never the reverse (a heading merely
    containing the subject would let an overly-short/generic subject
    match too liberally)."""
    if not candidate.heading:
        return False
    heading_norm = candidate.heading.strip().lower()
    subject_norm = subject.strip().lower()
    if not heading_norm or not subject_norm:
        return False
    if heading_norm == subject_norm:
        return True
    return heading_norm in subject_norm


def resolve_active_candidate_among_ambiguous(
    *,
    question: str,
    request_contract_subject: Optional[str],
    candidates: Sequence[RevalidatedGovernedProcedure],
    active_anchor: Optional[RevalidatedGovernedProcedure],
) -> Optional[RevalidatedGovernedProcedure]:
    """6A.14 Active Procedure Continuity Correction -- called ONLY when
    the pre-existing DEF-0026 revalidation/override logic above already
    determined more than one candidate is genuinely, currently ambiguous.
    Attempts to deterministically narrow to exactly ONE of those SAME
    candidates -- never a fabricated/different identity -- using, in
    strict precedence order: (1) `resolve_explicit_current_candidate`,
    (2) `request_contract_subject` matching via `_subject_matches_
    heading`, (3) `active_anchor` (an already-revalidated, single prior
    anchor the caller is responsible for supplying) being itself one of
    `candidates`. Returns `None` (still genuinely ambiguous, caller must
    fall back to the existing clarification) when none of the three
    steps resolve to exactly one candidate.
    """
    explicit_match = resolve_explicit_current_candidate(question, candidates)
    if explicit_match is not None:
        return explicit_match

    if request_contract_subject:
        subject_matches = [candidate for candidate in candidates if _subject_matches_heading(candidate, request_contract_subject)]
        if len(subject_matches) == 1:
            return subject_matches[0]

    if active_anchor is not None:
        active_identity = (active_anchor.knowledge_id, active_anchor.version_label, active_anchor.section_id)
        for candidate in candidates:
            if (candidate.knowledge_id, candidate.version_label, candidate.section_id) == active_identity:
                return candidate

    return None
