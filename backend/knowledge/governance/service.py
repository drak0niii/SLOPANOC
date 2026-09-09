"""Governed materialization (A) and lifecycle transitions (B) -- the two
non-version-graph governance responsibilities. See versioning.py for (C),
version governance.
"""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from backend.knowledge.domain.enums import KnowledgeDocumentType, LifecycleStatus
from backend.knowledge.domain.models import KnowledgeObject, KnowledgeSection, KnowledgeVersion
from backend.knowledge.governance.contracts import InvalidLifecycleTransitionError
from backend.knowledge.processing.contracts import StructuredKnowledgeDocument

_ALLOWED_LIFECYCLE_TRANSITIONS: dict[LifecycleStatus, frozenset[LifecycleStatus]] = {
    LifecycleStatus.CANDIDATE: frozenset({LifecycleStatus.APPROVED}),
    LifecycleStatus.APPROVED: frozenset({LifecycleStatus.ARCHIVE}),
    LifecycleStatus.ARCHIVE: frozenset(),
}
"""The one authoritative lifecycle state machine (§13). Same-state,
backwards, and skipped transitions are all absent from this table and
therefore rejected by `transition_lifecycle` -- there is no separate
rule set anywhere else in this package or a future one that could
diverge from it.
"""


def _length_prefixed_encode(parts: tuple[str, ...]) -> str:
    """A netstring-style, collision-safe encoding of an ordered tuple of
    strings: each part is emitted as `"{len(part)}:{part}"`, concatenated
    with no separator between parts.

    COLLISION-SAFETY, not merely determinism: a plain
    `"::".join(parts)` scheme is ambiguous whenever a part can itself
    contain the delimiter (e.g. `("a::b", "c")` and `("a", "b::c")` both
    join to `"a::b::c"`). Length-prefixing removes that ambiguity
    entirely -- decoding is a single unambiguous left-to-right walk (read
    digits up to the next `:`, then read exactly that many characters as
    the part, repeat), so two DIFFERENT tuples can never encode to the
    same string, regardless of what characters any part contains
    (including further `:` or digit characters) -- no restriction is
    placed on the input strings themselves.
    """
    return "".join(f"{len(part)}:{part}" for part in parts)


def final_section_id(knowledge_id: str, version_label: str, section_key: str) -> str:
    """The deterministic, collision-safe final `KnowledgeSection.section_id`
    scheme: the length-prefixed encoding (see `_length_prefixed_encode`)
    of the tuple `(knowledge_id, version_label, section_key)`.

    Deliberately built ONLY from already-governed identity (the explicit
    final `knowledge_id`, the explicit final `version.label`) and the
    5.1D LOCAL `section_key` -- never a random UUID, never a timestamp,
    and never a hash of the section's own operational body content
    (which would make identity accidentally depend on wording rather
    than governed identity). Because `version_label` is embedded, two
    different governed versions of the same `knowledge_id` never collide
    on final section identity, even if their local `section_key`s (which
    are only unique WITHIN one document) happen to match (e.g. both
    versions' first section is locally "section-0000") -- and because
    the encoding is length-prefixed rather than delimiter-joined, this
    holds even when `knowledge_id`/`version_label`/`section_key`
    themselves contain characters that look like a separator.
    """
    return _length_prefixed_encode((knowledge_id, version_label, section_key))


def materialize_candidate(
    structured_document: StructuredKnowledgeDocument,
    *,
    knowledge_id: str,
    document_type: KnowledgeDocumentType,
    version: KnowledgeVersion,
    governed_at: Optional[datetime] = None,
    section_roles: Optional[dict[str, str]] = None,
) -> KnowledgeObject:
    """(A) GOVERNED MATERIALIZATION: the explicit boundary where a
    pre-governance `StructuredKnowledgeDocument` becomes a governed
    `KnowledgeObject`, always entering as `LifecycleStatus.CANDIDATE` --
    never `APPROVED` (§8; approval is a separate, later, explicit
    transition -- see `approve_version` below).

    `knowledge_id`/`document_type`/`version` are REQUIRED, EXPLICIT
    keyword arguments -- this function never reads
    `structured_document.source_document.knowledge_id_hint`/
    `document_type_hint`/`version_hint` at all (verified structurally by
    `backend/tests/knowledge/test_governance_materialization.py`'s
    dedicated AST check, the same discipline used to guarantee
    document-type independence in 5.1D). A caller MAY choose to pass the
    same value a hint already suggested, but that is the caller's own
    explicit decision -- this function has no path through which a hint
    could silently become authoritative, and no fallback that invents a
    missing value (a missing/invalid `knowledge_id`/`document_type`/
    `version` is a caller error, not something this function guesses
    around).

    `title`/`source`/`metadata`/`applicability` are preserved from
    `structured_document.source_document` exactly, unmutated, unenriched,
    and never re-evaluated (applicability evaluation remains 5.1B's own
    concern, never invoked here). `artifacts` (A5) are carried through
    from `source_document.artifacts` exactly, unmutated -- this function
    never adds, removes, or reinterprets an artifact. Each
    `StructuredKnowledgeSection` becomes one `KnowledgeSection`,
    preserving `sequence`/`heading`/`content`/`source_locator`/
    `artifact_id` exactly; `section_type` is left unset (`None`) UNLESS
    the caller explicitly supplies `section_roles` (A5) -- a `section_key
    -> section_type` mapping applied ONLY for the section_key(s) present
    in it, exactly mirroring `knowledge_id`/`document_type`/`version`'s
    own "required, explicit, never inferred" discipline: 5.1E still never
    fabricates a semantic type on its own initiative; a caller
    (ultimately a trusted/operator-controlled ingestion path, never the
    model -- see A5 instruction section 40) may explicitly assert one.
    `created_at`/`updated_at` are set to `governed_at` only if the caller
    supplies it -- this function never calls
    `datetime.now()`/`datetime.utcnow()` itself.
    """
    source_document = structured_document.source_document
    roles = section_roles or {}
    sections = [
        KnowledgeSection(
            section_id=final_section_id(knowledge_id, version.label, section.section_key),
            knowledge_id=knowledge_id,
            heading=section.heading,
            section_type=roles.get(section.section_key),
            sequence=section.sequence,
            content=section.content,
            source_locator=section.source_locator,
            artifact_id=section.artifact_id,
        )
        for section in structured_document.sections
    ]
    return KnowledgeObject(
        knowledge_id=knowledge_id,
        document_type=document_type,
        title=source_document.title,
        version=version,
        lifecycle_status=LifecycleStatus.CANDIDATE,
        source=source_document.source,
        metadata=source_document.metadata,
        applicability=source_document.applicability,
        sections=sections,
        artifacts=source_document.artifacts,
        created_at=governed_at,
        updated_at=governed_at,
    )


def transition_lifecycle(
    knowledge_object: KnowledgeObject,
    target_status: LifecycleStatus,
    *,
    transitioned_at: Optional[datetime] = None,
) -> KnowledgeObject:
    """(B) LIFECYCLE TRANSITIONS: a pure function -- never mutates
    `knowledge_object`, never persists anything, never emits an event,
    never calls an external system, never touches any OTHER
    `KnowledgeObject` (approving/archiving one object never
    auto-archives a predecessor/successor -- see `versioning.py`'s
    module docstring for why that is a version-resolution concern, not a
    lifecycle-mutation one).

    Raises `InvalidLifecycleTransitionError` for any transition not in
    `_ALLOWED_LIFECYCLE_TRANSITIONS` -- including a same-state
    "transition" (e.g. CANDIDATE -> CANDIDATE), which is not itself in
    the table and is therefore rejected exactly like any other invalid
    transition, per instruction.
    """
    allowed = _ALLOWED_LIFECYCLE_TRANSITIONS.get(knowledge_object.lifecycle_status, frozenset())
    if target_status not in allowed:
        raise InvalidLifecycleTransitionError(
            f"cannot transition from {knowledge_object.lifecycle_status.value!r} "
            f"to {target_status.value!r}"
        )
    return knowledge_object.model_copy(update={"lifecycle_status": target_status, "updated_at": transitioned_at})


def approve_version(knowledge_object: KnowledgeObject, *, transitioned_at: Optional[datetime] = None) -> KnowledgeObject:
    """Convenience wrapper enforcing the SAME transition rule table as
    `transition_lifecycle` (never a second lifecycle implementation) --
    equivalent to `transition_lifecycle(knowledge_object,
    LifecycleStatus.APPROVED, transitioned_at=transitioned_at)`.
    """
    return transition_lifecycle(knowledge_object, LifecycleStatus.APPROVED, transitioned_at=transitioned_at)


def archive_version(knowledge_object: KnowledgeObject, *, transitioned_at: Optional[datetime] = None) -> KnowledgeObject:
    """Convenience wrapper enforcing the SAME transition rule table as
    `transition_lifecycle` -- equivalent to `transition_lifecycle(
    knowledge_object, LifecycleStatus.ARCHIVE, transitioned_at=transitioned_at)`.
    """
    return transition_lifecycle(knowledge_object, LifecycleStatus.ARCHIVE, transitioned_at=transitioned_at)
