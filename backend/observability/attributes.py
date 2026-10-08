"""Attribute and cardinality contract; metric values need a bounded registry."""
from enum import Enum, unique

@unique
class Attribute(str, Enum):
    SERVICE_NAME = "service.name"
    SERVICE_VERSION = "service.version"
    ENVIRONMENT = "deployment.environment.name"
    CLOUD_PROVIDER = "cloud.provider"
    CLOUD_PLATFORM = "cloud.platform"
    REGION = "cloud.region"
    MODEL = "gen_ai.request.model"
    PROVIDER = "gen_ai.provider.name"
    GIT_SHA = "slopanoc.git_sha"
    RELEASE_ID = "slopanoc.release_id"
    CLOUD_RUN_REVISION = "slopanoc.cloud_run_revision"
    MODEL_VERSION = "slopanoc.model_version"
    AGENT_CONTRACT_VERSION = "slopanoc.agent_contract_version"
    PROMPT_VERSION = "slopanoc.prompt_version"
    TOOL_SCHEMA_VERSION = "slopanoc.tool_schema_version"
    TURN_PLAN_SCHEMA_VERSION = "slopanoc.turn_plan_schema_version"
    TELEMETRY_SCHEMA_VERSION = "slopanoc.telemetry_schema_version"
    COST_SCHEMA_VERSION = "slopanoc.cost_schema_version"
    PRICING_VERSION = "slopanoc.pricing_version"
    CONFIG_VERSION = "slopanoc.config_version"
    STATUS = "slopanoc.status"
    TURN_ID_ORIGIN = "slopanoc.turn_id_origin"
    STAGE = "slopanoc.stage"
    AGENT = "slopanoc.agent"
    TOOL = "slopanoc.tool"
    EXECUTION_ROLE = "slopanoc.execution_role"
    EXECUTION_PURPOSE = "slopanoc.execution_purpose"
    TOOL_CATEGORY = "slopanoc.tool_category"
    RESULT_CATEGORY = "slopanoc.result_category"
    RESULT_COUNT = "slopanoc.result_count"
    EXECUTION_DISPOSITION = "slopanoc.execution_disposition"
    TIMEOUT_OBSERVED = "slopanoc.timeout_observed"
    DEADLINE_CATEGORY = "slopanoc.deadline_category"
    CALLER_DISPOSITION = "slopanoc.caller_disposition"
    WORKER_DISPOSITION = "slopanoc.worker_disposition"
    CLEANUP_OUTCOME = "slopanoc.cleanup_outcome"
    OUTCOME_CERTAINTY = "slopanoc.outcome_certainty"
    REMAINING_SECONDS = "slopanoc.remaining_seconds"
    PROGRESS_AGE_SECONDS = "slopanoc.progress_age_seconds"
    LATENESS_SECONDS = "slopanoc.lateness_seconds"
    DEPENDENCY = "slopanoc.dependency"
    DOMAIN = "slopanoc.domain"
    DIALOGUE_ACT = "slopanoc.dialogue_act"
    AUTHORITY_MODE = "slopanoc.authority_mode"
    SOURCE_MODE = "slopanoc.source_mode"
    OUTCOME = "slopanoc.outcome"
    RETRY_COUNT = "slopanoc.retry_count"
    ERROR_CODE = "slopanoc.error_code"
    RUN_ID = "slopanoc.run_id"
    TURN_ID = "slopanoc.turn_id"
    SESSION_ID = "slopanoc.session_id"
    THREAD_ID = "slopanoc.thread_id"
    CASE_ID = "slopanoc.case_id"
    FAULT_ID = "slopanoc.fault_id"
    USER_ID = "slopanoc.user_id"
    TEAMS_CHAT_ID = "slopanoc.teams_chat_id"
    APPROVAL_ID = "slopanoc.approval_id"
    EXECUTION_ID = "slopanoc.execution_id"
    KNOWLEDGE_ID = "slopanoc.knowledge_id"
    SOURCE_ID = "slopanoc.source_id"

RESOURCE_ATTRIBUTES = frozenset({Attribute.SERVICE_NAME, Attribute.SERVICE_VERSION,
    Attribute.ENVIRONMENT, Attribute.CLOUD_PROVIDER, Attribute.CLOUD_PLATFORM,
    Attribute.REGION, Attribute.GIT_SHA, Attribute.RELEASE_ID, Attribute.CLOUD_RUN_REVISION,
    Attribute.TELEMETRY_SCHEMA_VERSION})
RELEASE_ATTRIBUTES = frozenset({Attribute.SERVICE_VERSION, Attribute.GIT_SHA,
    Attribute.RELEASE_ID, Attribute.CLOUD_RUN_REVISION, Attribute.ENVIRONMENT,
    Attribute.REGION, Attribute.MODEL, Attribute.MODEL_VERSION,
    Attribute.AGENT_CONTRACT_VERSION, Attribute.PROMPT_VERSION,
    Attribute.TOOL_SCHEMA_VERSION, Attribute.TURN_PLAN_SCHEMA_VERSION,
    Attribute.TELEMETRY_SCHEMA_VERSION, Attribute.COST_SCHEMA_VERSION,
    Attribute.PRICING_VERSION, Attribute.CONFIG_VERSION})
HIGH_CARDINALITY_FIELDS = frozenset({"run_id", "turn_id", "session_id", "user_id",
    "teams_chat_id", "chat_id", "thread_id", "case_id", "fault_id", "source_id",
    "knowledge_id", "approval_id", "execution_id", "trace_id", "span_id"})
METRIC_LABELS = frozenset({"environment", "agent", "tool", "dependency", "model",
    "provider", "domain", "status", "error_code", "operation", "tool_category", "window"})

def validate_metric_labels(labels: dict[str, str], *, value_registry: dict[str, frozenset[str]]) -> dict[str, str]:
    """Require registered keys AND bounded values; no arbitrary strings as labels."""
    for key, value in labels.items():
        if key not in METRIC_LABELS or not isinstance(value, str) or value not in value_registry.get(key, ()):
            raise ValueError("Unregistered metric label or value")
    return dict(labels)
