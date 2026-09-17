"""POST-6A PROMPT 3 -- Descriptor authoring and human approval.

THE GAP THIS CLOSES: `approve_descriptor(descriptor,
governance_transition_approved=True)` (operation_descriptor.py, repair 3)
made approval a CALLER ASSERTION. Any code able to call it could mint an
APPROVED descriptor, which is precisely the authority boundary the
Generic KM contract exists to protect. There was also no authoring path
at all, so the descriptor concept was unreachable in practice.

WHAT THIS IS: the one governed seam where a descriptor becomes
authoritative, expressed in the SAME shape the rest of this package
already uses -- pure functions over `KnowledgeObject`, no persistence, no
clock, no external call. Approval is carried by a typed
`OperationApprovalRecord` that a human governance step produces, and it
is checked against the real content it claims to approve:

  - BOUND TO THE EXACT SOURCE VERSION: `knowledge_id` + `version_label` +
    `section_id` must match the object and section being approved. An
    approval for `v1` can never promote the descriptor on `v2`.
  - BOUND TO THE EXACT DESCRIPTOR CONTENT: `descriptor_fingerprint` is a
    deterministic hash of every authority-bearing field (scope, effect,
    parameters, templates, prerequisites, prohibitions). Editing a
    template, adding a parameter, or widening the scope changes the
    fingerprint, so the old approval stops matching and the descriptor
    falls back to CANDIDATE. Approval cannot be "carried over" an edit.
  - NEVER SELF-ASSERTED: the fingerprint is computed here from the
    descriptor's own content, never read from a field the descriptor (or
    whoever authored it) supplies.

EXISTING CORPUS IS NEVER AUTO-APPROVED: there is no bulk/implicit
promotion path in this module, `materialize_candidate` forces every
ingested descriptor to CANDIDATE, and a descriptor with no matching
approval record stays CANDIDATE -- which every consumer already treats as
"nothing established".
"""
from __future__ import annotations

import hashlib
from datetime import datetime
from typing import Optional

from pydantic import BaseModel, ConfigDict, field_validator

from backend.knowledge.domain._shared import require_non_blank
from backend.knowledge.domain.enums import LifecycleStatus
from backend.knowledge.domain.models import KnowledgeObject
from backend.knowledge.domain.operation_descriptor import (
    GovernedOperationDescriptor,
    OperationDescriptorAuthority,
    _approved_copy,
)
from backend.knowledge.governance.contracts import KnowledgeGovernanceError

__all__ = [
    "OperationApprovalError",
    "OperationApprovalRecord",
    "descriptor_fingerprint",
    "author_section_operation",
    "approve_section_operation",
    "apply_operation_approvals",
]

_FINGERPRINT_VERSION = "1"
"""Bumped only by a deliberate change to what the fingerprint covers --
which would correctly invalidate every existing approval, since the thing
being approved would no longer be described the same way."""


class OperationApprovalError(KnowledgeGovernanceError):
    """Raised when an approval record does not actually authorize what it
    is being applied to -- wrong knowledge/version/section, a fingerprint
    that no longer matches the descriptor's content, or an attempt to
    approve an operation on knowledge that is not itself APPROVED."""


class OperationApprovalRecord(BaseModel):
    """The output of a human governance decision about ONE descriptor on
    ONE section of ONE version.

    Deliberately carries no authority of its own beyond the identity it
    names: it is checked against real content before anything is
    promoted, so possessing a record for a DIFFERENT descriptor, section,
    or version promotes nothing.
    """

    model_config = ConfigDict(frozen=True)

    knowledge_id: str
    version_label: str
    section_id: str
    descriptor_fingerprint: str
    approved_by: str
    approved_at: Optional[datetime] = None
    note: Optional[str] = None

    @field_validator("knowledge_id", "version_label", "section_id", "descriptor_fingerprint", "approved_by")
    @classmethod
    def _non_blank(cls, value: str, info) -> str:  # type: ignore[no-untyped-def]
        return require_non_blank(value, info.field_name)


def _encode(parts: tuple[str, ...]) -> str:
    """Length-prefixed encoding -- the SAME unambiguous-joining discipline
    `governance/service.py`'s own `final_section_id` uses, so no two
    different field tuples can ever produce the same string."""
    return "\x1f".join(f"{len(part)}:{part}" for part in parts)


def descriptor_fingerprint(descriptor: GovernedOperationDescriptor) -> str:
    """Deterministic SHA-256 over every AUTHORITY-BEARING field of
    `descriptor`.

    DELIBERATELY EXCLUDED: `authority` itself (otherwise the fingerprint
    would change the moment it is promoted, invalidating its own
    approval) and the `knowledge_id`/`version_label`/`section_id` binding
    (which the approval record checks separately, and which
    `bind_to_source` legitimately re-stamps). Everything a human would be
    approving -- what the operation does, what it needs, and what it may
    run -- is covered.
    """
    parameter_parts = tuple(
        _encode(
            (
                param.name,
                param.kind.value,
                "required" if param.required else "optional",
                param.identifier_class or "",
                _encode(tuple(param.allowed_values)),
            )
        )
        for param in descriptor.parameters
    )
    template_parts = tuple(
        _encode(
            (
                template.template_id,
                template.template,
                template.effect.value,
                _encode(tuple(template.prerequisites)),
                _encode(tuple(template.prohibitions)),
            )
        )
        for template in descriptor.command_templates
    )
    basis = _encode(
        (
            _FINGERPRINT_VERSION,
            descriptor.operation_id,
            descriptor.target_scope.value,
            descriptor.effect.value,
            _encode(parameter_parts),
            _encode(template_parts),
            _encode(tuple(descriptor.prerequisites)),
            _encode(tuple(descriptor.prohibitions)),
        )
    )
    return hashlib.sha256(basis.encode("utf-8")).hexdigest()


def author_section_operation(
    knowledge_object: KnowledgeObject, section_id: str, descriptor: GovernedOperationDescriptor
) -> KnowledgeObject:
    """(AUTHORING) Attach a descriptor to one section, ALWAYS as
    `CANDIDATE`, with its source identity stamped from the object itself.

    Pure: returns a new `KnowledgeObject`, never mutates the input, never
    persists. Whatever `authority` the caller's descriptor carried is
    discarded -- authoring never approves, exactly as
    `materialize_candidate` never approves a document.
    """
    section = _require_section(knowledge_object, section_id)
    candidate = descriptor.model_copy(
        update={"authority": OperationDescriptorAuthority.CANDIDATE}
    ).bind_to_source(
        knowledge_id=knowledge_object.knowledge_id,
        version_label=knowledge_object.version.label,
        section_id=section.section_id,
    )
    return _replace_section_operation(knowledge_object, section_id, candidate)


def approve_section_operation(
    knowledge_object: KnowledgeObject, approval: OperationApprovalRecord
) -> KnowledgeObject:
    """(APPROVAL) Promote ONE section's descriptor to `APPROVED`, if and
    only if `approval` genuinely authorizes THIS descriptor on THIS
    section of THIS version.

    Raises `OperationApprovalError` -- never silently returns the object
    unchanged -- so a mismatched approval is a loud governance failure
    rather than an invisible no-op.
    """
    if approval.knowledge_id != knowledge_object.knowledge_id:
        raise OperationApprovalError(
            f"approval names knowledge_id {approval.knowledge_id!r}, object is {knowledge_object.knowledge_id!r}"
        )
    if approval.version_label != knowledge_object.version.label:
        raise OperationApprovalError(
            f"approval names version {approval.version_label!r}, object is version "
            f"{knowledge_object.version.label!r} -- an approval never carries across versions"
        )
    if knowledge_object.lifecycle_status is not LifecycleStatus.APPROVED:
        raise OperationApprovalError(
            "an operation descriptor cannot be approved on knowledge that is not itself APPROVED "
            f"(lifecycle_status={knowledge_object.lifecycle_status.value!r})"
        )

    section = _require_section(knowledge_object, approval.section_id)
    descriptor = section.operation
    if descriptor is None:
        raise OperationApprovalError(f"section {approval.section_id!r} carries no operation descriptor to approve")

    actual = descriptor_fingerprint(descriptor)
    if actual != approval.descriptor_fingerprint:
        raise OperationApprovalError(
            f"approval fingerprint does not match the descriptor currently on section "
            f"{approval.section_id!r} -- the descriptor has changed since it was approved"
        )

    approved = _approved_copy(descriptor).bind_to_source(
        knowledge_id=knowledge_object.knowledge_id,
        version_label=knowledge_object.version.label,
        section_id=section.section_id,
    )
    return _replace_section_operation(knowledge_object, approval.section_id, approved)


def apply_operation_approvals(
    knowledge_object: KnowledgeObject, approvals: list[OperationApprovalRecord]
) -> KnowledgeObject:
    """Apply several approvals to one object, in order. Every one is
    checked independently by `approve_section_operation` -- this is a
    convenience for a governance UI/CLI applying a reviewed batch, never
    a bulk-promotion shortcut: an approval that does not match still
    raises, and the batch stops there rather than partially trusting."""
    result = knowledge_object
    for approval in approvals:
        result = approve_section_operation(result, approval)
    return result


def _require_section(knowledge_object: KnowledgeObject, section_id: str):
    for section in knowledge_object.sections:
        if section.section_id == section_id:
            return section
    raise OperationApprovalError(
        f"no section {section_id!r} in knowledge_id={knowledge_object.knowledge_id!r} "
        f"version_label={knowledge_object.version.label!r}"
    )


def _replace_section_operation(
    knowledge_object: KnowledgeObject, section_id: str, descriptor: Optional[GovernedOperationDescriptor]
) -> KnowledgeObject:
    sections = [
        section.model_copy(update={"operation": descriptor}) if section.section_id == section_id else section
        for section in knowledge_object.sections
    ]
    return knowledge_object.model_copy(update={"sections": sections})
