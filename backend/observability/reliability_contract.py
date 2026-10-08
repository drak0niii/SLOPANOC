"""Finite M6 reliability vocabulary. Metadata never authorizes business work."""
from enum import Enum
import math
from .errors import ErrorCode

class Category(str, Enum):
    TURN = 'turn'
    CLEANUP = 'cleanup'
    SESSION = 'session_load'
    LOCK = 'session_lock'
    PLANNING = 'planning'
    ORCHESTRATION = 'orchestration'
    MODEL = 'model'
    AGENT = 'agent'
    TOOL = 'tool'
    GATEWAY = 'gateway'
    DATABASE_ACQUIRE = 'database_acquire'
    DATABASE_QUERY = 'database_query'
    KNOWLEDGE = 'knowledge'
    STORAGE = 'storage'
    SECRET = 'secret'
    PERSISTENCE = 'persistence'
    DELIVERY = 'sse_delivery'
    QUEUE = 'queue_wait'

CODES = {c: ErrorCode.TURN_TIMEOUT for c in Category}
CODES.update({Category.PLANNING: ErrorCode.PLANNING_TIMEOUT,
    Category.MODEL: ErrorCode.MODEL_TIMEOUT, Category.TOOL: ErrorCode.TOOL_TIMEOUT,
    Category.GATEWAY: ErrorCode.TOOL_TIMEOUT, Category.DATABASE_ACQUIRE: ErrorCode.DATABASE_TIMEOUT,
    Category.DATABASE_QUERY: ErrorCode.DATABASE_TIMEOUT, Category.KNOWLEDGE: ErrorCode.KNOWLEDGE_TIMEOUT,
    Category.STORAGE: ErrorCode.STORAGE_TIMEOUT, Category.SECRET: ErrorCode.TOOL_TIMEOUT,
    Category.PERSISTENCE: ErrorCode.DATABASE_TIMEOUT, Category.DELIVERY: ErrorCode.SSE_COMPLETION_MISMATCH,
    Category.QUEUE: ErrorCode.SSE_COMPLETION_MISMATCH})

EVENTS = frozenset({'run.stalled','reliability.progress_resumed','reliability.deadline_nearing',
    'reliability.deadline_exceeded','reliability.cleanup_started','reliability.cleanup_completed',
    'reliability.cleanup_failed','reliability.cleanup_timeout','reliability.late_completion',
    'reliability.worker_running','reliability.controller_failed'})
ENUM_FIELDS = {
    'slopanoc.deadline_category': frozenset(c.value for c in Category),
    'slopanoc.caller_disposition': frozenset({'waiting','completed','failed','timeout','cancelled','not_started'}),
    'slopanoc.worker_disposition': frozenset({'not_started','running','terminated','unknown'}),
    'slopanoc.cleanup_outcome': frozenset({'pending','running','completed','failed','timeout'}),
    'slopanoc.outcome_certainty': frozenset({'CONFIRMED_SUCCESS','CONFIRMED_FAILURE','NOT_STARTED','OUTCOME_UNKNOWN'}),
}
NUMERIC_FIELDS = frozenset({'slopanoc.remaining_seconds','slopanoc.progress_age_seconds',
    'slopanoc.lateness_seconds'})

def project(values):
    safe = {k:v for k,v in values.items() if k in ENUM_FIELDS and type(v) is str and v in ENUM_FIELDS[k]}
    safe.update({k:v for k,v in values.items() if k in NUMERIC_FIELDS and type(v) in (int,float)
        and math.isfinite(v) and 0 <= v <= 86400})
    return safe
