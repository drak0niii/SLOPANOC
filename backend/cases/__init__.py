"""Persistent Case/Fault context (Phase 4D).

A Case is NOT an ADK session (see docs/... "CORE ARCHITECTURAL PRINCIPLE"
in the Phase 4D instruction): an ADK session is one conversation's
transient/conversational state (history, selected Teams chat, pending
approval); a Case is a durable operational problem that may outlive any
one conversation, be worked on through several sessions, and be shared by
several authorized users. This package knows nothing about ADK sessions,
Team Manager, or Gemini -- it is pure, ADK-independent persistence and
domain logic for Cases, membership, and the Case context ledger.
`backend/api/case_service.py` is the one place that combines this package
with `backend.api.session_service` (ADK session ownership) for the
session-linking operations that genuinely need both.

  - db.py       -- the application's own SQLAlchemy async engine/session
                    factory, bound to the same `SLOPANOC_DATABASE_URL`
                    Phase 4C's ADK `DatabaseSessionService` uses, but a
                    completely separate engine/table set (`slopanoc_*`
                    tables) -- never ADK's own tables or engine internals.
  - models.py   -- SQLAlchemy ORM models (`CaseRecord`,
                    `CaseMembershipRecord`, `CaseSessionLinkRecord`,
                    `CaseContextItemRecord`).
  - schemas.py  -- Pydantic DTOs and the closed enums (`CaseStatus`,
                    `CaseMemberRole`, `ContextItemKind`, `SourceType`)
                    that enforce the epistemic/provenance boundaries.
  - service.py  -- deterministic Case domain operations (create/get/list/
                    update Case, membership, context-item read/write,
                    including the restricted agent-analysis write path).
  - snapshot.py -- the deterministic, budgeted `CaseContextSnapshot`
                    builder used for model-facing context injection.
"""
