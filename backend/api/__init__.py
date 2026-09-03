"""The SLOPANOC backend HTTP API (Phase 4A).

Bridges a future frontend to the existing, unmodified ADK agent runtime
(`backend.agents.team_manager.agent.team_manager`) via a small session
model: one client-facing `session_id` maps to exactly one ADK session,
and Team Manager remains the only agent the API ever talks to directly.

  - schemas.py         -- request/response data contracts.
  - errors.py           -- the SafeError <-> HTTP-status bridge (reuses
                            backend.gateway.safe_error, never a second
                            error taxonomy).
  - pending_action.py   -- deterministic `pending_action_proposal` ->
                            safe frontend DTO mapper (plain Python, never
                            model-generated).
  - session_service.py  -- ADK session lifecycle + per-session
                            concurrency serialization.
  - chat_service.py     -- the one chat-execution path (used for both the
                            synchronous response this milestone returns
                            and, later, streaming -- see its docstring).
  - app.py              -- the FastAPI app and its routes.

No approval-mutation endpoint exists in this package (Phase 4B). No
frontend code lives here. No second agent runtime is created.
"""
