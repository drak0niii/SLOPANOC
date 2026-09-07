"""Structured input/output contract for incident_manager, for this
read-only slice.

These are concrete, ADK-native realizations of the conceptual
`IncidentManagerRequest`/`IncidentManagerResponse` in
docs/AGENT_CONTRACT.md #8:

  - `IncidentManagerRequest` is wired as `incident_manager`'s ADK
    `input_schema` (`LlmAgent.input_schema`: "The input schema when agent
    is used as a tool.") -- when team_manager calls the `incident_manager`
    AgentTool, ADK builds the tool's function-call parameters directly
    from this model's fields, so team_manager's model calls it with
    structured `chat_topic`/`question`/`requested_time_range` arguments
    rather than free text. `requested_time_range` carries the user's
    natural-language time scope, if any -- converting it into UTC
    boundaries is incident_manager's own job (see its prompt), never
    team_manager's or the deterministic tool layer's
    (docs/TEAMS_TOOL_CONTRACT.md #4c).
  - `IncidentManagerResponse` is wired as `incident_manager`'s ADK
    `output_schema`, so its final reply is guaranteed to conform to this
    shape rather than free-form prose.

Both are scoped to only the intents implemented in this slice (chat
discovery + message retrieval + grounded summarization/Q&A). Write-related
fields from the full conceptual contract (`writeDraft`,
`approvedPayloadReference`, `proposal`, `executionResult`) are
intentionally omitted -- they belong to a later slice once write tools
exist.
"""
from __future__ import annotations

from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field

from backend.approval.schemas import WriteOperation


class IncidentManagerRequest(BaseModel):
    """What team_manager sends when delegating to incident_manager --
    Teams-domain work, governed-knowledge work, or both (Phase 5.1J
    correction pass).
    """

    chat_topic: Optional[str] = Field(
        default=None,
        description=(
            "The exact Teams chat title/name as the user stated it, to be "
            "resolved via teams_list_chats -- set ONLY when an external "
            "Teams conversation is actually relevant to this request. "
            "Leave unset for a request that does not need Teams at all "
            "(e.g. a governed-knowledge-only question) -- team_manager "
            "must never fabricate a value here merely because a Teams "
            "chat happens to be currently selected."
        ),
    )
    question: Optional[str] = Field(
        default=None,
        description=(
            "A specific question to answer about the chat, if the user "
            "asked one. Leave unset for a general summary. incident_manager "
            "is stateless and never sees the ongoing conversation, so this "
            "must be a complete, self-contained statement of what is "
            "needed -- team_manager resolves any pronoun/ellipsis/prior "
            "reference (e.g. 'that message', 'it') against its own "
            "conversation history before setting this field, never "
            "forwarding the user's unresolved wording verbatim."
        ),
    )
    requested_time_range: Optional[str] = Field(
        default=None,
        description=(
            "The user's natural-language time scope for this request, "
            "verbatim, if they gave one -- e.g. 'today', 'the last 7 "
            "days', 'since Monday', 'yesterday', 'between 25 August and "
            "28 August'. Leave unset when the user did not mention a time "
            "range (the request then covers the chat's most recent "
            "history, unbounded, exactly as before this field existed). "
            "incident_manager -- not team_manager -- is responsible for "
            "converting this into UTC from_datetime/to_datetime boundaries."
        ),
    )
    requires_governed_knowledge: bool = Field(
        default=False,
        description=(
            "True when governed knowledge (via knowledge_search) is a "
            "REQUIRED source for fulfilling this specific request -- set "
            "by team_manager's own semantic judgment, never inferred from "
            "keywords. False (the default) means the request does not "
            "explicitly require governed knowledge in addition to whatever "
            "else is being asked -- incident_manager may still use "
            "knowledge_search on its own initiative if it judges it useful "
            "(see GOVERNED KNOWLEDGE below), but nothing REQUIRES it to. "
            "This field is also read by the exact-read Teams fast path "
            "(direct_read_fast_path.py) to decide whether a request can be "
            "fully satisfied by a Teams read alone -- when True, that "
            "optimization is skipped so the normal, multi-tool "
            "incident_manager turn can use both Teams and governed "
            "knowledge together."
        ),
    )


class IncidentManagerOutcome(str, Enum):
    OK = "ok"
    NO_RESULT = "no_result"
    AMBIGUOUS = "ambiguous"
    NOT_FOUND = "not_found"
    ERROR = "error"
    # Milestone 3B (approval-protected Teams write execution):
    PROPOSED = "proposed"
    """A write action (`teams.createChat`/`teams.sendMessage`) was
    validated, normalized, and recorded as a pending `ActionProposal` --
    `write_action` carries the safe proposal info. Nothing was sent to
    Teams. Approval is a trusted-application-boundary decision this agent
    has no path to make for itself (docs/AGENT_CONTRACT.md's write-safety
    principle; backend/approval/service.py's module docstring).
    """
    EXECUTED = "executed"
    """A previously approved write action actually executed against
    Power Automate and the now-consumed proposal's result is in
    `write_action`."""
    SELECTION_NEEDED = "selection_needed"
    """No exact chat match for `chat_topic`, but similar chats exist for
    the user to choose from. Leave `chat_id`/`chat_title`/`summary`/
    `write_action`/`candidate_titles` all unset -- never repeat or invent
    candidate names yourself. `detail` may hold a short, plain statement
    that the exact chat was not found and similar ones are available."""


class TeamsEvidence(BaseModel):
    """Thin, message-level provenance supporting a claim in `summary` --
    identifies which retrieved message backs a claim, without repeating
    its text (message bodies are looked up separately from `message_id`
    when needed). Populate only from messages actually retrieved this
    turn -- never invent a `message_id`.
    """

    message_id: str
    author: str
    sent_at: str


class DecisionItem(BaseModel):
    """A confirmed choice, approval, agreement, commitment, or agreed
    direction -- only ever populated when the retrieved messages show
    actual acceptance/agreement/confirmation, never a suggestion or
    discussion alone (see incident_manager's "SEMANTIC CLASSIFICATION"
    instructions: when uncertain, the item belongs in `proposals`
    instead, never here).
    """

    decision: str
    context: Optional[str] = None


class ActionItem(BaseModel):
    """Work someone is expected to perform. `owner`/`due_date`/`status`
    are populated only when explicitly stated in a retrieved message --
    never inferred (e.g. never assume whoever is discussing the action
    owns it, never derive a due date from a meeting date).
    """

    action: str
    owner: Optional[str] = None
    due_date: Optional[str] = None
    status: Optional[str] = None


class ProposalItem(BaseModel):
    """A suggested approach, recommendation, idea, or possible direction
    that has not clearly been accepted or confirmed -- the conservative
    classification for anything that looks decision-adjacent but lacks
    clear agreement (see `DecisionItem`).
    """

    proposal: str
    context: Optional[str] = None


class OpenQuestionItem(BaseModel):
    """An unresolved question, clarification, dependency, or pending
    choice that still needs an answer or decision -- never itself
    presented as a conclusion or a decision.
    """

    question: str
    context: Optional[str] = None
    owner: Optional[str] = None


class RiskItem(BaseModel):
    """A stated issue, dependency, constraint, or condition that could
    delay/prevent progress, or affect quality/security/compliance/the
    intended outcome. `mitigation` is populated only when one was
    actually discussed -- a risk and its mitigation (if any) are kept as
    two distinct fields, never merged into one inferred decision.
    """

    risk: str
    impact: Optional[str] = None
    mitigation: Optional[str] = None


class TeamsWriteActionResult(BaseModel):
    """Safe, user-facing information about a proposed or executed Teams
    write action -- populated only when `outcome` is "proposed" or
    "executed". Excludes `payload_hash` and any Power Automate/credential
    detail. This is a read-back of what the write tool already returned --
    never a value incident_manager invents itself. Has no expiry field --
    never state or estimate an expiry time/countdown.
    """

    operation: WriteOperation
    proposal_id: Optional[str] = Field(
        default=None,
        description="Set when outcome is \"proposed\" -- the pending proposal's id.",
    )
    status: Optional[str] = None
    chat_id: Optional[str] = None
    title: Optional[str] = None
    members: list[str] = Field(default_factory=list)
    message: Optional[str] = None
    web_url: Optional[str] = None
    target_display_name: Optional[str] = Field(
        default=None,
        description=(
            "PRESENTATION ONLY -- the human-readable destination name for "
            "a teams.sendMessage proposal (never the raw chat_id), copied "
            "verbatim from teams_propose_send_message's own result when "
            "present. Never invented, never parsed from conversation "
            "text -- left unset when the tool did not return one. Not "
            "used for teams.createChat, which already has `title`."
        ),
    )


class IncidentManagerResponse(BaseModel):
    """incident_manager's structured reply to team_manager for this slice.

    `summary` may only describe content present in `evidence` (when
    populated) or the retrieved messages generally -- OR, for a request
    that used governed knowledge instead of (or alongside) Teams (Phase
    5.1J), content grounded in evidence actually returned by
    `knowledge_search`. When `outcome` is anything other than "ok",
    `summary`, `evidence`, and the classification fields below are left
    unset/empty, and `detail`/`candidate_titles` carry the explanation
    instead. When `outcome` is "proposed"/"executed", `write_action`
    carries the write-action info instead, and `summary` may hold a short
    human-readable description of the action. `chat_id`/`chat_title`
    legitimately stay unset for an "ok" response that did not involve any
    Teams conversation (`chat_topic` was absent on the request) -- this
    is normal, not an omission to correct.

    `decisions`/`actions`/`proposals`/`open_questions`/`risks` are
    separate, optional/empty-by-default lists -- populate each only when
    the retrieved messages actually contain that kind of content and it
    is relevant to the request. All grounding for entries in these fields
    flows through the single `evidence` list -- there is no separate
    per-category evidence mechanism.
    """

    outcome: IncidentManagerOutcome
    chat_id: Optional[str] = None
    chat_title: Optional[str] = None
    summary: Optional[str] = None
    evidence: list[TeamsEvidence] = Field(default_factory=list)
    decisions: list[DecisionItem] = Field(default_factory=list)
    actions: list[ActionItem] = Field(default_factory=list)
    proposals: list[ProposalItem] = Field(default_factory=list)
    open_questions: list[OpenQuestionItem] = Field(default_factory=list)
    risks: list[RiskItem] = Field(default_factory=list)
    write_action: Optional[TeamsWriteActionResult] = None
    candidate_titles: list[str] = Field(default_factory=list)
    detail: Optional[str] = None
