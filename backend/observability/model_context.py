"""Server-owned model attribution and the shared observational execution handle."""
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from enum import Enum


class ModelAgent(str, Enum):
    TEAM_MANAGER = 'team_manager'
    TECHNICAL_AUTHORITY = 'technical_authority_engineer'
    INCIDENT_MANAGER = 'incident_manager'
    PROBLEM_MANAGER = 'problem_manager'
    AUTOMATED_OPERATIONS = 'automated_operations_engineer'
    SYSTEM = 'system'
    IMAGE = 'km_image_interpreter'
    RETRIEVAL = 'knowledge_retrieval'
    EMBEDDING = 'knowledge_embedding'


class ModelPurpose(str, Enum):
    ORCHESTRATION = 'orchestration'
    PRESENTATION = 'presentation'
    CLASSIFICATION = 'classification'
    SPECIALIST = 'specialist_reasoning'
    SYNTHESIS = 'synthesis'
    REPAIR = 'structured_output_repair'
    REMEDIATION = 'remediation'
    RESELECTION = 'action_reselection'
    WARMUP = 'warmup'
    IMAGE = 'image_interpretation'
    EMBEDDING = 'embedding'
    EMBEDDING_QUERY = 'embedding_query'
    EMBEDDING_DOCUMENT = 'embedding_document'


@dataclass(frozen=True)
class Attribution:
    agent: ModelAgent
    operation: ModelPurpose
    workload: str | None = None
    execution: object | None = None

_override = ContextVar('slopanoc_model_attribution', default=None)
_sink = ContextVar('slopanoc_model_usage_sink', default=None)
_runtime = ContextVar('slopanoc_model_runtime', default=None)


@contextmanager
def model_context(agent, operation, *, workload=None):
    """Explicit bounded context for direct calls and existing remediation boundaries."""
    if workload not in (None, 'user_turn', 'warmup', 'ingestion', 'background'):
        raise ValueError('Unknown model workload')
    token = _override.set(Attribution(ModelAgent(agent), ModelPurpose(operation), workload))
    try:
        yield
    finally:
        _override.reset(token)


@contextmanager
def observation_sink(sink, *, runtime=None):
    """Injected synchronous handoff. No queue, database, retries or durability claim.

    M10 can supply its bounded writer here without changing provider callsites.
    The sink must not perform unbounded I/O; failures are isolated by the producer.
    """
    token, rt = _sink.set(sink), _runtime.set(runtime)
    try:
        yield
    finally:
        _runtime.reset(rt)
        _sink.reset(token)


def model_activity(agent, operation, *, workload=None):
    """Attribution-only coroutine decorator; original arguments/result/exception."""
    from functools import wraps
    def decorate(fn):
        @wraps(fn)
        async def observed(*args, **kwargs):
            with model_context(agent, operation, workload=workload):
                return await fn(*args, **kwargs)
        return observed
    return decorate


FINISH_REASONS = frozenset({'stop', 'max_tokens', 'safety', 'recitation', 'other', 'blocklist',
    'prohibited_content', 'spii', 'malformed_function_call', 'image_safety', 'unexpected_tool_call',
    'image_prohibited_content', 'no_image', 'image_recitation', 'image_other', 'unspecified'})

# One canonical execution descriptor supplements the existing attribution owner.
# Handles are execution-local, never persisted; closed copied contexts are invalid.
_execution = ContextVar('slopanoc_execution', default=None)


def current_attribution():
    value = _override.get()
    if value is not None and value.execution is not None:
        from .agent_instrumentation import active_execution
        if active_execution() is None:
            return None
    return value
