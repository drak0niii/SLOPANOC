"""Phase 5.1J: the CONCRETE ADK adapter for the Generic Knowledge
Management (KM) capability -- Incident Manager's first real consumer
integration.

This package intentionally lives OUTSIDE `backend/knowledge/**` (which
remains, by design, completely free of any `google.adk`/`ToolContext`/
agent dependency -- see backend/knowledge/tools/__init__.py's own "NO
ADK/GEMINI WIRING" section). Everything ADK-specific -- `ToolContext`,
model-visible tool schemas, run-scoped trusted evidence state keyed by
the application's own trusted run identity -- lives here instead.

  - runtime.py -- process-wide `KnowledgeToolService` composition (a
                  local `SQLiteKnowledgeRepository` + `KnowledgeRetrievalService`
                  + `KnowledgeProvenanceService`), the trusted, run-id-keyed
                  `KnowledgeRunEvidenceState` store (available vs. selected
                  evidence, one trusted `as_of` captured once per run), and
                  its lifecycle (init/merge/select/snapshot/discard).
  - tools.py   -- the two concrete, ADK-compatible tool functions:
                  `knowledge_search` (model-visible: `query_text`, `limit`
                  only) and `knowledge_select_evidence` (model-visible:
                  `selections: list[KnowledgeEvidenceSelectionKey]` only,
                  the frozen 5.1H identity-only contract, used directly --
                  verified to generate a correctly-shaped, closed nested
                  ADK schema with no wrapper DTO needed).

CENTRAL 5.1J TRUST PROBLEM THIS PACKAGE SOLVES: `KnowledgeToolService.search`
(5.1I) returns a `KnowledgeSearchExecutionResult` whose `agent_payload` may
reach the model and whose `evidence_set` must not. This package keeps
`evidence_set` (and everything derived from successive searches within one
run) as SERVER-OWNED, run-id-keyed Python state -- never serialized into
ordinary ADK/session state, never round-tripped through the model, and
never accepted back from a caller/model as authority. A model may later
supply only identity (`KnowledgeEvidenceSelectionKey`); Python alone
determines what that identity actually resolves to, validated against
`validate_evidence_selection` (5.1H, reused unchanged) scoped to exactly
this run's own accumulated available evidence -- never the wider
repository, never another run.

WHY A RUN-ID-KEYED DICT, NOT A `ContextVar`: verified directly against
this codebase's own prior investigation
(backend/agents/team_manager/direct_read_fast_path.py's "CORRECTION PASS"
section) -- ADK's `handle_function_call_list_async` runs every tool call
via `asyncio.create_task(...)`, which COPIES the current `contextvars.Context`
at task-creation time; a `ContextVar.set(...)` performed inside that child
task can never propagate back out to a sibling task or the parent. A plain,
lock-protected, module-level `dict[run_id, KnowledgeRunEvidenceState]` --
the same fix already proven correct for `_pending_trusted_result_by_run` in
that module -- sidesteps this entirely: ordinary shared mutable state is
visible from any task in the same process, unlike context-local storage.
`run_id` itself is obtained via `backend.api.turn_context.current_run_id()`,
a value that IS safely readable from any nested task (it is bound ONCE, at
chat_service.py's own turn start, before any task-splitting occurs -- only
WRITES performed inside a child task fail to propagate upward, not reads of
an already-bound value; `teams_get_messages` already relies on this exact
same asymmetry).

Cleanup is wired into chat_service.py's own existing, already-proven
turn-end `finally` block (see that module's own "BUGFIX (evidence-mailbox
lifecycle audit)" comment) -- guaranteeing no stale run-scoped evidence
survives past the one turn/run that produced it, on every exit path
(success, a caught exception, or cancellation), exactly like
`pop_message_texts`/`discard_model_call_tracking`/`discard_pending_trusted_result`
already do for their own per-run bookkeeping.
"""
