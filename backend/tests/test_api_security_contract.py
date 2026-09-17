"""Security-contract tests for the Phase 4A + 4B + 4G backend API.

Confirms: the trusted approve/reject endpoints (Phase 4B) exist and are
the ONLY place besides the manual dev CLI that ever calls
`approve_proposal`/`reject_proposal` -- team_manager, incident_manager,
chat_service, and pending_action must never call either. The trusted
execute endpoint (Phase 4G) is the ONLY API-layer place that ever calls
the Teams write tools directly, and it does so deterministically, never
through Gemini/the Runner. No arbitrary tool-execution or state-mutation
endpoint exists, the API never returns the Power Automate gateway URL or
any credential, and the frozen Teams READ v1 / Teams WRITE v1 /
approval-framework layers remain completely untouched by this milestone.
"""
from __future__ import annotations

import inspect

from fastapi.testclient import TestClient

from backend.api import app as app_module
from backend.api import approval_service as approval_service_module
from backend.api import chat_service as chat_service_module
from backend.api import execution_service as execution_service_module
from backend.api import pending_action as pending_action_module
from backend.api import session_service as session_service_module
from backend.api.app import app


def _all_paths() -> set[str]:
    return {getattr(route, "path", "") for route in app.routes}


def _all_methods_by_path() -> dict[str, set[str]]:
    result: dict[str, set[str]] = {}
    for route in app.routes:
        path = getattr(route, "path", None)
        methods = getattr(route, "methods", None)
        if path is not None and methods is not None:
            result.setdefault(path, set()).update(methods)
    return result


# --- No approval-mutation endpoint exists (Phase 4B) ------------------------


def _mentions_as_code(source: str, name: str) -> bool:
    """True only for an actual import/call site -- not a docstring/prose
    mention explaining what's deliberately absent (several modules here
    document, in prose, that approve/reject are intentionally not used;
    that mention is not itself a violation). Mirrors the same helper used
    in test_teams_write_security_contract.py.
    """
    return f"import {name}" in source or f"{name}(" in source


def test_approve_and_reject_routes_exist() -> None:
    paths = _all_paths()
    assert "/api/sessions/{session_id}/approve" in paths
    assert "/api/sessions/{session_id}/reject" in paths


def test_app_module_never_calls_approve_or_reject_directly() -> None:
    """`app.py` delegates to `approval_service.approve`/`.reject` -- it
    never calls the raw `approve_proposal`/`reject_proposal` functions
    itself.
    """
    source = inspect.getsource(app_module)
    assert not _mentions_as_code(source, "approve_proposal")
    assert not _mentions_as_code(source, "reject_proposal")


def test_chat_service_never_imports_approve_or_reject() -> None:
    """The chat/agent-turn path must never be able to change approval
    status -- only the dedicated approval endpoints can.
    """
    source = inspect.getsource(chat_service_module)
    assert not _mentions_as_code(source, "approve_proposal")
    assert not _mentions_as_code(source, "reject_proposal")


def test_pending_action_module_never_imports_approve_or_reject() -> None:
    """Reading the pending proposal must never be able to mutate it."""
    source = inspect.getsource(pending_action_module)
    assert not _mentions_as_code(source, "approve_proposal")
    assert not _mentions_as_code(source, "reject_proposal")
    assert not _mentions_as_code(source, "consume_proposal")


def test_session_service_never_imports_approve_or_reject() -> None:
    """Session lifecycle/locking/persistence stays generic -- it knows
    nothing about approval semantics specifically.
    """
    source = inspect.getsource(session_service_module)
    assert not _mentions_as_code(source, "approve_proposal")
    assert not _mentions_as_code(source, "reject_proposal")


def test_approval_service_is_the_sanctioned_caller_of_approve_and_reject() -> None:
    source = inspect.getsource(approval_service_module)
    assert _mentions_as_code(source, "approve_proposal")
    assert _mentions_as_code(source, "reject_proposal")


def test_approval_service_never_calls_consume_proposal() -> None:
    """Approving a proposal must never itself consume it -- consumption
    is reserved for a successful write execution, which this endpoint
    never performs (instruction section 10).
    """
    source = inspect.getsource(approval_service_module)
    assert not _mentions_as_code(source, "consume_proposal")


def test_approval_service_never_calls_the_runner_gemini_or_power_automate() -> None:
    source = inspect.getsource(approval_service_module)
    for forbidden in ("Runner(", "run_async(", "PowerAutomateClient(", "teams_create_chat(", "teams_send_message("):
        assert forbidden not in source


# --- Execution continuation (Phase 4G) ---------------------------------------


def test_execute_route_exists() -> None:
    assert "/api/sessions/{session_id}/execute" in _all_paths()


def test_execution_service_is_the_sanctioned_caller_of_the_teams_write_tools() -> None:
    """`execution_service.py` is the only API-layer module that ever
    imports/calls `teams_create_chat`/`teams_send_message` directly --
    everywhere else, those functions are only reachable as ADK tools on
    `incident_manager`'s own agent-driven turn. Checked directly (not via
    `_mentions_as_code`, which looks for an `X(` call form or an exact
    `import X` substring -- too narrow here, since both are passed as
    bare function references into `run_in_threadpool(...)`, not called
    with `(`).
    """
    source = inspect.getsource(execution_service_module)
    assert "teams_create_chat" in source
    assert "teams_send_message" in source


def test_no_other_api_module_calls_the_teams_write_tools_directly() -> None:
    for module in (app_module, approval_service_module, chat_service_module, pending_action_module, session_service_module):
        source = inspect.getsource(module)
        assert not _mentions_as_code(source, "teams_create_chat")
        assert not _mentions_as_code(source, "teams_send_message")


def test_execution_service_never_calls_the_runner_or_gemini() -> None:
    """The execution continuation is deterministic Python, never an agent
    turn -- confirms no Runner/Gemini involvement, mirroring the identical
    guarantee approval_service.py already makes for approve/reject.
    """
    source = inspect.getsource(execution_service_module)
    for forbidden in ("Runner(", "run_async(", "AgentTool("):
        assert forbidden not in source


def test_execution_service_never_approves_or_rejects_a_proposal() -> None:
    """Execution must never itself grant approval -- it can only act on a
    proposal that is ALREADY approved via the separate, trusted /approve
    endpoint.
    """
    source = inspect.getsource(execution_service_module)
    assert not _mentions_as_code(source, "approve_proposal")
    assert not _mentions_as_code(source, "reject_proposal")


# --- No arbitrary tool-execution / state-mutation endpoint ------------------


def test_only_the_expected_routes_exist() -> None:
    """A closed allow-list of paths -- anything else (a tool-execution
    endpoint, a raw state-mutation endpoint) would fail this test.
    `/docs`/`/redoc`/`/openapi.json` are FastAPI's own auto-generated
    documentation routes, not application endpoints.
    """
    expected = {
        "/health",
        "/api/sessions",
        "/api/sessions/{session_id}/messages",
        # Phase 4E -- SSE variant of the chat endpoint (instruction section 34).
        "/api/sessions/{session_id}/messages/stream",
        # Pre-4H refinement -- real server-side Stop: cancels the exact
        # tracked background task driving an in-flight run, if any
        # (chat_service.ChatService.cancel_run). Never a generic
        # tool-execution/state-mutation channel -- it can only cancel,
        # never start or redirect a run.
        "/api/sessions/{session_id}/runs/{run_id}/cancel",
        # Phase 4G hardening pass -- conversational branching for editing a
        # historical user message (chat_service.py's
        # rewind_before_user_turn); never a new session, never a
        # tool-execution channel.
        "/api/sessions/{session_id}/rewind",
        # POST-6A -- the governed-operation authoring/review surface.
        # Listing and drafting are read/CANDIDATE-only and grant nothing;
        # approve/revoke are the two authority-bearing routes and are
        # gated server-side on `SLOPANOC_KNOWLEDGE_GOVERNORS` inside
        # `OperationApprovalStore.require_governance_permission`. None of
        # these is a tool-execution or raw state-mutation channel: they
        # change only descriptor authority, never a session, a proposal,
        # or anything executable.
        "/api/knowledge/{knowledge_id}/versions/{version_label}/operations",
        "/api/knowledge/{knowledge_id}/versions/{version_label}/operations/{section_id}/draft",
        "/api/knowledge/{knowledge_id}/versions/{version_label}/operations/{section_id}/approve",
        "/api/knowledge/{knowledge_id}/versions/{version_label}/operations/{section_id}/revoke",
        # POST-6A -- read-only recovery surface: which of THIS session's
        # turns never reached a terminal state, and whether an execution
        # was left unconfirmed. Ownership-scoped like every other session
        # route; never a mutation channel.
        "/api/sessions/{session_id}/operational-status",
        "/api/sessions/{session_id}/approve",
        "/api/sessions/{session_id}/reject",
        # Phase 4G -- the only route that can actually execute an approved
        # proposal (execution_service.py).
        "/api/sessions/{session_id}/execute",
        # Interaction-capability extension -- deterministic Teams chat-name
        # disambiguation (selection_service.py); destination resolution
        # only, never write approval/execution.
        "/api/sessions/{session_id}/selections/{selection_id}/choose",
        "/api/sessions/{session_id}/selections/{selection_id}/skip",
        # Phase 4D -- Case/Fault context (instruction section 28).
        "/api/cases",
        "/api/cases/{case_id}",
        "/api/cases/{case_id}/members",
        "/api/cases/{case_id}/sessions/{session_id}",
        "/api/cases/{case_id}/context",
        # POST-5.1 B2 -- durable chat attachments (backend/api/attachment_
        # service.py). Upload is metadata/GCS only -- never a tool-
        # execution/state-mutation channel, never linked to a message or
        # sent to Gemini here (that's B5). The two GET routes return
        # frontend-safe metadata/binary content only, authorized by
        # attachment ownership -- never a raw storage path.
        "/api/sessions/{session_id}/attachments",
        "/api/attachments/{attachment_id}",
        "/api/attachments/{attachment_id}/content",
        # POST-5.1 B7 -- closes the B3-documented orphan gap: deletes a
        # still-READY (never sent) attachment removed from the draft
        # before send. Session-ownership-scoped like upload, above;
        # structurally rejects deleting a LINKED (sent) attachment --
        # see backend/api/attachment_service.py's `delete_ready_
        # attachment`. Never a generic state-mutation/tool-execution
        # channel.
        "/api/sessions/{session_id}/attachments/{attachment_id}",
        # POST-5.1 B4B -- safe saved-conversation list/history rehydration
        # (session_history_service.py) plus durable manual rename. Never
        # raw ADK state/events; ownership-scoped identically to every
        # other session route. GET /api/sessions itself needs no new
        # entry -- that path already exists (POST, above).
        "/api/sessions/{session_id}",
        "/api/sessions/{session_id}/history",
        # Teams Visual Evidence milestone -- lazy, authenticated retrieval
        # of ONE Teams-hosted image actually delivered to Gemini for a past
        # turn (backend/api/source_images.py). session_id/source_id/
        # image_id are ALL opaque/session-ownership-scoped -- never a raw
        # Teams chat_id/message_id/hosted_content_id. Read-only; never a
        # tool-execution/state-mutation channel.
        "/api/sessions/{session_id}/sources/{source_id}/images/{image_id}",
        "/openapi.json",
        "/docs",
        "/docs/oauth2-redirect",
        "/redoc",
    }
    assert _all_paths() == expected


def test_chat_endpoint_only_accepts_message_and_attachment_ids_fields() -> None:
    """POST-5.1 B5 -- the chat endpoint's request schema has exactly two
    fields: `message` (free text) and `attachment_ids` (a plain list of
    opaque, server-generated ids the client already received from its own
    prior `POST /api/sessions/{id}/attachments` uploads). Still no channel
    through which a client could specify a tool name, a raw ADK action, a
    state key to write, a storage path/URI, or anything else -- every
    `attachment_id` is independently re-validated server-side
    (`prepare_attachments_for_turn`: existence, ownership, session, READY
    status, MIME, limits) before it can influence model input at all; the
    client never supplies a gs:// URI, bucket, or session id for it.
    """
    from backend.api.schemas import SendMessageRequest

    assert set(SendMessageRequest.model_fields) == {"message", "attachment_ids"}


def test_approval_endpoints_only_accept_a_proposal_id_field() -> None:
    """No field for `status`, `operation`, or `payload` -- the client can
    request WHICH proposal to act on, never WHAT happens to it or with
    what data (instruction section 11).
    """
    from backend.api.schemas import ApprovalRequest

    assert set(ApprovalRequest.model_fields) == {"proposal_id"}


def test_rewind_endpoint_only_accepts_a_turn_index_field() -> None:
    """No field for raw ADK event indices, invocation ids, or a target
    session id -- the client can only say WHICH of its own already-active
    turns to rewind before, on the session already in the URL path.
    """
    from backend.api.schemas import RewindSessionRequest

    assert set(RewindSessionRequest.model_fields) == {"before_user_turn_index"}


def test_session_route_methods_are_exactly_as_expected() -> None:
    methods = _all_methods_by_path()
    # POST-5.1 B4B added GET (the saved-chat list) alongside the existing
    # POST (create) on the same path.
    assert methods["/api/sessions"] == {"POST", "GET"}
    # POST-5.1 B4B -- durable manual rename only; no GET-by-id/DELETE route.
    assert methods["/api/sessions/{session_id}"] == {"PATCH"}
    assert methods["/api/sessions/{session_id}/history"] == {"GET"}
    assert methods["/api/sessions/{session_id}/messages"] == {"POST"}
    assert methods["/api/sessions/{session_id}/messages/stream"] == {"POST"}
    assert methods["/api/sessions/{session_id}/runs/{run_id}/cancel"] == {"POST"}
    assert methods["/api/sessions/{session_id}/rewind"] == {"POST"}
    assert methods["/api/sessions/{session_id}/approve"] == {"POST"}
    assert methods["/api/sessions/{session_id}/reject"] == {"POST"}
    assert methods["/api/sessions/{session_id}/execute"] == {"POST"}
    assert methods["/api/sessions/{session_id}/selections/{selection_id}/choose"] == {"POST"}
    assert methods["/api/sessions/{session_id}/selections/{selection_id}/skip"] == {"POST"}
    assert methods["/health"] == {"GET"}


# --- Never returns the gateway URL / credentials ----------------------------


def test_api_modules_never_reference_the_gateway_url_env_vars() -> None:
    for module in (
        app_module,
        chat_service_module,
        pending_action_module,
        session_service_module,
        approval_service_module,
    ):
        source = inspect.getsource(module)
        assert "SLOPANOC_POWER_AUTOMATE_GATEWAY_URL" not in source
        assert "resolve_power_automate_gateway_url" not in source


def test_openapi_schema_never_mentions_power_automate_or_gateway_url() -> None:
    with TestClient(app) as client:
        schema = client.get("/openapi.json").json()
    schema_text = str(schema)
    assert "power_automate" not in schema_text.lower()
    assert "gateway_url" not in schema_text.lower()


# --- Frozen layers untouched -------------------------------------------------


def test_agent_topology_is_unaffected_by_the_api_layer() -> None:
    """The API layer itself (app.py/session_service.py/chat_service.py/
    approval_service.py/case_service.py) never adds, removes, or wraps an
    agent tool -- `record_case_analysis` (Phase 4D) is a legitimate,
    deliberately-added restricted Case-write capability (instruction:
    "Adding a deterministic Case-context capability is allowed."), added
    directly on `team_manager`'s own definition, not something the API
    layer injected. `knowledge_search`/`knowledge_select_evidence`
    (Phase 5.1J) are the same kind of legitimate, deliberate addition --
    added directly on `incident_manager`'s own definition
    (backend/agents/incident_manager/agent.py), not something the API
    layer injected -- and, critically, `team_manager.tools` below remains
    exactly the pre-5.1J three: Team Manager never receives either KM
    tool directly (docs/KNOWLEDGE_CONTRACT.md's Phase 5.1J section).

    `teams_get_hosted_content` (Teams Rich Content milestone, single-image
    scope) is the same kind of legitimate, deliberate addition -- added
    directly on `incident_manager`'s own definition, read-only, retrieval
    only (no multimodal injection yet -- see get_hosted_content.py's own
    module docstring), never something the API layer injected.

    `teams_get_all_hosted_content` (Deterministic All-Image Retrieval
    milestone) is the same kind of legitimate, deliberate addition --
    read-only, deterministic backend expansion over one message's
    already-discovered hosted_content_ids, never a model-driven loop.

    `troubleshooting_manager` (Phase 6A.10, Dual-Specialist Orchestration)
    is the same kind of legitimate, deliberate addition -- a plain
    FunctionTool wrapper (backend/agents/team_manager/troubleshooting_
    tool.py) calling the canonical 6A.9 `run_troubleshooting_assessment`,
    added directly on `team_manager`'s own definition, not something the
    API layer injected.
    """
    from backend.agents.incident_manager.agent import incident_manager
    from backend.agents.team_manager.agent import team_manager

    def tname(t):
        return getattr(t, "name", None) or getattr(t, "__name__", str(t))

    assert [tname(t) for t in team_manager.tools] == [
        "incident_manager",
        "record_case_analysis",
        "record_conversation_target",
        "record_source_requirements",
        "record_request_contract",
        "troubleshooting_manager",
    ]
    assert [tname(t) for t in incident_manager.tools] == [
        "teams_list_chats",
        "teams_get_messages",
        "teams_get_hosted_content",
        "teams_get_all_hosted_content",
        "get_current_time_context",
        "teams_propose_create_chat",
        "teams_propose_send_message",
        "teams_create_chat",
        "teams_send_message",
        "knowledge_search",
        "knowledge_select_evidence",
    ]


def test_chat_service_reuses_the_single_canonical_team_manager_agent() -> None:
    """No second agent runtime/definition was introduced, and the
    operational variant's capability surface is RESTRICTED, never widened.

    POST-6A -- CORRECTED, NOT RELAXED. This test previously asserted
    `runner.agent.instruction is canonical_team_manager.instruction` and
    `runner.agent.tools == canonical_team_manager.tools`. Both assertions
    had become assertions that a deliberate restriction had NOT happened:

      - `operational_team_manager` structurally REMOVES
        `record_request_contract` and `record_source_requirements`,
        because the deterministic preflight now owns both declarations.
      - It therefore also carries its OWN instruction provider
        (`operational_team_manager_instruction_provider`), because the
        full `TEAM_MANAGER_INSTRUCTION` demands tool calls this variant
        cannot make -- and a model given an unfulfillable tool
        instruction narrates the attempt to the user as plain text, the
        exact failure LIVE-CORR-13 already fixed for
        `presentation_team_manager`.

    Restoring either tool to satisfy the old assertion would undo a
    safety repair to keep a test green. So the assertions are corrected
    to check the invariant that actually matters here -- ONE agent
    identity, and a tool surface that only ever SHRINKS.
    """
    from backend.agents.team_manager.agent import team_manager as canonical_team_manager
    from backend.agents.team_manager.case_context import (
        operational_team_manager_instruction_provider,
    )
    from backend.api.chat_service import _build_runner
    from backend.api.session_service import ApiSessionService

    runner = _build_runner(ApiSessionService())

    # ONE agent identity and ONE model -- not a second agent runtime.
    assert runner.agent.name == canonical_team_manager.name
    assert runner.agent.model is canonical_team_manager.model
    assert runner.agent.after_tool_callback == canonical_team_manager.after_tool_callback
    assert runner.agent.after_model_callback is canonical_team_manager.after_model_callback

    # The instruction is the provider that MATCHES this variant's real
    # toolset -- deliberately not the canonical one.
    assert runner.agent.instruction is operational_team_manager_instruction_provider
    assert runner.agent.instruction is not canonical_team_manager.instruction

    # THE SECURITY INVARIANT: the operational runner's tools are a strict
    # SUBSET of the canonical agent's. A test that pinned equality could
    # not tell a removal (safe) from an addition (a new capability
    # reaching the model); a subset check rejects exactly the dangerous
    # direction and permits exactly the safe one.
    canonical_tool_names = {_tool_name(tool) for tool in canonical_team_manager.tools}
    runner_tool_names = {_tool_name(tool) for tool in runner.agent.tools}
    assert runner_tool_names <= canonical_tool_names, (
        "the operational runner must never gain a capability the canonical agent does not have"
    )

    # And the two declaration tools are REMOVED, on purpose: the
    # deterministic preflight owns both declarations for this turn.
    assert "record_request_contract" not in runner_tool_names
    assert "record_source_requirements" not in runner_tool_names
    assert {"record_request_contract", "record_source_requirements"} <= canonical_tool_names


def _tool_name(tool) -> str:
    """ADK tools are a mix of plain functions and `AgentTool`/`BaseTool`
    instances, so neither `__name__` nor `.name` alone covers them."""
    return getattr(tool, "name", None) or getattr(tool, "__name__", repr(tool))
