"""Canonical event stages; span operation names and public SSE codes are separate."""
from enum import Enum, unique
from types import MappingProxyType

@unique
class Stage(str, Enum):
    REQUEST_RECEIVED = "request.received"
    REQUEST_VALIDATED = "request.validated"
    SESSION_LOAD_STARTED = "session.load.started"
    SESSION_LOAD_COMPLETED = "session.load.completed"
    ATTACHMENTS_STARTED = "attachments.started"
    ATTACHMENTS_COMPLETED = "attachments.completed"
    PLANNING_STARTED = "planning.started"
    PLANNING_MODEL_STARTED = "planning.model.started"
    PLANNING_MODEL_COMPLETED = "planning.model.completed"
    PLANNING_COMPLETED = "planning.completed"
    PLANNING_FAILED = "planning.failed"
    THREAD_RESOLVE_STARTED = "thread.resolve.started"
    THREAD_RESOLVE_COMPLETED = "thread.resolve.completed"
    PENDING_INTERACTION_RESOLVE_STARTED = "pending_interaction.resolve.started"
    PENDING_INTERACTION_RESOLVE_COMPLETED = "pending_interaction.resolve.completed"
    SOURCE_REQUIREMENTS_COMPLETED = "source_requirements.completed"
    CONTEXT_SELECTION_STARTED = "context.selection.started"
    CONTEXT_SELECTION_COMPLETED = "context.selection.completed"
    CONTEXT_ITEM_SELECTED = "context.item.selected"
    CONTEXT_ITEM_EXCLUDED = "context.item.excluded"
    AGENT_STARTED = "agent.started"
    AGENT_COMPLETED = "agent.completed"
    AGENT_FAILED = "agent.failed"
    AGENT_TEAM_MANAGER = "agent.team_manager"
    AGENT_TECHNICAL_AUTHORITY = "agent.technical_authority"
    AGENT_INCIDENT_MANAGER = "agent.incident_manager"
    AGENT_PROBLEM_MANAGER = "agent.problem_manager"
    AGENT_AUTOMATED_OPERATIONS = "agent.automated_operations"
    MODEL_REQUEST_STARTED = "model.request.started"
    MODEL_FIRST_TOKEN = "model.first_token"
    MODEL_REQUEST_COMPLETED = "model.request.completed"
    MODEL_REQUEST_FAILED = "model.request.failed"
    MODEL_REQUEST_TIMEOUT = "model.request.timeout"
    TOOL_STARTED = "tool.started"
    TOOL_COMPLETED = "tool.completed"
    TOOL_FAILED = "tool.failed"
    TOOL_TIMEOUT = "tool.timeout"
    KNOWLEDGE_SEARCH_STARTED = "knowledge.search.started"
    KNOWLEDGE_SEARCH_COMPLETED = "knowledge.search.completed"
    TEAMS_LIST_CHATS_STARTED = "teams.list_chats.started"
    TEAMS_LIST_CHATS_COMPLETED = "teams.list_chats.completed"
    TEAMS_READ_MESSAGES_STARTED = "teams.read_messages.started"
    TEAMS_READ_MESSAGES_COMPLETED = "teams.read_messages.completed"
    DATABASE_QUERY_STARTED = "database.query.started"
    DATABASE_QUERY_COMPLETED = "database.query.completed"
    STORAGE_OPERATION_STARTED = "storage.operation.started"
    STORAGE_OPERATION_COMPLETED = "storage.operation.completed"
    KNOWLEDGE_SELECTION_COMPLETED = "knowledge.selection.completed"
    PROCEDURE_ACTION_RESOLVED = "procedure_action.resolved"
    COMMAND_AUTHORITY_COMPLETED = "command_authority.completed"
    APPROVAL_REQUESTED = "approval.requested"
    APPROVAL_COMPLETED = "approval.completed"
    AUTHORITY_SELECTED = "authority.selected"
    PROVENANCE_COMPLETED = "provenance.completed"
    COMMAND_EGRESS_COMPLETED = "command_egress.completed"
    SYNTHESIS_STARTED = "synthesis.started"
    SYNTHESIS_COMPLETED = "synthesis.completed"
    PERSISTENCE_STARTED = "persistence.started"
    PERSISTENCE_COMPLETED = "persistence.completed"
    SSE_STARTED = "sse.started"
    SSE_COMPLETED = "sse.completed"
    TURN_COMPLETED = "turn.completed"
    TURN_FAILED = "turn.failed"
    TURN_TIMEOUT = "turn.timeout"
    TURN_CANCELLED = "turn.cancelled"
    RUN_STALLED = "run.stalled"
    HEARTBEAT = "heartbeat"

@unique
class RunStatus(str, Enum):
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    TIMEOUT = "TIMEOUT"
    CANCELLED = "CANCELLED"
    STALLED = "STALLED"

TERMINAL_STATUSES = frozenset({RunStatus.COMPLETED, RunStatus.FAILED, RunStatus.TIMEOUT, RunStatus.CANCELLED})
STATUS_TRANSITIONS = MappingProxyType({
    RunStatus.PENDING: frozenset({RunStatus.RUNNING, *TERMINAL_STATUSES}),
    RunStatus.RUNNING: frozenset({RunStatus.STALLED, *TERMINAL_STATUSES}),
    RunStatus.STALLED: frozenset({RunStatus.RUNNING, *TERMINAL_STATUSES}),
    **{status: frozenset() for status in TERMINAL_STATUSES},
})

def validate_transition(previous: RunStatus, following: RunStatus) -> None:
    if following not in STATUS_TRANSITIONS[previous]:
        raise ValueError("Invalid run status transition")

# One-way projection; never infer a technical status from a UI warning/label.
PUBLIC_TERMINAL_OUTCOME = MappingProxyType({
    RunStatus.COMPLETED: "ok", RunStatus.FAILED: "error",
    RunStatus.TIMEOUT: "error", RunStatus.CANCELLED: "error",
})
PUBLIC_PROGRESS = MappingProxyType({
    Stage.PLANNING_STARTED: ("processing", "Understanding request…"),
    Stage.CONTEXT_SELECTION_STARTED: ("processing", "Selecting context…"),
    Stage.TEAMS_READ_MESSAGES_STARTED: ("teams_context", "Reading Teams conversation…"),
    Stage.KNOWLEDGE_SEARCH_STARTED: ("knowledge_retrieval", "Searching governed knowledge…"),
    Stage.CONTEXT_SELECTION_COMPLETED: ("evidence_processing", "Analyzing evidence…"),
    Stage.SYNTHESIS_STARTED: ("response_generation", "Preparing response…"),
})
# Only exact observed perf milestones map. Ambiguous marks stay documented, unmapped.
LEGACY_PERF_STAGES = MappingProxyType({
    "session_loaded": Stage.SESSION_LOAD_COMPLETED,
    "model_call_start": Stage.MODEL_REQUEST_STARTED,
    "model_call_end": Stage.MODEL_REQUEST_COMPLETED,
})
