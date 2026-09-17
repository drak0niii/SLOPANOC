"""POST-6A -- The API-facing descriptor authoring and approval service.

Connects `backend/knowledge/governance/operation_approval.py` (pure
rules) and `operation_approval_store.py` (permission, persistence,
revocation) to the real knowledge repository, so a human can actually
draft, review, approve and revoke a governed operation descriptor.

THE AUTHORITY CHAIN, end to end:

  1. DRAFT -- `author_operation` attaches a descriptor to a section as
     CANDIDATE and persists it to the repository. Drafting grants no
     operational authority, but it WITHDRAWS any live approval on that
     section, so it is permission-gated exactly like approval: removing
     authority is a governance act too.
  2. APPROVE -- `approve_operation` verifies the actor's governance
     permission SERVER-SIDE, builds the approval record itself from the
     descriptor actually on the section, persists reviewer identity and
     timestamp, and promotes the stored descriptor to APPROVED.
  3. RESOLVE -- at runtime, `resolve_governed_operation_descriptor`
     (team_manager) reads the APPROVED descriptor off the selected
     evidence. Nothing else confers authority.

A DESCRIPTOR IS NEVER FABRICATED. `draft_descriptor_from_section` builds
a CANDIDATE skeleton from real section content -- it records the section
identity and leaves scope UNKNOWN with no templates, because guessing a
target scope or inventing a command template would be exactly the
fabrication this whole mechanism exists to prevent. A human fills those
in; the code refuses to.

THE MODEL HAS NO ROLE HERE. There is no tool, no agent hook and no
model-callable path into this module.
"""
from __future__ import annotations

import logging
from functools import lru_cache
from typing import Optional

from backend.config.settings import get_settings
from backend.knowledge.domain.models import KnowledgeObject
from backend.knowledge.domain.operation_descriptor import (
    GovernedOperationDescriptor,
    OperationTargetScope,
)
from backend.knowledge.governance.contracts import KnowledgeGovernanceError
from backend.knowledge.governance.operation_approval import (
    OperationApprovalRecord,
    author_section_operation,
    descriptor_fingerprint,
)
from backend.knowledge.governance.operation_approval_store import (
    GovernancePermissionError,
    GovernanceUnavailableError,
    OperationApprovalStore,
)
from backend.knowledge.repository.contracts import KnowledgeRepository
from backend.tools.knowledge.runtime import get_knowledge_repository

_logger = logging.getLogger(__name__)

__all__ = [
    "GovernancePermissionError",
    "GovernanceUnavailableError",
    "KnowledgeGovernanceService",
    "draft_descriptor_from_section",
    "get_knowledge_governance_service",
]


def draft_descriptor_from_section(
    knowledge_object: KnowledgeObject, section_id: str, *, operation_id: Optional[str] = None
) -> GovernedOperationDescriptor:
    """A CANDIDATE skeleton drafted FROM real source material.

    Deliberately minimal and deliberately incomplete:
      - `target_scope` is UNKNOWN, not guessed. Guessing
        TARGET_INDEPENDENT would hand out a command with no confirmed
        target; guessing SINGLE_TARGET would demand one for a genuinely
        system-wide operation. Neither is knowable from prose.
      - `command_templates` is EMPTY. A template is an executable form;
        extracting one by pattern-matching a document would fabricate
        operational authority from text.

    What it DOES establish is identity: which section this operation
    belongs to, bound from the object itself. The human review step fills
    in scope, parameters and templates.
    """
    section = next((s for s in knowledge_object.sections if s.section_id == section_id), None)
    if section is None:
        raise KnowledgeGovernanceError(f"no section {section_id!r} in this knowledge version")
    return GovernedOperationDescriptor(
        operation_id=operation_id or f"{knowledge_object.knowledge_id}:{section_id}",
        target_scope=OperationTargetScope.UNKNOWN,
    ).bind_to_source(
        knowledge_id=knowledge_object.knowledge_id,
        version_label=knowledge_object.version.label,
        section_id=section_id,
    )


class KnowledgeGovernanceService:
    """The one service the API routes depend on."""

    def __init__(self, repository: KnowledgeRepository, store: OperationApprovalStore) -> None:
        self._repository = repository
        self._store = store

    async def _require_object(self, knowledge_id: str, version_label: str) -> KnowledgeObject:
        knowledge_object = await self._repository.get(knowledge_id, version_label)
        if knowledge_object is None:
            raise KnowledgeGovernanceError(
                f"no governed knowledge found for knowledge_id={knowledge_id!r} version_label={version_label!r}"
            )
        return knowledge_object

    async def list_sections(self, knowledge_id: str, version_label: str) -> list[dict]:
        """The authoring/review listing: every section, its current
        descriptor state, and whether a live approval currently backs
        it. Content is truncated -- this is a review surface, not a
        document viewer."""
        knowledge_object = await self._require_object(knowledge_id, version_label)
        rows: list[dict] = []
        for section in knowledge_object.sections:
            approval = await self._store.active_approval_for(knowledge_object, section.section_id)
            rows.append(
                {
                    "section_id": section.section_id,
                    "heading": section.heading,
                    "content_preview": section.content[:400],
                    "has_descriptor": section.operation is not None,
                    "authority": section.operation.authority.value if section.operation else None,
                    "operation_id": section.operation.operation_id if section.operation else None,
                    "target_scope": section.operation.target_scope.value if section.operation else None,
                    "effect": section.operation.effect.value if section.operation else None,
                    "descriptor_fingerprint": (
                        descriptor_fingerprint(section.operation) if section.operation else None
                    ),
                    "approved_by": approval.approved_by if approval else None,
                    "approved_at": approval.approved_at.isoformat() if approval and approval.approved_at else None,
                }
            )
        return rows

    async def author_operation(
        self,
        knowledge_id: str,
        version_label: str,
        section_id: str,
        descriptor: GovernedOperationDescriptor,
        *,
        actor_user_id: str = "",
        verified: bool = False,
        roles: tuple[str, ...] = (),
    ) -> GovernedOperationDescriptor:
        """Draft or replace a section's descriptor, ALWAYS as CANDIDATE.

        Replacing an approved descriptor is allowed and is exactly the
        edit case: the new content has a different fingerprint, so the
        stored approval stops matching and the section falls back to
        CANDIDATE until it is reviewed again.
        """
        knowledge_object = await self._require_object(knowledge_id, version_label)
        # POST-6A -- DRAFTING IS PERMISSION-GATED, not merely gated on
        # governance being available.
        #
        # The earlier reasoning ("a CANDIDATE grants nothing, so anyone
        # may draft") was wrong about what this operation actually does.
        # Authoring REPLACES the section's stored descriptor and then
        # calls `invalidate_on_edit`, which withdraws the live approval.
        # So an unprivileged caller could not mint authority, but could
        # DESTROY it -- overwrite a reviewed descriptor and demote an
        # approved operation to CANDIDATE, disabling a governed operation
        # for everyone. Removing authority is a governance act as much as
        # granting it, and it gets the same gate.
        self._store.require_governance_permission(actor_user_id, verified=verified, roles=roles)
        updated = author_section_operation(knowledge_object, section_id, descriptor)
        await self._repository.replace(updated)
        # An edit explicitly invalidates the prior approval, rather than
        # relying only on the fingerprint no longer matching -- so a review
        # surface never shows a stale "approved" against changed content.
        await self._store.invalidate_on_edit(knowledge_id, version_label, section_id)
        _logger.info(
            "knowledge_governance: descriptor drafted as CANDIDATE knowledge_id=%s version=%s section=%s",
            knowledge_id,
            version_label,
            section_id,
        )
        authored = next(s for s in updated.sections if s.section_id == section_id).operation
        assert authored is not None
        return authored

    async def approve_operation(
        self,
        knowledge_id: str,
        version_label: str,
        section_id: str,
        *,
        actor_user_id: str,
        verified: bool = False,
        roles: tuple[str, ...] = (),
        note: Optional[str] = None,
    ) -> OperationApprovalRecord:
        """Verify permission, record the decision, promote the stored
        descriptor.

        The order matters: permission is checked first (so an
        unauthorized actor never creates an artifact), the record is
        built server-side from real content (so it cannot describe a
        descriptor other than the one being approved), and only then is
        the repository updated.
        """
        knowledge_object = await self._require_object(knowledge_id, version_label)
        record = await self._store.create_approval(
            knowledge_object, section_id, actor_user_id=actor_user_id, verified=verified, roles=roles, note=note
        )
        promoted = await self._store.apply_active_approvals(knowledge_object)
        await self._repository.replace(promoted)
        _logger.info(
            "knowledge_governance: descriptor APPROVED by=%s knowledge_id=%s version=%s section=%s",
            actor_user_id,
            knowledge_id,
            version_label,
            section_id,
        )
        return record

    async def revoke_operation(
        self,
        knowledge_id: str,
        version_label: str,
        section_id: str,
        *,
        actor_user_id: str,
        verified: bool = False,
        roles: tuple[str, ...] = (),
    ) -> bool:
        """Withdraw an approval and demote the stored descriptor back to
        CANDIDATE in the same operation -- a revocation that left the
        repository showing APPROVED would be a revocation in name only."""
        knowledge_object = await self._require_object(knowledge_id, version_label)
        revoked = await self._store.revoke_approval(
            knowledge_id, version_label, section_id, actor_user_id=actor_user_id, verified=verified, roles=roles
        )
        if not revoked:
            return False
        section = next((s for s in knowledge_object.sections if s.section_id == section_id), None)
        if section is not None and section.operation is not None:
            demoted = author_section_operation(knowledge_object, section_id, section.operation)
            await self._repository.replace(demoted)
        _logger.info(
            "knowledge_governance: descriptor approval REVOKED by=%s knowledge_id=%s version=%s section=%s",
            actor_user_id,
            knowledge_id,
            version_label,
            section_id,
        )
        return True


@lru_cache(maxsize=1)
def get_knowledge_governance_service() -> KnowledgeGovernanceService:
    settings = get_settings()
    return KnowledgeGovernanceService(
        get_knowledge_repository(),
        OperationApprovalStore(
            settings.resolve_knowledge_database_url(),
            governors=settings.knowledge_governors,
            governor_roles=settings.knowledge_governor_roles,
            dev_mode=settings.governance_dev_mode,
        ),
    )
