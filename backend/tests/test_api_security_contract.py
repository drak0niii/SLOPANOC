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
        "/openapi.json",
        "/docs",
        "/docs/oauth2-redirect",
        "/redoc",
    }
    assert _all_paths() == expected


def test_chat_endpoint_only_accepts_a_message_field() -> None:
    """The chat endpoint's request schema has exactly one field -- no
    channel through which a client could specify a tool name, a raw ADK
    action, or a state key to write.
    """
    from backend.api.schemas import SendMessageRequest

    assert set(SendMessageRequest.model_fields) == {"message"}


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
    assert methods["/api/sessions"] == {"POST"}
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
    ]
    assert [tname(t) for t in incident_manager.tools] == [
        "teams_list_chats",
        "teams_get_messages",
        "get_current_time_context",
        "teams_propose_create_chat",
        "teams_propose_send_message",
        "teams_create_chat",
        "teams_send_message",
        "knowledge_search",
        "knowledge_select_evidence",
    ]


def test_chat_service_reuses_the_single_canonical_team_manager_agent() -> None:
    """No second agent runtime/definition was introduced.

    P4B.3 COMPLETION PASS: `_build_runner`'s agent is now a `.model_copy`
    of the canonical `team_manager` (direct_read_fast_path.py's own
    `get_fast_path_team_manager`) -- the SAME established pattern already
    used for `presentation_team_manager` (R1): identical name, instruction,
    tools, model, and every OTHER callback; only one additional, narrowly-
    scoped `before_model_callback` is prepended (normally a no-op -- see
    that module's own docstring). This is not a second agent runtime/
    definition, so the assertion checks identity of everything that
    actually defines the agent's behavior/security surface, not raw
    object identity.
    """
    from backend.agents.team_manager.agent import team_manager as canonical_team_manager
    from backend.api.chat_service import _build_runner
    from backend.api.session_service import ApiSessionService

    runner = _build_runner(ApiSessionService())
    assert runner.agent.name == canonical_team_manager.name
    assert runner.agent.instruction is canonical_team_manager.instruction
    assert runner.agent.tools == canonical_team_manager.tools
    assert runner.agent.model is canonical_team_manager.model
    assert runner.agent.after_tool_callback == canonical_team_manager.after_tool_callback
    assert runner.agent.after_model_callback is canonical_team_manager.after_model_callback
