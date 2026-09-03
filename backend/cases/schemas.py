"""Data contracts for the Case/Fault context domain.

The enums here ARE the epistemic-boundary enforcement mechanism
(instruction section 11): `ContextItemKind` is a closed set precisely so
"observation", "hypothesis", "recommendation", "decision", "action", and
"resolution" can never be silently flattened into one undifferentiated
"fact" -- every context item is tagged with exactly one of these kinds,
for its entire lifetime (items are append-only, never reclassified in
place -- instruction section 34).
"""
from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel, Field


class CaseStatus(str, Enum):
    OPEN = "open"
    INVESTIGATING = "investigating"
    MONITORING = "monitoring"
    RESOLVED = "resolved"
    CLOSED = "closed"


class CaseMemberRole(str, Enum):
    OWNER = "owner"
    MEMBER = "member"


class ContextItemKind(str, Enum):
    """The closed set of semantic kinds a `CaseContextItem` may carry --
    see this module's docstring. Split into two groups for the
    agent-write restriction (instruction section 15):
    `ANALYSIS_KINDS` (what the model may persist directly) is a strict
    subset of the full set (what a human/deterministic source may
    persist).
    """

    OBSERVATION = "observation"
    EVIDENCE = "evidence"
    HYPOTHESIS = "hypothesis"
    RECOMMENDATION = "recommendation"
    DECISION = "decision"
    ACTION = "action"
    RISK = "risk"
    OPEN_QUESTION = "open_question"
    RESOLUTION = "resolution"


# Instruction section 15: "Allow the agent layer to persist only
# clearly-labelled ANALYSIS types" -- hypothesis/recommendation/
# open_question ONLY. Never evidence, observation, decision, action, or
# resolution from the model directly (instruction: these require "a
# future trusted deterministic source/user confirmation").
AGENT_ANALYSIS_KINDS: frozenset[ContextItemKind] = frozenset(
    {ContextItemKind.HYPOTHESIS, ContextItemKind.RECOMMENDATION, ContextItemKind.OPEN_QUESTION}
)


class SourceType(str, Enum):
    """Where a context item's content actually came from -- instruction
    section 13's prepared-but-not-yet-integrated source list. Only
    `USER`, `AGENT`, and `SYSTEM` are actually reachable through this
    milestone's API/agent capability; `TEAMS`/`TICKET`/`ALARM`/
    `KNOWLEDGE` exist so the schema does not need to change when those
    connectors are integrated later.
    """

    USER = "user"
    TEAMS = "teams"
    TICKET = "ticket"
    ALARM = "alarm"
    KNOWLEDGE = "knowledge"
    AGENT = "agent"
    SYSTEM = "system"
    OTHER = "other"


class CaseDTO(BaseModel):
    case_id: str
    title: str
    problem_statement: str
    external_reference: Optional[str] = None
    status: CaseStatus
    created_by_user_id: str
    created_at: datetime
    updated_at: datetime


class CaseListItemDTO(BaseModel):
    """Deliberately smaller than `CaseDTO` -- concise enough for a future
    sidebar/workspace list (instruction section 30).
    """

    case_id: str
    title: str
    status: CaseStatus
    updated_at: datetime


class CaseMembershipDTO(BaseModel):
    case_id: str
    user_id: str
    role: CaseMemberRole
    created_at: datetime


class CaseContextItemDTO(BaseModel):
    item_id: str
    case_id: str
    kind: ContextItemKind
    content: str
    source_type: SourceType
    source_ref: Optional[str] = None
    source_timestamp: Optional[datetime] = None
    source_author: Optional[str] = None
    confidence: Optional[float] = None
    created_by_user_id: Optional[str] = None
    created_by_agent: Optional[str] = None
    created_at: datetime
    supporting_item_ids: list[str] = Field(default_factory=list)


class CaseSessionLinkDTO(BaseModel):
    case_id: str
    session_id: str
    session_user_id: str
    linked_at: datetime
    linked_by_user_id: str


class CaseContextSnapshotItem(BaseModel):
    """One entry in a model-facing `CaseContextSnapshot` -- a trimmed
    projection of `CaseContextItemDTO` (no internal-only fields).
    """

    kind: ContextItemKind
    content: str
    source_type: SourceType
    source_author: Optional[str] = None
    created_at: datetime


class CaseContextSnapshot(BaseModel):
    """The deterministic, budgeted, model-facing view of a Case --
    instruction section 18. Never the full ledger; see snapshot.py for
    the selection/truncation algorithm. `context_truncated` must be
    checked (and, when true, surfaced) by anything that renders this --
    never silently presented as the complete Case history.
    """

    case_id: str
    title: str
    status: CaseStatus
    problem_statement: str
    external_reference: Optional[str] = None
    items: list[CaseContextSnapshotItem] = Field(default_factory=list)
    context_truncated: bool = False
    total_item_count: int = 0
    included_item_count: int = 0
