"""Phase 6A.4 Gate 1: AUTHORITY / ELIGIBILITY.

Reuses, never duplicates, the existing, frozen 5.1E version/supersession
authority (`backend/knowledge/governance/versioning.py`'s `resolve_
current_version`) -- this module adds only the per-OBJECT reason-code
mapping that authority does not itself provide (`resolve_current_version`
answers "which version, if any, is current for this family", never "why
was THIS SPECIFIC object excluded").

GOVERNANCE/ASSET-METADATA POLICY SIGNALS (§9/§19/§20/§21): AI-Approved,
Expiry, Review-Due, and Source-of-Truth are ALWAYS computed and reported
on every version-eligible object, regardless of policy. Only version
authority decides `EligibilityDecision.eligible` in THIS module --
applying `NarrowingPolicy.enforce_ai_approved`/`enforce_not_expired` (the
OBSERVE/ENFORCE boundary) is deliberately centralized in `service.py`'s
own single call site, so one object's final eligibility can combine
version-authority reasons AND policy reasons in exactly one place, never
two independently-acting exclusion mechanisms that could disagree.
Missing metadata is NEVER treated as if it were `True` (AI-Approved) or
"not expired" -- see `_ai_approval_signal`/`_expiry_signal` below for the
exact three-way (`True`/`False`/`None`-meaning-unknown) representation.
Source-of-Truth is reported but never enforced anywhere in 6A.4 (§21:
"6A.4 may expose eligibility signal... Do not invent business policy") --
there is no `enforce_source_of_truth` flag because no instruction asked
for one.

AUTHORIZATION (§22): audited before writing this module -- no
authorization/confidentiality ENFORCEMENT mechanism exists anywhere in
the current Governed Knowledge architecture (Phase 4H, which would build
one, is still future/unimplemented, confirmed by `docs/DEFECT_REGISTER
.md`/`CLAUDE.md`'s own CURRENT LIMITATIONS section). There is therefore
nothing for this module to "preserve" -- `NarrowingReasonCode
.UNAUTHORIZED` exists as a reserved, currently-unreachable code for
Phase 4H to wire into later, never a fabricated check here.
"""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from backend.knowledge.domain.enums import LifecycleStatus
from backend.knowledge.domain.models import KnowledgeObject
from backend.knowledge.governance.contracts import CurrentVersionResolution, CurrentVersionResolutionStatus, InvalidVersionFamilyError
from backend.knowledge.governance.versioning import is_effective, resolve_current_version
from backend.knowledge.narrowing.contracts import EligibilityDecision, NarrowingReasonCode

__all__ = ["evaluate_eligibility_for_corpus"]


def _group_by_knowledge_id(corpus: list[KnowledgeObject]) -> dict[str, list[KnowledgeObject]]:
    """Mirrors `retrieval/service.py`'s own `_group_by_knowledge_id`
    exactly (deliberately duplicated at this small size rather than
    imported, to keep `narrowing/` independent of `retrieval/` -- the two
    packages sit at the same architectural layer, neither should depend
    on the other's internals; both independently depend only on the
    shared, frozen `domain`/`governance` layers beneath them).
    """
    families: dict[str, list[KnowledgeObject]] = {}
    for knowledge_object in corpus:
        families.setdefault(knowledge_object.knowledge_id, []).append(knowledge_object)
    return families


def _permanently_excluded_labels_for_family(family: list[KnowledgeObject], as_of: datetime, resolution: CurrentVersionResolution) -> set[str]:
    """Re-derives which labels were permanently superseded, WITHOUT
    reimplementing `versioning.py`'s own private permanent-supersession
    rule (never imported -- this module only ever imports the public
    `resolve_current_version`/`is_effective`): a label is permanently
    excluded if and only if it is APPROVED, effective, yet still absent
    from the ALREADY-COMPUTED `resolution.candidates` -- reasoning
    directly and safely from that one already-correct public result.
    """
    approved_effective_labels = {
        obj.version.label
        for obj in family
        if obj.lifecycle_status is LifecycleStatus.APPROVED and is_effective(obj.version, as_of)
    }
    return approved_effective_labels - set(resolution.candidates)


def _version_exclusion_reason(knowledge_object: KnowledgeObject, as_of: datetime, permanently_excluded: bool) -> NarrowingReasonCode:
    """Why `knowledge_object` (already known NOT to be its family's
    resolved current version) is excluded -- never guessed, derived
    purely from its own already-governed fields.
    """
    if knowledge_object.lifecycle_status is LifecycleStatus.CANDIDATE:
        return NarrowingReasonCode.CANDIDATE_NOT_APPROVED
    if knowledge_object.lifecycle_status is LifecycleStatus.ARCHIVE:
        return NarrowingReasonCode.ARCHIVED
    # APPROVED from here on.
    if not is_effective(knowledge_object.version, as_of):
        return NarrowingReasonCode.NOT_EFFECTIVE
    if permanently_excluded:
        return NarrowingReasonCode.SUPERSEDED
    # APPROVED, effective, not permanently excluded, yet still not the
    # resolved current version -- structurally unreachable given
    # `resolve_current_version`'s own contract, but resolved defensively
    # rather than silently mis-attributing a reason.
    return NarrowingReasonCode.SUPERSEDED


def _ai_approval_signal(knowledge_object: KnowledgeObject) -> Optional[bool]:
    """`True`/`False`/`None` (metadata never set) -- `None` is NEVER
    treated as `True` by any caller of this function."""
    return knowledge_object.metadata.asset_metadata.governance.ai_approved_flag.normalized_value


def _expiry_signals(knowledge_object: KnowledgeObject, as_of: datetime) -> tuple[Optional[bool], Optional[bool]]:
    """Returns `(expired, review_due)`, each `True`/`False`/`None`
    (`None` = no date set at all -- never treated as "not expired")."""
    expiry = knowledge_object.metadata.asset_metadata.lifecycle.expiry_date.normalized_value
    review = knowledge_object.metadata.asset_metadata.lifecycle.next_review_date.normalized_value
    expired = None if expiry is None else (as_of.date() > expiry)
    review_due = None if review is None else (as_of.date() >= review)
    return expired, review_due


def _build_decision(knowledge_object: KnowledgeObject, as_of: datetime, *, eligible: bool, reasons: list[NarrowingReasonCode]) -> EligibilityDecision:
    ai_approved = _ai_approval_signal(knowledge_object)
    expired, review_due = _expiry_signals(knowledge_object, as_of)
    source_of_truth = knowledge_object.metadata.asset_metadata.governance.source_of_truth_flag.normalized_value
    return EligibilityDecision(
        knowledge_id=knowledge_object.knowledge_id,
        version_label=knowledge_object.version.label,
        eligible=eligible,
        reason_codes=reasons,
        ai_approved=ai_approved,
        expired=expired,
        review_due=review_due,
        source_of_truth=source_of_truth,
    )


def evaluate_eligibility_for_corpus(corpus: list[KnowledgeObject], as_of: datetime) -> list[EligibilityDecision]:
    """Gate 1's OWN version-authority conclusion for an entire corpus --
    one `EligibilityDecision` per input object. Governance/asset-metadata
    POLICY enforcement (AI-Approved/Expiry) is NOT applied here -- see
    this module's own docstring; `service.py` applies `NarrowingPolicy`
    on top of this function's result as a separate, explicit pass.

    A structurally invalid version family (`InvalidVersionFamilyError` --
    duplicate labels, a dangling supersession reference, a supersession
    cycle) fails every member of that family closed -- excluded, reason
    `AMBIGUOUS_VERSION_RESOLUTION` (the closest existing meaning: this
    family's currentness cannot be deterministically resolved), mirroring
    `retrieval/service.py`'s own diagnostic-and-skip behavior for the
    same underlying exception, never a raised exception escaping this
    function for one bad family while poisoning the whole corpus.
    """
    families = _group_by_knowledge_id(corpus)
    decisions: list[EligibilityDecision] = []

    for knowledge_id in sorted(families):
        family = families[knowledge_id]
        try:
            resolution = resolve_current_version(family, as_of)
        except InvalidVersionFamilyError:
            for knowledge_object in family:
                decisions.append(_build_decision(knowledge_object, as_of, eligible=False, reasons=[NarrowingReasonCode.AMBIGUOUS_VERSION_RESOLUTION]))
            continue

        if resolution.status is CurrentVersionResolutionStatus.AMBIGUOUS:
            for knowledge_object in family:
                decisions.append(_build_decision(knowledge_object, as_of, eligible=False, reasons=[NarrowingReasonCode.AMBIGUOUS_VERSION_RESOLUTION]))
            continue

        current_label = resolution.current.version.label if resolution.status is CurrentVersionResolutionStatus.RESOLVED else None
        permanently_excluded_labels = _permanently_excluded_labels_for_family(family, as_of, resolution)

        for knowledge_object in family:
            if knowledge_object.version.label == current_label:
                decisions.append(_build_decision(knowledge_object, as_of, eligible=True, reasons=[]))
            else:
                reason = _version_exclusion_reason(
                    knowledge_object, as_of, permanently_excluded=knowledge_object.version.label in permanently_excluded_labels
                )
                decisions.append(_build_decision(knowledge_object, as_of, eligible=False, reasons=[reason]))

    return decisions
