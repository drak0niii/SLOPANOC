"""(C) VERSION GOVERNANCE -- effectiveness, the explicit supersession
graph, and deterministic current-version resolution.

VERSION LABELS ARE OPAQUE IDENTIFIERS, NEVER ORDERING INFORMATION (§17).
Nothing in this module compares, sorts, parses, or converts a
`KnowledgeVersion.label` for the purpose of deciding which version is
newer/current -- currentness comes ENTIRELY from explicit
`supersedes`/`superseded_by` declarations, `effective_from`/
`effective_to`, and `LifecycleStatus`. Where a deterministic tie-break is
needed for OUTPUT ordering only (never for deciding an outcome), labels
are sorted alphabetically purely for repeatable presentation -- never as
a proxy for "newer".

PERMANENT-SUPERSESSION RULE (the key design decision behind
`resolve_current_version`, documented here once rather than re-derived
at every call site): a successor "activates" a PERMANENT exclusion of
everything it (transitively) supersedes the moment it genuinely became
approved AND effective -- never merely because it was once approved, and
never merely because a later wall-clock date happens to fall on/after its
effective_from. Once activated, the exclusion holds forever afterward,
regardless of what later happens to the successor itself (even if it is
subsequently archived). See `_has_activated` for the exact, deterministic
rule -- summarized here for the three still-live successor states:

  - Still `CANDIDATE`: never approved -> never activates -> the
    predecessor is not excluded (§30).
  - Still `APPROVED`: activates the instant its own `effective_from` is
    reached (`effective_from <= as_of`) -- before that, the predecessor
    remains current (§29's future-effective successor).
  - Now `ARCHIVE`: activation must be determined from history, since the
    live `lifecycle_status` alone no longer distinguishes "was approved
    and effective before being archived" (§31 -- must keep excluding the
    predecessor) from "was approved but archived before its effective
    window ever began" (correction-pass requirement -- must NOT exclude
    the predecessor, and must not resume excluding it merely because the
    calendar later passes the abandoned successor's former
    `effective_from`). See `_has_activated`'s `ARCHIVE` branch for the
    exact deterministic rule, including its documented conservative
    fallback when available timestamps cannot prove either way.
"""
from __future__ import annotations

from datetime import datetime
from typing import Iterable

from backend.knowledge.domain.enums import LifecycleStatus
from backend.knowledge.domain.models import KnowledgeObject, KnowledgeVersion
from backend.knowledge.governance.contracts import (
    CurrentVersionResolution,
    CurrentVersionResolutionStatus,
    DuplicateVersionLabelError,
    MixedKnowledgeIdError,
    SupersessionCycleError,
    UnknownSupersessionReferenceError,
)


def is_effective(version: KnowledgeVersion, as_of: datetime) -> bool:
    """Deterministic effectiveness only -- independent of
    `LifecycleStatus` (§26: a version can be `APPROVED` but not yet
    effective, or effective-dated but still only `CANDIDATE`; neither
    alone makes it current). Both bounds are INCLUSIVE
    (`effective_from <= as_of <= effective_to`); an absent bound is
    simply unbounded on that side.
    """
    if version.effective_from is not None and as_of < version.effective_from:
        return False
    if version.effective_to is not None and as_of > version.effective_to:
        return False
    return True


def _validate_version_family(versions: list[KnowledgeObject]) -> dict[str, KnowledgeObject]:
    """Shared structural validation for every operation in this module.
    Returns the family as a `{version.label: KnowledgeObject}` mapping
    once validated. Raises (fails closed, per §22/§39) rather than
    guessing around any of: more than one `knowledge_id` present,
    duplicate version labels, a supersession reference to a label absent
    from the supplied family, or a supersession cycle (direct or
    transitive).
    """
    if not versions:
        return {}

    knowledge_ids = {v.knowledge_id for v in versions}
    if len(knowledge_ids) > 1:
        raise MixedKnowledgeIdError(f"version family contains more than one knowledge_id: {sorted(knowledge_ids)}")

    by_label: dict[str, KnowledgeObject] = {}
    for version_object in versions:
        label = version_object.version.label
        if label in by_label:
            raise DuplicateVersionLabelError(f"duplicate version label {label!r} within one version family")
        by_label[label] = version_object

    # Normalize supersedes/superseded_by into one edge set:
    # (newer_label, older_label) means "newer_label supersedes older_label".
    edges: set[tuple[str, str]] = set()
    for label, version_object in by_label.items():
        for older_label in version_object.version.supersedes:
            if older_label not in by_label:
                raise UnknownSupersessionReferenceError(
                    f"version {label!r} declares supersedes={older_label!r}, which is not "
                    "present in the supplied version family"
                )
            edges.add((label, older_label))
        for newer_label in version_object.version.superseded_by:
            if newer_label not in by_label:
                raise UnknownSupersessionReferenceError(
                    f"version {label!r} declares superseded_by={newer_label!r}, which is not "
                    "present in the supplied version family"
                )
            edges.add((newer_label, label))

    _reject_cycles(edges, by_label.keys())
    return by_label


def _reject_cycles(edges: set[tuple[str, str]], labels: Iterable[str]) -> None:
    successors: dict[str, set[str]] = {label: set() for label in labels}
    for newer, older in edges:
        successors[newer].add(older)

    WHITE, GRAY, BLACK = 0, 1, 2
    color: dict[str, int] = {label: WHITE for label in successors}

    def visit(label: str, path: list[str]) -> None:
        color[label] = GRAY
        for next_label in sorted(successors[label]):
            if color[next_label] == GRAY:
                cycle = path[path.index(next_label):] + [next_label]
                raise SupersessionCycleError(f"supersession cycle detected: {' -> '.join(cycle)}")
            if color[next_label] == WHITE:
                visit(next_label, path + [next_label])
        color[label] = BLACK

    for start_label in sorted(successors):
        if color[start_label] == WHITE:
            visit(start_label, [start_label])


def _direct_successors(by_label: dict[str, KnowledgeObject]) -> dict[str, set[str]]:
    """label -> the set of labels that DIRECTLY supersede it."""
    successors: dict[str, set[str]] = {label: set() for label in by_label}
    for label, version_object in by_label.items():
        for older_label in version_object.version.supersedes:
            successors[older_label].add(label)
        for newer_label in version_object.version.superseded_by:
            successors[label].add(newer_label)
    return successors


def _has_activated(version_object: KnowledgeObject, as_of: datetime) -> bool:
    """Whether `version_object` has, at some point, genuinely become
    approved AND effective -- the precise "activation" test the
    PERMANENT-SUPERSESSION RULE (this module's own docstring) is built
    on. Three cases, one per live `LifecycleStatus`:

    - `CANDIDATE`: never approved -> never activated. `False`.
    - `APPROVED`: still live -- activation tracks the CURRENT resolution
      instant: activated once its own `effective_from` has been reached
      relative to `as_of` (`effective_from is None or effective_from <=
      as_of`), exactly as before this correction pass.
    - `ARCHIVE`: the live `lifecycle_status` alone can no longer
      distinguish "was approved and effective before being archived"
      (must stay activated -- correction-pass §31) from "was approved
      but archived before its effective window ever began" (must NOT be
      treated as activated, even after the calendar later passes its
      abandoned `effective_from` -- correction-pass requirement). This
      package deliberately does not add a dedicated "approved_at" or
      "archived_at" history field (no lifecycle-history infrastructure
      is introduced) -- instead it reuses `KnowledgeObject.updated_at`,
      which `transition_lifecycle` (service.py) already sets on every
      transition and therefore already holds the timestamp of the most
      recent transition. For an object currently `ARCHIVE` -- a terminal
      state with no further transitions -- that most recent transition
      IS the archive transition, so `updated_at` is exactly the moment
      it became `ARCHIVE`.

        * If `updated_at` is known AND strictly earlier than
          `version.effective_from`: the object was archived before its
          effective window could even begin -- it is IMPOSSIBLE for it
          to have ever been approved and effective at the same time.
          Definitively `False` (never activated) -- this is what makes
          the "archived before ever becoming effective" case correct,
          including after `effective_from` has since passed.
        * Otherwise (`updated_at` is unknown -- `transitioned_at` was
          never supplied to the archiving transition -- or
          `effective_from` is unset/unbounded, or `updated_at` falls
          on/after `effective_from`): this module cannot, from the
          fields available on `KnowledgeObject` alone, positively rule
          out that the object was approved and effective at some
          overlapping instant before being archived. Per instruction
          ("fail conservatively... do NOT silently resurrect potentially
          obsolete knowledge"), the CONSERVATIVE default is `True`
          (treat as activated) -- this is the direction that keeps a
          predecessor excluded rather than risking a stale/superseded
          predecessor being silently resurrected as current. This
          conservative default is also exactly correct, not just safe,
          for the ordinary "archived after genuinely becoming current"
          case (§31): there, `updated_at` (the archive moment) is always
          on/after `effective_from` by construction, so this branch is
          reached and correctly returns `True`.
    """
    if version_object.lifecycle_status is LifecycleStatus.CANDIDATE:
        return False
    if version_object.lifecycle_status is LifecycleStatus.APPROVED:
        return version_object.version.effective_from is None or version_object.version.effective_from <= as_of
    # ARCHIVE
    archived_at = version_object.updated_at
    effective_from = version_object.version.effective_from
    if archived_at is not None and effective_from is not None and archived_at < effective_from:
        return False
    return True


def _permanently_excluded_labels(by_label: dict[str, KnowledgeObject], as_of: datetime) -> set[str]:
    """See this module's own docstring, "PERMANENT-SUPERSESSION RULE",
    and `_has_activated` for the exact per-version activation test.
    Returns every label that is transitively superseded by at least one
    activated successor.
    """
    successors = _direct_successors(by_label)
    activated = [label for label, version_object in by_label.items() if _has_activated(version_object, as_of)]

    excluded: set[str] = set()
    for activated_label in activated:
        # BFS from each activated version backward along its own direct
        # predecessors, then those predecessors' own predecessors, etc.
        # -- an activated successor permanently excludes everything it
        # transitively supersedes, not merely its direct predecessor.
        stack = [
            predecessor
            for predecessor, its_successors in successors.items()
            if activated_label in its_successors
        ]
        seen: set[str] = set()
        while stack:
            predecessor = stack.pop()
            if predecessor in seen:
                continue
            seen.add(predecessor)
            excluded.add(predecessor)
            stack.extend(
                grandparent
                for grandparent, its_successors in successors.items()
                if predecessor in its_successors and grandparent not in seen
            )
    return excluded


def resolve_current_version(versions: list[KnowledgeObject], as_of: datetime) -> CurrentVersionResolution:
    """(C) Deterministic current-version resolution for one logical
    `knowledge_id` at one `as_of` instant.

    Only `APPROVED` versions that are effective at `as_of` are ever
    eligible to BE current (§28: `CANDIDATE` and `ARCHIVE` are never
    returned). Among those, any version permanently excluded by this
    module's "PERMANENT-SUPERSESSION RULE" is removed. Exactly one
    remaining candidate -> `RESOLVED`; zero -> `NOT_FOUND`; more than one
    -> `AMBIGUOUS` (branching supersession, or no relationship at all
    between two otherwise-eligible versions -- never resolved by label,
    creation time, list order, or any other proxy for "newer").

    Raises an `InvalidVersionFamilyError` subclass (never silently
    recovers) if `versions` is structurally invalid -- see
    `_validate_version_family`.
    """
    by_label = _validate_version_family(versions)
    if not by_label:
        return CurrentVersionResolution(status=CurrentVersionResolutionStatus.NOT_FOUND, candidates=[])

    excluded = _permanently_excluded_labels(by_label, as_of)
    eligible_labels = sorted(
        label
        for label, version_object in by_label.items()
        if version_object.lifecycle_status is LifecycleStatus.APPROVED
        and is_effective(version_object.version, as_of)
        and label not in excluded
    )

    if len(eligible_labels) == 0:
        return CurrentVersionResolution(status=CurrentVersionResolutionStatus.NOT_FOUND, candidates=[])
    if len(eligible_labels) == 1:
        return CurrentVersionResolution(
            status=CurrentVersionResolutionStatus.RESOLVED,
            current=by_label[eligible_labels[0]],
            candidates=eligible_labels,
        )
    return CurrentVersionResolution(status=CurrentVersionResolutionStatus.AMBIGUOUS, candidates=eligible_labels)


def resolve_supersession_chain(versions: list[KnowledgeObject]) -> list[str]:
    """(C) A small, deterministic topological ordering (oldest -> newest)
    of one version family's explicit supersession graph -- follows
    declared relationships only; never sorts by label, creation time, or
    input list order (verified independent of input shuffling). Ties
    (versions with no ordering relationship between them) are broken
    alphabetically by label purely for deterministic OUTPUT, never as a
    proxy for "newer" -- see this module's own docstring.

    Raises an `InvalidVersionFamilyError` subclass for a structurally
    invalid family, exactly like `resolve_current_version`.
    """
    by_label = _validate_version_family(versions)
    if not by_label:
        return []

    successors = _direct_successors(by_label)
    predecessor_counts: dict[str, int] = {label: 0 for label in by_label}
    for label, its_successors in successors.items():
        for successor_label in its_successors:
            predecessor_counts[successor_label] += 1

    ordered: list[str] = []
    remaining = dict(predecessor_counts)
    while remaining:
        ready = sorted(label for label, count in remaining.items() if count == 0)
        # `_reject_cycles` (already run by `_validate_version_family`)
        # guarantees `ready` is never empty here -- a cyclic graph would
        # already have raised before this function's caller received it.
        for label in ready:
            ordered.append(label)
            del remaining[label]
            for successor_label in successors[label]:
                if successor_label in remaining:
                    remaining[successor_label] -= 1
    return ordered
