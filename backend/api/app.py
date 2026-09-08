"""The SLOPANOC backend API (Phase 4A + 4B + 4C + 4D + 4E + 4G).

FRAMEWORK: FastAPI -- already present in this environment as a
`google-adk` dependency (it backs `adk web`'s own dev UI), so this adds
no new third-party dependency; picked per instruction ("If there is no
HTTP framework already present, FastAPI is acceptable" -- one already is).

ROUTES:
  GET  /health                              -- liveness only.
  POST /api/sessions                        -- create a new session,
                                                owned by the resolved
                                                caller (see identity.py).
  GET  /api/sessions                        -- (POST-5.1 B4B) the
                                                caller's own saved-chat
                                                list, safe summaries only
                                                (see
                                                session_history_service.py).
  GET  /api/sessions/{session_id}/history   -- (POST-5.1 B4B) the safe,
                                                active-branch transcript
                                                for one session -- never
                                                raw ADK events/state.
  PATCH /api/sessions/{session_id}          -- (POST-5.1 B4B) durable
                                                manual rename.
  POST /api/sessions/{session_id}/messages  -- send a message, get Team
                                                Manager's reply + any
                                                pending action + active
                                                Case (if linked).
  POST /api/sessions/{session_id}/messages/stream -- (Phase 4E) SSE
                                                variant of the above --
                                                same canonical pipeline
                                                (chat_service.py's
                                                `execute_turn_events`),
                                                emits normalized
                                                run.started/status/
                                                status.clear/message.delta/
                                                message.completed/
                                                action.pending/error/
                                                run.completed events (see
                                                streaming_events.py).
  POST /api/sessions/{session_id}/runs/{run_id}/cancel -- (pre-4H
                                                refinement) real server-
                                                side Stop: cancels the
                                                exact tracked background
                                                task driving that run, if
                                                still running (see
                                                chat_service.ChatService
                                                .cancel_run). Idempotent/
                                                safe for an already-
                                                finished or stale run_id.
  POST /api/sessions/{session_id}/rewind    -- (Phase 4G hardening pass)
                                                editing a historical user
                                                message: rewinds the
                                                session so a stale future
                                                turn is excluded from
                                                model context on the NEXT
                                                message sent to this same
                                                session id (never a new
                                                session -- see
                                                chat_service.py's
                                                `rewind_before_user_turn`).
  POST /api/sessions/{session_id}/approve   -- (Phase 4B) trusted
  POST /api/sessions/{session_id}/reject       approve/reject of the
                                                session's active
                                                proposal -- NEVER routes
                                                through Team Manager,
                                                Incident Manager, Gemini,
                                                or any tool (see
                                                approval_service.py's
                                                module docstring). Never
                                                executes a Teams write.
  POST /api/sessions/{session_id}/execute   -- (Phase 4G) the ONLY route
                                                that can actually execute
                                                an approved proposal --
                                                deterministic, never
                                                through Gemini/the Runner
                                                (see execution_service.py's
                                                module docstring). Approve
                                                alone never reaches this.
  POST /api/sessions/{session_id}/selections/{selection_id}/choose --
                                                (interaction-capability
                                                extension) resolve a
                                                pending Teams chat-name
                                                disambiguation to one of
                                                the offered candidates --
                                                destination resolution
                                                ONLY, never write approval
                                                (see selection_service.py's
                                                module docstring).
  POST /api/sessions/{session_id}/selections/{selection_id}/skip --
                                                decline to pick a
                                                candidate; never selects
                                                anything, never creates a
                                                proposal.
  POST   /api/cases                              -- (Phase 4D) create a
                                                     Case; creator becomes
                                                     owner.
  GET    /api/cases                              -- Cases the caller is a
                                                     member of, only.
  GET    /api/cases/{case_id}                    -- one Case, if a member.
  PATCH  /api/cases/{case_id}                    -- metadata/status update.
  POST   /api/cases/{case_id}/members            -- owner adds a member.
  POST   /api/cases/{case_id}/sessions/{session_id}   -- link a
                                                          caller-owned
                                                          session.
  DELETE /api/cases/{case_id}/sessions/{session_id}   -- explicit unlink.
  GET    /api/cases/{case_id}/context            -- the Case's context
                                                     ledger.
  POST   /api/cases/{case_id}/context            -- add a user-authored
                                                     context item.
  POST /api/sessions/{session_id}/attachments    -- (POST-5.1 B2)
                                                     multipart image
                                                     upload; validates,
                                                     stores in private
                                                     GCS, records a
                                                     READY Cloud SQL
                                                     metadata row.
                                                     Not yet linked to
                                                     any message or sent
                                                     to Gemini (that's
                                                     B5).
  GET  /api/attachments/{attachment_id}          -- (POST-5.1 B2)
                                                     frontend-safe
                                                     metadata only --
                                                     never a `gs://` URI
                                                     or storage object
                                                     name.
  GET  /api/attachments/{attachment_id}/content  -- (POST-5.1 B2) the
                                                     actual image bytes,
                                                     streamed from
                                                     private GCS through
                                                     this backend --
                                                     never a public or
                                                     signed URL.

No arbitrary tool-execution or state-mutation endpoint exists -- the only
way to reach Teams-domain conversational behavior through this API is via
Team Manager's own turn, the only way to change a proposal's approval
status is the trusted `approve`/`reject` endpoints, and the only way to
write Case context is either an authorized member (via the endpoints
above) or the one restricted agent capability
(`backend/agents/team_manager/case_tools.py`'s `record_case_analysis`,
never reachable through this HTTP API at all).

IDENTITY (Phase 4C): every session-/Case-bound route below resolves a
`UserContext` via `Depends(resolve_user_context)` -- a single, centralized
dependency (see identity.py; development-only, no production
authentication yet) -- and passes its `user_id` through. No request body
schema in this API has a `user_id`/`created_by_user_id`/`session_user_id`
field for the REQUESTING user -- identity always comes from the resolved
`UserContext` (instruction section 8). `AddCaseMemberRequest.user_id` is
the one apparent exception: it names the identity being ADDED as a Case
member, a deliberately distinct concept from the requester -- see
schemas.py's docstring on that field.
"""
from __future__ import annotations

from contextlib import asynccontextmanager
from typing import AsyncIterator

from fastapi import Depends, FastAPI, File, UploadFile
from fastapi.exceptions import RequestValidationError
from fastapi.responses import Response, StreamingResponse
from google.adk.utils.context_utils import Aclosing

from backend.api import approval_service
from backend.api import attachment_service as attachment_orchestration
from backend.api import case_service as case_orchestration
from backend.api import execution_service
from backend.api import selection_service
from backend.api.chat_service import ChatService, get_chat_service
from backend.api.errors import handle_request_validation_error, handle_safe_error, handle_unexpected_error
from backend.api.identity import UserContext, resolve_user_context
from backend.api import session_history_service
from backend.api.streaming_events import format_sse
from backend.api.schemas import (
    AddCaseContextItemRequest,
    AddCaseMemberRequest,
    ApprovalRequest,
    ApprovalResponse,
    AttachmentResponse,
    CancelRunResponse,
    CaseContextItemResponse,
    CaseContextResponse,
    CaseListItemResponse,
    CaseListResponse,
    CaseMembershipResponse,
    CaseResponse,
    ChooseSelectionRequest,
    ChooseSelectionResponse,
    SkipSelectionResponse,
    CaseSessionLinkResponse,
    ChatResponse,
    CreateCaseRequest,
    CreateSessionResponse,
    ExecuteActionResponse,
    HealthResponse,
    RenameSessionRequest,
    RewindSessionRequest,
    RewindSessionResponse,
    SendMessageRequest,
    SessionHistoryResponse,
    SessionListResponse,
    SessionSummaryDTO,
    UpdateCaseRequest,
)
from backend.api.session_service import ApiSessionService, get_session_service
from backend.attachments.models import ChatAttachmentRecord
from backend.attachments.service import AttachmentService, get_attachment_service
from backend.attachments.storage import ChatAttachmentStorage, get_attachment_storage
from backend.cases.service import CaseService, get_case_service
from backend.config.model_warmup import warmup_shared_model
from backend.config.settings import Settings, get_settings
from backend.gateway.safe_error import SafeErrorException


def _case_response(case) -> CaseResponse:
    return CaseResponse(
        case_id=case.case_id,
        title=case.title,
        problem_statement=case.problem_statement,
        external_reference=case.external_reference,
        status=case.status,
        created_by_user_id=case.created_by_user_id,
        created_at=case.created_at.isoformat(),
        updated_at=case.updated_at.isoformat(),
    )


def _attachment_response(record: ChatAttachmentRecord) -> AttachmentResponse:
    """Maps the trusted persistence model to the frontend-safe DTO --
    never a direct serialization (`storage_object_name`/`owner_user_id`/
    `sha256`/any `gs://` reference never leave this function).
    """
    return AttachmentResponse(
        attachment_id=record.attachment_id,
        filename=record.original_filename,
        mime_type=record.mime_type,
        size_bytes=record.size_bytes,
        status=record.status,
    )


def _content_disposition_filename(original_filename: str) -> str:
    """`original_filename` is already normalized (control characters/
    directory-traversal significance stripped) by `backend.attachments
    .validation.normalize_filename` at upload time -- this only guards
    against the one remaining header-breakout vector a stored filename
    could still contain: a literal `"` closing the quoted-string value
    early. CR/LF are already impossible here, so this alone is
    sufficient to make embedding this value in a `Content-Disposition`
    header safe.
    """
    return original_filename.replace('"', "")


def _context_item_response(item) -> CaseContextItemResponse:
    return CaseContextItemResponse(
        item_id=item.item_id,
        case_id=item.case_id,
        kind=item.kind,
        content=item.content,
        source_type=item.source_type.value,
        source_author=item.source_author,
        created_at=item.created_at.isoformat(),
        supporting_item_ids=item.supporting_item_ids,
    )


@asynccontextmanager
async def _lifespan(app: FastAPI) -> AsyncIterator[None]:
    """P4COLD: this application's one, existing startup/shutdown
    mechanism -- FastAPI's own documented `lifespan` protocol (the ASGI
    server enters it exactly once per app instance, before any request is
    routed) -- no separate/competing startup hook exists elsewhere in this
    module, so this is the only integration point, per instruction.

    `warmup_shared_model()` is BEST EFFORT by its own design (see model_
    warmup.py's own docstring): it never raises, so a warm-up failure or
    timeout can never prevent `yield` from being reached and the
    application from becoming ready for normal traffic. Existing shutdown
    behavior (after `yield`) is unmodified -- there is nothing for this
    pass to clean up (the shared model client is never closed; see model_
    warmup.py's own docstring on why).
    """
    await warmup_shared_model()
    yield


def create_app() -> FastAPI:
    app = FastAPI(title="SLOPANOC API", version="0.4.0-phase4g", lifespan=_lifespan)

    app.add_exception_handler(SafeErrorException, handle_safe_error)
    app.add_exception_handler(RequestValidationError, handle_request_validation_error)
    app.add_exception_handler(Exception, handle_unexpected_error)

    @app.get("/health", response_model=HealthResponse)
    async def health() -> HealthResponse:
        return HealthResponse()

    @app.post("/api/sessions", response_model=CreateSessionResponse, status_code=201)
    async def create_session(
        user: UserContext = Depends(resolve_user_context),
        session_service: ApiSessionService = Depends(get_session_service),
    ) -> CreateSessionResponse:
        session_id = await session_service.create_session(user.user_id)
        return CreateSessionResponse(session_id=session_id)

    # --- Saved conversation / history rehydration (POST-5.1 B4B) ----------

    @app.get("/api/sessions", response_model=SessionListResponse)
    async def list_sessions_endpoint(
        user: UserContext = Depends(resolve_user_context),
        session_service: ApiSessionService = Depends(get_session_service),
        settings: Settings = Depends(get_settings),
    ) -> SessionListResponse:
        """Owner-scoped saved-chat list (`session_history_service
        .list_saved_sessions`'s own docstring for the tri-state marker /
        bounded-backfill semantics). Never a raw `state`/`events` dump --
        `SessionSummaryDTO` is the only shape this ever returns.
        """
        sessions = await session_history_service.list_saved_sessions(
            session_service, user.user_id, settings.saved_chat_list_limit
        )
        return SessionListResponse(sessions=sessions)

    @app.get("/api/sessions/{session_id}/history", response_model=SessionHistoryResponse)
    async def get_session_history_endpoint(
        session_id: str,
        user: UserContext = Depends(resolve_user_context),
        session_service: ApiSessionService = Depends(get_session_service),
        attachment_service: AttachmentService = Depends(get_attachment_service),
    ) -> SessionHistoryResponse:
        """Safe transcript projection (`session_history_service
        .get_session_history`) -- active branch only, final assistant
        text only, LINKED attachment references only. Ownership enforced
        by `session_service.get_session` before anything else, same
        anti-enumeration behavior as every other session-scoped route.
        """
        return await session_history_service.get_session_history(
            session_service, attachment_service, session_id, user.user_id
        )

    @app.patch("/api/sessions/{session_id}", response_model=SessionSummaryDTO)
    async def rename_session_endpoint(
        session_id: str,
        body: RenameSessionRequest,
        user: UserContext = Depends(resolve_user_context),
        session_service: ApiSessionService = Depends(get_session_service),
    ) -> SessionSummaryDTO:
        """Durable manual rename (B4A correction pass) -- the existing
        frontend `RENAME_CHAT` reducer is local-only today and would be
        silently overwritten by the derived title on the next refresh
        without this. Same `CHAT_TITLE_STATE_KEY` the automatic first-turn
        title uses -- last write (auto or manual) wins.
        """
        return await session_history_service.rename_session(
            session_service, session_id, user.user_id, body.title
        )

    @app.post("/api/sessions/{session_id}/messages", response_model=ChatResponse)
    async def send_message(
        session_id: str,
        body: SendMessageRequest,
        user: UserContext = Depends(resolve_user_context),
        chat_service: ChatService = Depends(get_chat_service),
    ) -> ChatResponse:
        return await chat_service.run_turn(session_id, body.message, user.user_id, body.attachment_ids)

    @app.post("/api/sessions/{session_id}/messages/stream")
    async def stream_message(
        session_id: str,
        body: SendMessageRequest,
        user: UserContext = Depends(resolve_user_context),
        session_service: ApiSessionService = Depends(get_session_service),
        chat_service: ChatService = Depends(get_chat_service),
    ) -> StreamingResponse:
        """SSE variant of `send_message` (instruction section 34) -- same
        underlying canonical pipeline (`chat_service.execute_turn_events`),
        same ownership enforcement. Session ownership is verified HERE,
        BEFORE the streaming response is even opened, so an unknown/
        foreign session id gets a normal HTTP 404 (via the existing
        `SafeErrorException` handler) rather than an SSE stream containing
        an error event -- exactly the same ownership check
        `chat_service.run_turn` performs, applied identically here
        (instruction section 36).
        """
        await session_service.get_session(session_id, user.user_id)

        async def event_source():
            async with Aclosing(
                chat_service.execute_turn_events(session_id, body.message, user.user_id, body.attachment_ids)
            ) as events:
                async for event in events:
                    yield format_sse(event)

        return StreamingResponse(
            event_source(),
            media_type="text/event-stream",
            # Latency-diagnosis pass: without these, an intermediary
            # (a reverse proxy/load balancer/CDN in front of this API --
            # common in a real deployment) may buffer the ENTIRE response
            # before forwarding anything to the client, silently
            # defeating streaming regardless of how promptly this backend
            # itself yields `message.delta` events -- the client would
            # then perceive "first token" and "final response" arriving
            # at the same moment, at the full turn's own duration.
            # `Cache-Control: no-cache` stops any HTTP cache from trying
            # to buffer/replay this as a cacheable response; `X-Accel-
            # Buffering: no` is nginx's own documented opt-out of its
            # default response buffering for proxied responses (a no-op,
            # harmless header for any other proxy that doesn't recognize
            # it). Deliberately NOT setting `Connection: keep-alive` here
            # -- that is a hop-by-hop header ASGI servers already manage
            # themselves, and is invalid to set explicitly on an HTTP/2
            # connection.
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    @app.post("/api/sessions/{session_id}/runs/{run_id}/cancel", response_model=CancelRunResponse)
    async def cancel_run_endpoint(
        session_id: str,
        run_id: str,
        user: UserContext = Depends(resolve_user_context),
        chat_service: ChatService = Depends(get_chat_service),
    ) -> CancelRunResponse:
        """Pre-4H refinement -- the Stop control's real, server-side
        cancellation (previously the backend's own ADK run kept executing
        after a client-side abort; see `ChatService.cancel_run`'s own
        docstring for exactly what this can and cannot guarantee).
        `session_id`/`run_id` are the same values the frontend already
        has from `run.started`'s own event envelope -- no new identifier
        concept. Ownership is enforced identically to every other
        session-scoped route (`SafeErrorException` `not_found` for an
        unknown/foreign session, via `cancel_run`'s own `get_session`
        call, before anything else happens).
        """
        cancelled = await chat_service.cancel_run(session_id, run_id, user.user_id)
        return CancelRunResponse(session_id=session_id, run_id=run_id, cancelled=cancelled)

    @app.post("/api/sessions/{session_id}/rewind", response_model=RewindSessionResponse)
    async def rewind_session(
        session_id: str,
        body: RewindSessionRequest,
        user: UserContext = Depends(resolve_user_context),
        chat_service: ChatService = Depends(get_chat_service),
    ) -> RewindSessionResponse:
        """Phase 4G hardening pass -- editing a historical user message.
        See `chat_service.ChatService.rewind_before_user_turn`'s own
        docstring for the full branching semantics. The frontend calls
        this BEFORE sending the edited text as a normal new message on
        the SAME session id -- a raised `SafeErrorException` here means
        nothing was mutated, so the frontend can safely leave its own
        conversation view untouched on failure (fail before commit).
        """
        await chat_service.rewind_before_user_turn(session_id, body.before_user_turn_index, user.user_id)
        return RewindSessionResponse(session_id=session_id)

    @app.post("/api/sessions/{session_id}/approve", response_model=ApprovalResponse)
    async def approve_proposal_endpoint(
        session_id: str,
        body: ApprovalRequest,
        user: UserContext = Depends(resolve_user_context),
        session_service: ApiSessionService = Depends(get_session_service),
    ) -> ApprovalResponse:
        return await approval_service.approve(session_service, session_id, body.proposal_id, user.user_id)

    @app.post("/api/sessions/{session_id}/reject", response_model=ApprovalResponse)
    async def reject_proposal_endpoint(
        session_id: str,
        body: ApprovalRequest,
        user: UserContext = Depends(resolve_user_context),
        session_service: ApiSessionService = Depends(get_session_service),
    ) -> ApprovalResponse:
        return await approval_service.reject(session_service, session_id, body.proposal_id, user.user_id)

    @app.post("/api/sessions/{session_id}/execute", response_model=ExecuteActionResponse)
    async def execute_approved_action_endpoint(
        session_id: str,
        body: ApprovalRequest,
        user: UserContext = Depends(resolve_user_context),
        session_service: ApiSessionService = Depends(get_session_service),
    ) -> ExecuteActionResponse:
        """The deterministic execution continuation for an APPROVED
        proposal (Phase 4G) -- see execution_service.py's module docstring
        for why this exists and exactly what security guarantees it
        preserves unmodified. Approving a proposal (above) never executes
        it; this is the only route that can.
        """
        return await execution_service.execute(session_service, session_id, body.proposal_id, user.user_id)

    # --- Interactive selection (interaction-capability extension) ---------

    @app.post(
        "/api/sessions/{session_id}/selections/{selection_id}/choose",
        response_model=ChooseSelectionResponse,
    )
    async def choose_selection_endpoint(
        session_id: str,
        selection_id: str,
        body: ChooseSelectionRequest,
        user: UserContext = Depends(resolve_user_context),
        session_service: ApiSessionService = Depends(get_session_service),
    ) -> ChooseSelectionResponse:
        """See selection_service.choose's own docstring for the full
        resume semantics -- deterministic destination disambiguation,
        NEVER write approval (instruction section 14)."""
        return await selection_service.choose(session_service, session_id, selection_id, body.option_id, user.user_id)

    @app.post(
        "/api/sessions/{session_id}/selections/{selection_id}/skip",
        response_model=SkipSelectionResponse,
    )
    async def skip_selection_endpoint(
        session_id: str,
        selection_id: str,
        user: UserContext = Depends(resolve_user_context),
        session_service: ApiSessionService = Depends(get_session_service),
    ) -> SkipSelectionResponse:
        return await selection_service.skip(session_service, session_id, selection_id, user.user_id)

    # --- Cases (Phase 4D) -------------------------------------------------

    @app.post("/api/cases", response_model=CaseResponse, status_code=201)
    async def create_case(
        body: CreateCaseRequest,
        user: UserContext = Depends(resolve_user_context),
        case_service: CaseService = Depends(get_case_service),
    ) -> CaseResponse:
        case = await case_service.create_case(
            user.user_id, body.title, body.problem_statement, body.external_reference
        )
        return _case_response(case)

    @app.get("/api/cases", response_model=CaseListResponse)
    async def list_cases(
        user: UserContext = Depends(resolve_user_context),
        case_service: CaseService = Depends(get_case_service),
    ) -> CaseListResponse:
        cases = await case_service.list_cases(user.user_id)
        return CaseListResponse(
            cases=[
                CaseListItemResponse(case_id=c.case_id, title=c.title, status=c.status, updated_at=c.updated_at.isoformat())
                for c in cases
            ]
        )

    @app.get("/api/cases/{case_id}", response_model=CaseResponse)
    async def get_case(
        case_id: str,
        user: UserContext = Depends(resolve_user_context),
        case_service: CaseService = Depends(get_case_service),
    ) -> CaseResponse:
        case = await case_service.get_case(user.user_id, case_id)
        return _case_response(case)

    @app.patch("/api/cases/{case_id}", response_model=CaseResponse)
    async def update_case(
        case_id: str,
        body: UpdateCaseRequest,
        user: UserContext = Depends(resolve_user_context),
        case_service: CaseService = Depends(get_case_service),
    ) -> CaseResponse:
        case = await case_service.update_case(
            user.user_id,
            case_id,
            title=body.title,
            problem_statement=body.problem_statement,
            status=body.status.value if body.status is not None else None,
            external_reference=body.external_reference,
        )
        return _case_response(case)

    @app.post("/api/cases/{case_id}/members", response_model=CaseMembershipResponse, status_code=201)
    async def add_case_member(
        case_id: str,
        body: AddCaseMemberRequest,
        user: UserContext = Depends(resolve_user_context),
        case_service: CaseService = Depends(get_case_service),
    ) -> CaseMembershipResponse:
        membership = await case_service.add_member(user.user_id, case_id, body.user_id, body.role.value)
        return CaseMembershipResponse(case_id=membership.case_id, user_id=membership.user_id, role=membership.role)

    @app.post("/api/cases/{case_id}/sessions/{session_id}", response_model=CaseSessionLinkResponse, status_code=201)
    async def link_case_session(
        case_id: str,
        session_id: str,
        user: UserContext = Depends(resolve_user_context),
        session_service: ApiSessionService = Depends(get_session_service),
        case_service: CaseService = Depends(get_case_service),
    ) -> CaseSessionLinkResponse:
        link = await case_orchestration.link_session(session_service, case_service, user.user_id, case_id, session_id)
        return CaseSessionLinkResponse(case_id=link.case_id, session_id=link.session_id, linked_at=link.linked_at.isoformat())

    @app.delete("/api/cases/{case_id}/sessions/{session_id}", status_code=204)
    async def unlink_case_session(
        case_id: str,
        session_id: str,
        user: UserContext = Depends(resolve_user_context),
        session_service: ApiSessionService = Depends(get_session_service),
        case_service: CaseService = Depends(get_case_service),
    ) -> None:
        await case_orchestration.unlink_session(session_service, case_service, user.user_id, case_id, session_id)

    @app.get("/api/cases/{case_id}/context", response_model=CaseContextResponse)
    async def get_case_context(
        case_id: str,
        user: UserContext = Depends(resolve_user_context),
        case_service: CaseService = Depends(get_case_service),
    ) -> CaseContextResponse:
        items = await case_service.get_context_items(user.user_id, case_id)
        return CaseContextResponse(items=[_context_item_response(i) for i in items])

    @app.post("/api/cases/{case_id}/context", response_model=CaseContextItemResponse, status_code=201)
    async def add_case_context(
        case_id: str,
        body: AddCaseContextItemRequest,
        user: UserContext = Depends(resolve_user_context),
        case_service: CaseService = Depends(get_case_service),
    ) -> CaseContextItemResponse:
        item = await case_service.add_user_context_item(user.user_id, case_id, body.kind.value, body.content)
        return _context_item_response(item)

    # --- Chat Attachments (POST-5.1 B2) ------------------------------------

    @app.post("/api/sessions/{session_id}/attachments", response_model=AttachmentResponse, status_code=201)
    async def upload_session_attachment(
        session_id: str,
        file: UploadFile = File(...),
        user: UserContext = Depends(resolve_user_context),
        session_service: ApiSessionService = Depends(get_session_service),
        attachment_service: AttachmentService = Depends(get_attachment_service),
        storage: ChatAttachmentStorage = Depends(get_attachment_storage),
        settings: Settings = Depends(get_settings),
    ) -> AttachmentResponse:
        record = await attachment_orchestration.upload_attachment(
            session_service=session_service,
            attachment_service=attachment_service,
            storage=storage,
            settings=settings,
            user_id=user.user_id,
            session_id=session_id,
            upload_file=file,
        )
        return _attachment_response(record)

    @app.get("/api/attachments/{attachment_id}", response_model=AttachmentResponse)
    async def get_attachment_metadata(
        attachment_id: str,
        user: UserContext = Depends(resolve_user_context),
        attachment_service: AttachmentService = Depends(get_attachment_service),
    ) -> AttachmentResponse:
        record = await attachment_orchestration.get_attachment_metadata(attachment_service, user.user_id, attachment_id)
        return _attachment_response(record)

    @app.get("/api/attachments/{attachment_id}/content")
    async def get_attachment_content(
        attachment_id: str,
        user: UserContext = Depends(resolve_user_context),
        attachment_service: AttachmentService = Depends(get_attachment_service),
        storage: ChatAttachmentStorage = Depends(get_attachment_storage),
    ) -> Response:
        data, record = await attachment_orchestration.get_attachment_content(
            attachment_service, storage, user.user_id, attachment_id
        )
        safe_filename = _content_disposition_filename(record.original_filename)
        return Response(
            content=data,
            media_type=record.mime_type,
            headers={
                "Content-Disposition": f'inline; filename="{safe_filename}"',
                "Cache-Control": "private",
            },
        )

    return app


app = create_app()
