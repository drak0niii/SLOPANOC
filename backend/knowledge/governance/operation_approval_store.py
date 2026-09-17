"""POST-6A -- Persistence, permission and revocation for operation
descriptor approvals.

`operation_approval.py` owns the PURE rules (what an approval must match
to promote a descriptor). This module owns the parts that cannot be pure:

  - WHO may approve. An `OperationApprovalRecord` is DATA. Anyone able to
    construct one could otherwise hand it to `approve_section_operation`
    and get an APPROVED descriptor -- which is the same caller-asserted
    authority the removed `governance_transition_approved=True` boolean
    had, just wearing a typed hat. `create_approval` therefore builds the
    record SERVER-SIDE, after verifying the actor's governance
    permission, and refuses to accept a caller-supplied record at all.
  - WHAT was approved, durably: reviewer identity, timestamp, the exact
    (knowledge_id, version_label, section_id), and the descriptor
    fingerprint at the moment of approval.
  - REVOCATION: an approval can be withdrawn, and a withdrawn approval
    stops promoting immediately.
  - INVALIDATION AFTER EDIT: an approval is bound to a fingerprint, so
    editing the descriptor leaves the stored approval matching nothing.
    `active_approval_for` returns it only when the fingerprint still
    matches the descriptor actually on the section.

WHAT THIS DELIBERATELY IS NOT: a workflow engine, a multi-reviewer
quorum, or an audit subsystem. One reviewer, one record, one revocation
flag -- the minimum that makes approval a real, attributable, revocable
human act rather than a function argument.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import Boolean, DateTime, Text, select
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from backend.knowledge.domain.models import KnowledgeObject
from backend.knowledge.governance.contracts import KnowledgeGovernanceError
from backend.knowledge.governance.operation_approval import (
    OperationApprovalRecord,
    approve_section_operation,
    descriptor_fingerprint,
)

__all__ = [
    "GovernancePermissionError",
    "GovernanceUnavailableError",
    "OperationApprovalStore",
    "OperationApprovalTable",
]

_TABLE_NAME = "slopanoc_operation_approvals"


class GovernanceUnavailableError(KnowledgeGovernanceError):
    """Governance mutations are not available in this deployment.

    Distinct from `GovernancePermissionError` on purpose: that one means
    "you are not a governor"; this one means "this deployment cannot
    verify who anyone is, so nobody may perform a privileged governance
    act here at all". Conflating them would let a production deployment
    look like it merely had the wrong allowlist.
    """


class GovernancePermissionError(KnowledgeGovernanceError):
    """The actor is not permitted to approve governed operations.

    Raised BEFORE any record is created -- so an unauthorized actor never
    produces an approval artifact at all, rather than producing one that
    is later ignored.
    """


class Base(DeclarativeBase):
    pass


class OperationApprovalTable(Base):
    """One human approval decision. Keyed by
    (knowledge_id, version_label, section_id) -- an approval is always
    about exactly one descriptor on one section of one version."""

    __tablename__ = _TABLE_NAME

    approval_id: Mapped[str] = mapped_column(Text, primary_key=True)
    knowledge_id: Mapped[str] = mapped_column(Text, index=True)
    version_label: Mapped[str] = mapped_column(Text)
    section_id: Mapped[str] = mapped_column(Text)
    descriptor_fingerprint: Mapped[str] = mapped_column(Text)
    approved_by: Mapped[str] = mapped_column(Text)
    approved_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    note: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    revoked: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="false")
    revoked_by: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    revoked_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)


def _approval_id(knowledge_id: str, version_label: str, section_id: str) -> str:
    """Deterministic -- re-approving the same descriptor on the same
    section of the same version replaces the previous decision rather
    than accumulating duplicates nobody can reconcile."""
    return f"{knowledge_id}\x1f{version_label}\x1f{section_id}"


class OperationApprovalStore:
    """Durable approval records, plus the ONE permission gate."""

    def __init__(
        self,
        database_url: str,
        *,
        governors: frozenset[str],
        governor_roles: frozenset[str] = frozenset(),
        dev_mode: bool = False,
        manage_schema: bool = False,
    ) -> None:
        self._engine: AsyncEngine = create_async_engine(database_url, future=True)
        self._session_factory = async_sessionmaker(self._engine, expire_on_commit=False)
        self._governors = governors
        self._governor_roles = governor_roles
        self._dev_mode = dev_mode
        # POST-6A -- NO LAZY DDL IN NORMAL RUNTIME. Schema ownership
        # belongs to Alembic (`f1b6c3d05a27`). `manage_schema=True` is
        # for isolated tests and one-off local setup only, where creating
        # the table in-process is the point.
        self._manage_schema = manage_schema
        self._schema_ready = not manage_schema

    async def ensure_schema(self) -> None:
        """A no-op unless this store was constructed with
        `manage_schema=True`.

        `create_all` is idempotent and never drops or alters an existing
        table, so an already-migrated, compatible table keeps its data
        untouched even when a test-mode store runs against it.
        """
        if self._schema_ready or not self._manage_schema:
            return
        async with self._engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        self._schema_ready = True

    def require_governance_available(self, *, verified: bool = False) -> None:
        """Governance mutations require a caller whose identity was
        actually established.

        POST-6A -- TWO WAYS TO SATISFY THIS, and only two:

        1. `verified=True` -- the caller presented a verified token. This
           is the production path, and it needs NO development flag: a
           real authenticated identity is sufficient on its own merits.
        2. `dev_mode=True` -- the deployment has explicitly declared
           itself a development environment, where the unverified header
           is knowingly accepted.

        Neither present means governance is unavailable here. That is the
        honest state for a production deployment with no authentication
        configured: it cannot tell who is asking, so it must not perform a
        privileged act for them.
        """
        if verified or self._dev_mode:
            return
        raise GovernanceUnavailableError(
            "governance mutations are disabled: this deployment has no verified authentication configured"
        )

    def require_governance_permission(
        self, actor_user_id: str, *, verified: bool = False, roles: tuple[str, ...] = ()
    ) -> None:
        """THE permission gate. AUTHENTICATION and AUTHORIZATION, checked
        separately and in that order.

        AUTHENTICATION (`require_governance_available`): do we actually
        know who this is?

        AUTHORIZATION: is this identity permitted? Satisfied by EITHER a
        role from the verified token that is in the configured governor
        ROLE set, OR membership of the configured governor USER set. The
        role path is what makes production governance usable without any
        development flag; the user-id path remains for a deployment whose
        IdP issues no suitable role.

        Deny-by-default throughout: empty role and user sets mean nobody
        may approve. A capability whose default is "anyone" is not a
        boundary.
        """
        self.require_governance_available(verified=verified)
        if verified and self._governor_roles and set(roles) & self._governor_roles:
            return
        if actor_user_id and actor_user_id in self._governors:
            return
        raise GovernancePermissionError(
            "this user is not permitted to approve governed operation descriptors"
        )

    async def create_approval(
        self,
        knowledge_object: KnowledgeObject,
        section_id: str,
        *,
        actor_user_id: str,
        verified: bool = False,
        roles: tuple[str, ...] = (),
        note: Optional[str] = None,
        now: Optional[datetime] = None,
    ) -> OperationApprovalRecord:
        """Verify permission, then build the approval record SERVER-SIDE
        from the descriptor actually on the section right now.

        The caller never supplies the fingerprint (or any other field of
        the record): it is computed here, from real content, so an actor
        cannot approve descriptor A while presenting the fingerprint of
        descriptor B.
        """
        self.require_governance_permission(actor_user_id, verified=verified, roles=roles)
        await self.ensure_schema()

        section = next((s for s in knowledge_object.sections if s.section_id == section_id), None)
        if section is None or section.operation is None:
            raise KnowledgeGovernanceError(
                f"section {section_id!r} carries no operation descriptor to approve"
            )

        record = OperationApprovalRecord(
            knowledge_id=knowledge_object.knowledge_id,
            version_label=knowledge_object.version.label,
            section_id=section_id,
            descriptor_fingerprint=descriptor_fingerprint(section.operation),
            approved_by=actor_user_id,
            approved_at=now or datetime.now(timezone.utc),
            note=note,
        )

        async with self._session_factory() as session:
            approval_id = _approval_id(record.knowledge_id, record.version_label, record.section_id)
            existing = await session.get(OperationApprovalTable, approval_id)
            if existing is None:
                session.add(
                    OperationApprovalTable(
                        approval_id=approval_id,
                        knowledge_id=record.knowledge_id,
                        version_label=record.version_label,
                        section_id=record.section_id,
                        descriptor_fingerprint=record.descriptor_fingerprint,
                        approved_by=record.approved_by,
                        approved_at=record.approved_at,
                        note=record.note,
                        revoked=False,
                    )
                )
            else:
                existing.descriptor_fingerprint = record.descriptor_fingerprint
                existing.approved_by = record.approved_by
                existing.approved_at = record.approved_at
                existing.note = record.note
                existing.revoked = False
                existing.revoked_by = None
                existing.revoked_at = None
            await session.commit()
        return record

    async def invalidate_on_edit(self, knowledge_id: str, version_label: str, section_id: str) -> bool:
        """POST-6A -- an EDIT explicitly invalidates the prior approval.

        The fingerprint check in `active_approval_for` already makes an
        edited descriptor unapprovable, but leaving the row un-revoked
        means a review surface still shows it as approved, and a future
        reader could mistake presence for authority. This marks it
        revoked outright, attributed to the edit rather than to a person.

        Deliberately NOT permission-gated: invalidating an approval only
        ever REMOVES authority, and it must happen whenever content
        changes regardless of who changed it.
        """
        await self.ensure_schema()
        async with self._session_factory() as session:
            row = await session.get(
                OperationApprovalTable, _approval_id(knowledge_id, version_label, section_id)
            )
            if row is None or row.revoked:
                return False
            row.revoked = True
            row.revoked_by = "system:descriptor-edited"
            row.revoked_at = datetime.now(timezone.utc)
            await session.commit()
            return True

    async def revoke_approval(
        self,
        knowledge_id: str,
        version_label: str,
        section_id: str,
        *,
        actor_user_id: str,
        verified: bool = False,
        roles: tuple[str, ...] = (),
        now: Optional[datetime] = None,
    ) -> bool:
        """Withdraw an approval. Returns `False` when there was nothing to
        revoke -- not an error: revoking an already-absent approval leaves
        the system in exactly the state the caller wanted."""
        self.require_governance_permission(actor_user_id, verified=verified, roles=roles)
        await self.ensure_schema()
        async with self._session_factory() as session:
            row = await session.get(
                OperationApprovalTable, _approval_id(knowledge_id, version_label, section_id)
            )
            if row is None or row.revoked:
                return False
            row.revoked = True
            row.revoked_by = actor_user_id
            row.revoked_at = now or datetime.now(timezone.utc)
            await session.commit()
            return True

    async def active_approval_for(
        self, knowledge_object: KnowledgeObject, section_id: str
    ) -> Optional[OperationApprovalRecord]:
        """The approval that currently authorizes this section's
        descriptor, or `None`.

        Returns `None` when the approval is revoked, OR when the stored
        fingerprint no longer matches the descriptor on the section --
        which is how an EDIT invalidates a prior approval without anyone
        having to remember to revoke it.
        """
        await self.ensure_schema()
        section = next((s for s in knowledge_object.sections if s.section_id == section_id), None)
        if section is None or section.operation is None:
            return None
        async with self._session_factory() as session:
            row = await session.get(
                OperationApprovalTable,
                _approval_id(knowledge_object.knowledge_id, knowledge_object.version.label, section_id),
            )
        if row is None or row.revoked:
            return None
        if row.descriptor_fingerprint != descriptor_fingerprint(section.operation):
            return None
        return OperationApprovalRecord(
            knowledge_id=row.knowledge_id,
            version_label=row.version_label,
            section_id=row.section_id,
            descriptor_fingerprint=row.descriptor_fingerprint,
            approved_by=row.approved_by,
            approved_at=row.approved_at,
            note=row.note,
        )

    async def apply_active_approvals(self, knowledge_object: KnowledgeObject) -> KnowledgeObject:
        """Promote every section whose descriptor has a live, matching
        approval. THIS is what turns a stored human decision into runtime
        authority -- and it is the only thing that does.

        A section with no approval, a revoked one, or one whose
        fingerprint no longer matches simply stays CANDIDATE. Nothing is
        promoted in bulk and nothing is promoted implicitly.
        """
        result = knowledge_object
        for section in knowledge_object.sections:
            if section.operation is None:
                continue
            approval = await self.active_approval_for(knowledge_object, section.section_id)
            if approval is None:
                continue
            result = approve_section_operation(result, approval)
        return result

    async def list_approvals(self, knowledge_id: Optional[str] = None) -> list[OperationApprovalTable]:
        """For the review interface. Returns rows, including revoked ones
        -- a review surface that hides revocations is not a review
        surface."""
        await self.ensure_schema()
        async with self._session_factory() as session:
            query = select(OperationApprovalTable)
            if knowledge_id:
                query = query.where(OperationApprovalTable.knowledge_id == knowledge_id)
            result = await session.execute(query.order_by(OperationApprovalTable.approved_at.desc()))
            return list(result.scalars().all())

    async def close(self) -> None:
        await self._engine.dispose()
