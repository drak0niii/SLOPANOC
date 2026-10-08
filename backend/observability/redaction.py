"""Fail-closed default metadata projection, shared by every future sink."""
from enum import Enum, unique
from collections.abc import Mapping
from .attributes import HIGH_CARDINALITY_FIELDS

@unique
class DataClassification(str, Enum):
    SAFE = "safe"
    RESTRICTED = "restricted"
    PROHIBITED = "prohibited"

COUNT_FIELDS = frozenset({"message_count", "member_count", "evidence_count",
    "candidate_count", "document_count", "selected_count", "excluded_count",
    "input_tokens", "output_tokens", "cached_tokens", "retry_count", "request_count",
    "token_footprint", "egress_kept_count", "egress_removed_count"})
SAFE_TYPED_FIELDS = frozenset({"stage", "status", "error_code", "duration_ms",
    "timestamp", "agent", "tool", "dependency", "model", "provider"})
PROHIBITED_FIELDS = frozenset({"password", "api_key", "access_token", "oauth_token",
    "bearer_token", "authorization", "authorization_header", "headers", "token",
    "secret", "prompt", "full_prompt", "response", "model_response", "messages",
    "teams_message_body", "attachment_contents", "knowledge_body", "chain_of_thought",
    "reasoning", "query_text", "command", "user_request_text", "diagnostic_objective",
    "title", "section_heading", "content", "error_message", "stack_trace"})

def classify_field(name: str) -> DataClassification:
    name = name.lower().replace("-", "_")
    if name in HIGH_CARDINALITY_FIELDS:
        return DataClassification.RESTRICTED
    if name in COUNT_FIELDS or name in SAFE_TYPED_FIELDS:
        return DataClassification.SAFE
    # Unknown fields are denied, not implicitly classified as safe.
    return DataClassification.PROHIBITED

def sanitize_metadata(metadata: Mapping[str, object]) -> dict[str, int]:
    if not isinstance(metadata, Mapping):
        raise ValueError("Telemetry metadata must be a mapping")
    return {key: value for key, value in metadata.items()
            if key in COUNT_FIELDS and type(value) is int and 0 <= value <= 2**63 - 1}

def validate_metadata(metadata: object) -> dict[str, int]:
    if not isinstance(metadata, Mapping):
        raise ValueError("Unsafe telemetry metadata")
    safe = sanitize_metadata(metadata)
    if len(metadata) != len(safe):
        raise ValueError("Unsafe telemetry metadata")
    return safe


def safe_span_attributes(attributes):
    """M2 typed lifecycle/correlation fields plus count-only arbitrary metadata."""
    from .attributes import Attribute
    from .stages import Stage, RunStatus
    from .errors import ErrorCode
    safe = sanitize_metadata(attributes or {})
    for key, enum in ((Attribute.STAGE.value, Stage), (Attribute.ERROR_CODE.value, ErrorCode), ("slopanoc.status", RunStatus)):
        try:
            safe[key] = enum((attributes or {})[key]).value
        except (KeyError, ValueError, TypeError):
            pass
    import re
    for key in (Attribute.RUN_ID.value, Attribute.SESSION_ID.value, Attribute.TURN_ID.value,
                Attribute.THREAD_ID.value, Attribute.FAULT_ID.value):
        value = (attributes or {}).get(key)
        if isinstance(value, str) and re.fullmatch(r'[A-Za-z0-9_.:-]{1,128}', value):
            safe[key] = value
    origin = (attributes or {}).get('slopanoc.turn_id_origin')
    if origin in ('adk', 'execution'):
        safe['slopanoc.turn_id_origin'] = origin
    version = (attributes or {}).get(Attribute.CONFIG_VERSION.value)
    if isinstance(version, str) and re.fullmatch(r'[0-9a-f]{16}', version):
        safe[Attribute.CONFIG_VERSION.value] = version
    safe.update(safe_model_attributes(attributes or {}))
    safe.update(safe_execution_attributes(attributes or {}))
    from .dependency_contract import project
    safe.update(project(attributes or {}))
    from .reliability_contract import project as reliability_project
    safe.update(reliability_project(attributes or {}))
    return safe



def safe_model_attributes(attributes):
    """Exact M3 metadata projection; content-shaped GenAI fields never admitted."""
    import re
    import math
    from .model_context import ModelAgent, ModelPurpose
    from .model_context import FINISH_REASONS
    from .errors import ErrorCode
    safe = {}
    enums = {'slopanoc.agent': {v.value for v in ModelAgent},
        'slopanoc.model_operation': {v.value for v in ModelPurpose},
        'gen_ai.provider.name': {'gcp.vertex_ai','gcp.gemini','other'},
        'gen_ai.operation.name': {'generate_content','embeddings'},
        'slopanoc.workload': {'user_turn','warmup','ingestion','background'},
        'slopanoc.usage_availability': {'KNOWN','PARTIAL','UNKNOWN'},
        'slopanoc.ttft_availability': {'available','unavailable'},
        'slopanoc.ttft_boundary': {'first_provider_output'},
        'error.type':{v.value for v in ErrorCode}}
    for key, values in enums.items():
        value = attributes.get(key)
        if isinstance(value,str) and value in values:
            safe[key] = value
    for key in ('gen_ai.request.model','gen_ai.response.model','slopanoc.model_version'):
        value = attributes.get(key)
        if isinstance(value,str) and (value == 'other' or re.fullmatch(r'(?:gemini-|text-embedding-|embedding-)[A-Za-z0-9_.-]{1,100}',value)):
            safe[key] = value
    for key in ('slopanoc.logical_call_id','slopanoc.attempt_id'):
        value = attributes.get(key)
        if isinstance(value,str) and re.fullmatch(r'[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}',value):
            safe[key] = value
    for key in ('slopanoc.attempt','slopanoc.retry_count','gen_ai.usage.input_tokens',
            'gen_ai.usage.output_tokens','gen_ai.usage.cache_read.input_tokens',
            'slopanoc.total_tokens','slopanoc.candidate_tokens','slopanoc.thought_tokens',
            'slopanoc.tool_input_tokens','slopanoc.billable_characters'):
        value = attributes.get(key)
        if type(value) is int and 0 <= value <= 2**63-1:
            safe[key] = value
    for key in ('slopanoc.duration_ms','slopanoc.ttft_ms'):
        value = attributes.get(key)
        if type(value) in (int,float) and math.isfinite(value) and value >= 0:
            safe[key] = value
    value = attributes.get('slopanoc.streaming')
    if type(value) is bool:
        safe['slopanoc.streaming'] = value
    reasons = attributes.get('gen_ai.response.finish_reasons')
    if isinstance(reasons,(tuple,list)) and len(reasons) <= 16 and all(isinstance(v,str) and v in FINISH_REASONS for v in reasons):
        safe['gen_ai.response.finish_reasons'] = tuple(reasons)
    return safe


def safe_execution_attributes(attributes):
    from .tool_instrumentation import TOOLS, CATEGORIES, RESULTS
    from .model_context import ModelPurpose
    safe = {}
    values = {'slopanoc.tool':set(TOOLS) | {'other'}, 'slopanoc.tool_category':CATEGORIES,
        'slopanoc.execution_role':{'primary','supporting','coordination','system','unknown'},
        'slopanoc.execution_purpose':{v.value for v in ModelPurpose},
        'slopanoc.result_category':RESULTS,
        'slopanoc.execution_disposition':{'executed','not_executed','unknown'}}
    for key, allowed in values.items():
        value = attributes.get(key)
        if type(value) is str and value in allowed:
            safe[key] = value
    value = attributes.get('slopanoc.result_count')
    if type(value) is int and 0 <= value <= 2**63-1:
        safe['slopanoc.result_count'] = value
    value = attributes.get('slopanoc.timeout_observed')
    if type(value) is bool:
        safe['slopanoc.timeout_observed'] = value
    return safe
