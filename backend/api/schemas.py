"""Request/response data contracts for the SLOPANOC backend API.

Kept deliberately small and frontend-shaped -- never a passthrough of any
ADK/Gemini object. See pending_action.py for how `PendingActionDTO` is
populated (deterministic Python, not model output).
"""
from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, Field

from backend.cases.schemas import CaseMemberRole, CaseStatus, ContextItemKind


class CreateSessionResponse(BaseModel):
    session_id: str


class SendMessageRequest(BaseModel):
    message: str = Field(min_length=1, description="The user's message to Team Manager.")


class RewindSessionRequest(BaseModel):
    """Body for `POST /api/sessions/{id}/rewind` (Phase 4G hardening pass
    -- conversational branching for editing a historical user message).

    `before_user_turn_index` is 0-based: an index into this session's
    CURRENTLY ACTIVE user-authored turns (the ones a fresh model turn
    would still see as conversation history) -- never a raw ADK event
    index and never an invocation id, both internal identifiers the
    frontend never sees or computes. The turn at this index, and
    everything chronologically after it, becomes inactive for every
    future turn on this session -- still stored, never deleted (see
    chat_service.py's `rewind_before_user_turn`).
    """

    before_user_turn_index: int = Field(
        ge=0, description="0-based index of the active user turn to rewind to."
    )


class RewindSessionResponse(BaseModel):
    session_id: str


class CancelRunResponse(BaseModel):
    """Response for `POST /api/sessions/{session_id}/runs/{run_id}/cancel`
    (pre-4H refinement -- real server-side Stop). `cancelled` is `True`
    only when an actual `asyncio.Task.cancel()` was issued against a
    genuinely still-running task for this exact `run_id`; `False` covers
    every safe no-op case uniformly (the run already finished naturally,
    `run_id` is stale because a newer run has since started, or `run_id`
    was never valid for this session) -- never an error, since a client
    racing its own Stop click against the run's natural completion is a
    completely normal, harmless timing window (see
    chat_service.ChatService.cancel_run's own docstring).
    """

    session_id: str
    run_id: str
    cancelled: bool


class AssistantMessage(BaseModel):
    role: Literal["assistant"] = "assistant"
    content: str


class PendingActionDTO(BaseModel):
    """Safe, frontend-facing view of the session's active `ActionProposal`
    (if any). Deliberately excludes `payload_hash`, any Power Automate
    detail, and any credential -- see pending_action.py's module
    docstring for the deterministic mapping this is built from.

    `status` reflects the proposal's CURRENT effective status (dynamic
    expiry included), not just what was last persisted -- see
    `backend.approval.service.effective_status`, reused unchanged here.
    """

    proposal_id: str
    operation: str
    status: str
    summary: Optional[str] = None
    title: Optional[str] = None
    members: list[str] = Field(default_factory=list)
    chat_id: Optional[str] = None
    message: Optional[str] = None
    expires_at: str
    expires_in_seconds: int
    expires_in_minutes: int
    target_display_name: Optional[str] = Field(
        default=None,
        description=(
            "PRESENTATION ONLY -- human-readable destination name (e.g. "
            "a Teams chat's topic) for a teams.sendMessage proposal, "
            "mirroring ActionProposal.target_display_name unchanged. "
            "Never the raw chat_id. Unset when no authoritative name was "
            "available. For teams.createChat, the frontend uses `title` "
            "instead; this field is not populated for that operation."
        ),
    )


class ActiveCaseDTO(BaseModel):
    """Minimal case-awareness signal for the chat response (instruction
    section 32) -- never the full ledger; the frontend queries the Case
    endpoints separately for that.
    """

    case_id: str
    title: str
    status: CaseStatus


class SourceEvidenceItem(BaseModel):
    """One safe, already-validated, DISPLAY-WORTHY supporting reference --
    author/timestamp from `backend.agents.incident_manager.schemas.
    TeamsEvidence` (minus `message_id`, an internal Teams identifier never
    exposed to the frontend -- see `SourceReferenceDTO`'s own docstring),
    plus a snippet built entirely by deterministic backend code.

    `snippet` (snippet-authenticity fix, post UX/provenance polish pass)
    is REQUIRED here and always non-empty -- `source_reference.
    build_teams_source_reference` now only constructs a `SourceEvidenceItem`
    at all once it has independently looked up that message's own
    retrieved text (via `TEAMS_MESSAGE_TEXT_BY_ID_STATE_KEY`, forwarded
    from `teams_get_messages`) and derived a valid, non-empty excerpt from
    it via `_safe_snippet` -- NEVER from anything the model reproduces
    (`TeamsEvidence` no longer even has a `snippet` field). An evidence
    candidate for which no valid excerpt could be built (empty/system/
    filtered/unavailable message text) simply never becomes a
    `SourceEvidenceItem` -- see that function's own docstring for the
    "skip and keep scanning" selection algorithm. Still never a full
    message body and never more than one message's content.
    """

    author: str
    sent_at: str
    snippet: str


class SourceReferenceDTO(BaseModel):
    """Safe, structured provenance for a Teams-derived answer (pre-4H
    UX/provenance milestone) -- the frontend's source drawer renders this
    directly; never parses the assistant's own answer text to reconstruct
    it (see backend/api/source_reference.py's module docstring for how
    this is built, deterministically, from the same already-validated
    `evidence`/`chat_title` fields `activity_translator.py` already
    inspects for status/trace purposes).

    Deliberately excludes the raw Teams `chat_id`, any membership/user id,
    Power Automate/connector detail, and any credential -- `title`
    (the chat's human-readable topic) and `contributors` (display names)
    are the same class of already-safe, already-presented values this API
    already returns elsewhere (`PendingActionDTO.target_display_name`,
    `SourceEvidenceItem.author`).

    `evidence` holds at most `source_reference.MAX_EVIDENCE_ITEMS`
    entries (UX polish pass: "supporting evidence, curated, not a full
    dump") -- but, per the snippet-authenticity fix, it is "up to
    `MAX_EVIDENCE_ITEMS` VALID, snippet-bearing examples," never "the
    first `MAX_EVIDENCE_ITEMS` evidence records regardless of whether a
    snippet could be built." `build_teams_source_reference` scans every
    candidate in order and skips any for which no valid excerpt could be
    derived, so `evidence` may legitimately hold fewer than
    `MAX_EVIDENCE_ITEMS` entries -- but only when fewer than
    `MAX_EVIDENCE_ITEMS` valid, displayable examples actually exist, never
    as a shortcut. `message_count`/`period_start`/`period_end` are always
    derived from the FULL, uncapped set of valid (author/sent_at present)
    evidence the turn actually produced, regardless of how many of those
    had a displayable snippet -- so capping/filtering the displayed
    examples never changes what those summary fields report. That cap is
    a display-only limit on the drawer's examples -- it never bounds how
    much retrieved Teams content incident_manager actually considered when
    writing `summary`/the structured decision/action/proposal/
    open-question/risk fields (see team_manager/prompts.py and
    incident_manager/prompts.py for the explicit contract).

    `contributors` (contributor-accuracy fix, post pre-4H milestone) is
    resolved authoritatively from the chat's real Teams membership
    (`teams_get_members`, docs/TEAMS_TOOL_CONTRACT.md #5) -- NOT derived
    from `evidence` authors, so a participant who wrote none of the
    (capped) displayed evidence examples still appears here. See
    source_reference.py's `resolve_authoritative_contributors`.
    """

    source_id: str
    source_type: Literal["teams"]
    label: str
    title: Optional[str] = None
    message_count: Optional[int] = None
    period_start: Optional[str] = None
    period_end: Optional[str] = None
    contributors: list[str] = Field(default_factory=list)
    evidence: list[SourceEvidenceItem] = Field(default_factory=list)


class KnowledgeSourceReferenceDTO(BaseModel):
    """Safe, structured provenance for a governed-knowledge-derived
    answer (Phase 5.1J correction pass, Part C) -- the frontend's source
    drawer renders this directly, exactly like `SourceReferenceDTO`, but
    for the KM evidence path. Built ONLY from a trusted, backend-side
    `KnowledgeEvidenceItem` the model explicitly SELECTED this turn (via
    `knowledge_select_evidence`) -- never from `agent_payload`, never
    from the model's own answer text, and never from every item
    `knowledge_search` merely returned (AVAILABLE EVIDENCE != SELECTED
    EVIDENCE). See backend/api/knowledge_source_reference.py for how this
    is constructed.

    Deliberately excludes `KnowledgeSource.source_uri` -- see
    docs/KNOWLEDGE_CONTRACT.md's Phase 5.1J section, "DO NOT EXPOSE
    source_uri YET": the trusted backend `KnowledgeEvidenceItem` still
    retains it, but it never reaches this DTO, the API response, or any
    log. `content` here is the EXACT, untruncated, unparaphrased
    governed section text this answer was built from -- never a model-
    generated summary/snippet, mirroring `SourceEvidenceItem.snippet`'s
    own "never from the model" discipline for Teams.
    """

    source_id: str
    """Synthetic per-DTO identity (a `uuid4`), mirroring `SourceReferenceDTO
    .source_id` exactly -- NOT the governed `KnowledgeSource.source_id`
    (see `evidence_source_id` for that)."""
    source_type: Literal["knowledge"]
    label: str
    knowledge_id: str
    version_label: str
    section_id: str
    title: str
    document_type: str
    source_system: str
    evidence_source_id: str
    source_display_name: Optional[str] = None
    section_heading: Optional[str] = None
    source_locator: Optional[str] = None
    content: str


class ChatResponse(BaseModel):
    session_id: str
    message: AssistantMessage
    pending_action: Optional[PendingActionDTO] = None
    active_case: Optional[ActiveCaseDTO] = None


class ApprovalRequest(BaseModel):
    """Body for `/approve` and `/reject` (Phase 4B). The client supplies
    ONLY `proposal_id` -- there is no field for status, operation, or
    payload, so nothing about the transition's actual effect is
    client-controlled (instruction section 11).
    """

    proposal_id: str = Field(min_length=1, description="Must match the session's current active proposal.")


class ApprovalResponse(BaseModel):
    session_id: str
    result: Literal["approved", "rejected"]
    pending_action: Optional[PendingActionDTO] = None


class ExecutedActionDTO(BaseModel):
    """Safe, minimal view of what Power Automate actually confirmed for a
    just-executed action (Phase 4G) -- 1:1 with
    backend/tools/teams/execute_write.py's own returned dict on success.
    Never `payload_hash`, never a raw Power Automate response, never a
    field the gateway didn't actually return (nothing here is ever
    fabricated).
    """

    chat_id: Optional[str] = None
    title: Optional[str] = None
    web_url: Optional[str] = None


class ExecuteActionResponse(BaseModel):
    """Response for `POST /api/sessions/{id}/execute` (Phase 4G).
    Deliberately a DIFFERENT shape from `ApprovalResponse` -- approval and
    execution are different facts, and this endpoint is the only one that
    can ever report `executed_action`. A denial/failure never reaches this
    model at all -- it is raised as a `SafeErrorException` instead (same
    contract every other endpoint already uses), so `result` here is only
    ever the literal `"executed"`.
    """

    session_id: str
    result: Literal["executed"] = "executed"
    pending_action: Optional[PendingActionDTO] = None
    executed_action: Optional[ExecutedActionDTO] = None


class HealthResponse(BaseModel):
    status: Literal["ok"] = "ok"


# --- Case/Fault context (Phase 4D) ------------------------------------------


class CreateCaseRequest(BaseModel):
    """Deliberately small (instruction section 29) -- the server supplies
    `case_id`, `created_by_user_id` (from `UserContext`, never this
    body), `status`, and timestamps.
    """

    title: str = Field(min_length=1)
    problem_statement: str = Field(min_length=1)
    external_reference: Optional[str] = None


class UpdateCaseRequest(BaseModel):
    """Every field optional -- a PATCH updates only what is supplied.
    `status` is still restricted to `CaseStatus`'s closed enum by Pydantic
    itself; there is no way to set an arbitrary string.
    """

    title: Optional[str] = None
    problem_statement: Optional[str] = None
    status: Optional[CaseStatus] = None
    external_reference: Optional[str] = None


class CaseResponse(BaseModel):
    case_id: str
    title: str
    problem_statement: str
    external_reference: Optional[str] = None
    status: CaseStatus
    created_by_user_id: str
    created_at: str
    updated_at: str


class CaseListItemResponse(BaseModel):
    case_id: str
    title: str
    status: CaseStatus
    updated_at: str


class CaseListResponse(BaseModel):
    cases: list[CaseListItemResponse] = Field(default_factory=list)


class AddCaseMemberRequest(BaseModel):
    """`user_id` here is the identity being ADDED as a Case member --
    NOT the requesting user (that always comes from `UserContext`; see
    identity.py/app.py). Never confuse the two (instruction section 6).
    """

    user_id: str = Field(min_length=1)
    role: CaseMemberRole = CaseMemberRole.MEMBER


class CaseMembershipResponse(BaseModel):
    case_id: str
    user_id: str
    role: CaseMemberRole


class CaseSessionLinkResponse(BaseModel):
    case_id: str
    session_id: str
    linked_at: str


class AddCaseContextItemRequest(BaseModel):
    """User-facing context-item creation. There is deliberately no
    `source_type`/`source_author`/`created_by_user_id` field here -- the
    API always records `source_type="user"` and the resolved
    `UserContext` as author (instruction section 14); a client cannot
    claim Teams/ticket/alarm provenance or another user's authorship.
    """

    kind: ContextItemKind
    content: str = Field(min_length=1)


class CaseContextItemResponse(BaseModel):
    item_id: str
    case_id: str
    kind: ContextItemKind
    content: str
    source_type: str
    source_author: Optional[str] = None
    created_at: str
    supporting_item_ids: list[str] = Field(default_factory=list)


class CaseContextResponse(BaseModel):
    items: list[CaseContextItemResponse] = Field(default_factory=list)


# --- Interactive selection (interaction-capability extension) ---------------


class SelectionOptionDTO(BaseModel):
    """Safe, frontend-facing option -- an opaque `option_id` and a
    human-readable `label` ONLY. Never a raw underlying identifier (e.g.
    a Teams chat id) -- see pending_selection.py's module docstring for
    the deterministic mapping this is built from.
    """

    option_id: str
    label: str


class PendingSelectionDTO(BaseModel):
    """Safe, frontend-facing view of the session's active
    `PendingSelection` (if any) -- the generic analogue of
    `PendingActionDTO`. Deliberately excludes `option_targets`/
    `pending_write_message`/any raw Teams identifier -- see
    pending_selection.py's module docstring for the deterministic
    mapping this is built from.
    """

    selection_id: str
    kind: str
    status: str
    requested_value: str
    options: list[SelectionOptionDTO] = Field(default_factory=list)


class ChooseSelectionRequest(BaseModel):
    """Body for `POST /api/sessions/{id}/selections/{id}/choose`. The
    client supplies ONLY `option_id` -- there is no field for a raw chat
    id/topic, so nothing about which real resource gets selected is
    client-controlled beyond picking one of the options the server itself
    already offered (instruction section 32).
    """

    option_id: str = Field(min_length=1)


class ChooseSelectionResponse(BaseModel):
    session_id: str
    selection_id: str
    status: str
    selected_label: str
    # Populated only when resolving this selection completed a pending
    # WRITE intent (a `teams.sendMessage` whose destination was
    # ambiguous) -- deterministically, with no further model involvement
    # (see selection_service.py). `None` for a READ intent.
    pending_action: Optional[PendingActionDTO] = None
    # Hardening pass: populated only when resolving this selection
    # completed a pending READ intent (`None` for a write, which uses
    # `pending_action` instead) -- the deterministic, destination-free
    # text (see selection/read_resume.py) the frontend passes, unchanged,
    # as the message for a brand-new backend turn that resumes the read
    # against the now-authoritative selected chat. Never the user's own
    # original request text (that text still names the OLD, unresolved
    # destination -- see selection/schemas.py's `PendingReadIntent`
    # docstring for why replaying it reopens the same ambiguity).
    resume_message: Optional[str] = None


class SkipSelectionResponse(BaseModel):
    session_id: str
    selection_id: str
    status: str
