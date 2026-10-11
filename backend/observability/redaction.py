"""Fail-closed default metadata projection, shared by every future sink."""
from enum import Enum, unique
from functools import lru_cache
from types import MappingProxyType
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
MODEL_PATTERN = r'(?:gemini-(?:2[.]5|3)-(?:flash|pro)(?:-preview)?(?:-[0-9]{3}|-[0-9]{2}-[0-9]{2})?|text-embedding-005|other)'

@lru_cache(maxsize=1)
def _lifecycle_fields():
    from .attributes import Attribute
    from .stages import Stage, RunStatus
    from .errors import ErrorCode
    return ((('slopanoc.stage', MappingProxyType({v.value:v.value for v in Stage})),
             ('slopanoc.error_code', MappingProxyType({v.value:v.value for v in ErrorCode})),
             ('slopanoc.status', MappingProxyType({v.value:v.value for v in RunStatus}))),
            tuple(v.value for v in (Attribute.RUN_ID, Attribute.SESSION_ID,
                  Attribute.TURN_ID, Attribute.THREAD_ID, Attribute.FAULT_ID)))

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


@lru_cache(maxsize=1)
def _projection_keys():
    from .dependency_contract import ENUM_FIELDS, INT_FIELDS, FLOAT_FIELDS, BOOL_FIELDS
    from .reliability_contract import ENUM_FIELDS as RELIABILITY_ENUMS, NUMERIC_FIELDS
    from .archive import CLOSURE_FIELDS
    model = frozenset(_model_enums()) | frozenset({
        'gen_ai.request.model','gen_ai.response.model','slopanoc.model_version',
        'slopanoc.logical_call_id','slopanoc.attempt_id','slopanoc.attempt','slopanoc.retry_count',
        'gen_ai.usage.input_tokens','gen_ai.usage.output_tokens','gen_ai.usage.cache_read.input_tokens',
        'slopanoc.total_tokens','slopanoc.candidate_tokens','slopanoc.thought_tokens',
        'slopanoc.tool_input_tokens','slopanoc.billable_characters','slopanoc.duration_ms',
        'slopanoc.ttft_ms','slopanoc.streaming','gen_ai.response.finish_reasons'})
    execution = frozenset(_execution_enums()) | {'slopanoc.result_count','slopanoc.timeout_observed'}
    return (model, execution, frozenset(ENUM_FIELDS)|INT_FIELDS|FLOAT_FIELDS|BOOL_FIELDS|{'http.response.status_code'},
        frozenset(RELIABILITY_ENUMS)|NUMERIC_FIELDS, CLOSURE_FIELDS)


_REJECT = object()


def _bounded_count(value):
    return value if type(value) is int and 0 <= value <= 2**63-1 else _REJECT


def _boolean(value):
    return value if type(value) is bool else _REJECT


def _enum_validator(allowed, *, strict=True, canonical=False):
    # Only finite contract vocabularies are captured. No request data is cached.
    if canonical:
        def validate(value):
            try:return allowed[value]
            except (KeyError, TypeError):return _REJECT
    else:
        def validate(value):
            return value if (type(value) is str if strict else isinstance(value,str)) and value in allowed else _REJECT
    return validate


def _pattern_validator(pattern):
    from re import compile
    match=compile(pattern).fullmatch
    def validate(value):
        return value if isinstance(value,str) and match(value) else _REJECT
    return validate


def _number_validator(upper=None):
    from math import isfinite
    def validate(value):
        return value if type(value) in (int,float) and isfinite(value) and value >= 0 and (upper is None or value <= upper) else _REJECT
    return validate


@lru_cache(maxsize=1)
def _span_validators():
    """Immutable finite field dispatch; the same dynamic checks run every time."""
    from .dependency_contract import ENUM_FIELDS, INT_FIELDS, FLOAT_FIELDS, BOOL_FIELDS
    from .reliability_contract import ENUM_FIELDS as REL_ENUMS, NUMERIC_FIELDS
    from .model_context import FINISH_REASONS
    validators=dict.fromkeys(COUNT_FIELDS,_bounded_count)
    lifecycle,identifiers=_lifecycle_fields()
    validators.update({key:_enum_validator(allowed,canonical=True) for key,allowed in lifecycle})
    identifier=_pattern_validator(r'[A-Za-z0-9_.:-]{1,128}')
    validators.update(dict.fromkeys(identifiers,identifier))
    validators['slopanoc.turn_id_origin']=lambda value:value if value in ('adk','execution') else _REJECT
    validators['slopanoc.config_version']=_pattern_validator(r'[0-9a-f]{16}')
    validators.update({key:_enum_validator(allowed,strict=False) for key,allowed in _model_enums().items()})
    model=_pattern_validator(MODEL_PATTERN)
    validators.update(dict.fromkeys(('gen_ai.request.model','gen_ai.response.model','slopanoc.model_version'),model))
    uuid=_pattern_validator(r'[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}')
    validators.update(dict.fromkeys(('slopanoc.logical_call_id','slopanoc.attempt_id'),uuid))
    validators.update(dict.fromkeys(MODEL_INT_FIELDS,_bounded_count))
    validators.update(dict.fromkeys(('slopanoc.duration_ms','slopanoc.ttft_ms'),_number_validator()))
    validators['slopanoc.streaming']=_boolean
    validators['gen_ai.response.finish_reasons']=lambda value:tuple(value) if isinstance(value,(tuple,list)) and len(value)<=16 and all(isinstance(v,str) and v in FINISH_REASONS for v in value) else _REJECT
    validators.update({key:_enum_validator(allowed) for key,allowed in _execution_enums().items()})
    validators['slopanoc.result_count']=_bounded_count
    validators['slopanoc.timeout_observed']=_boolean
    validators.update({key:_enum_validator(allowed) for key,allowed in ENUM_FIELDS.items()})
    validators.update(dict.fromkeys(INT_FIELDS,_bounded_count))
    validators.update(dict.fromkeys(FLOAT_FIELDS,_number_validator(3600)))
    validators.update(dict.fromkeys(BOOL_FIELDS,_boolean))
    validators['http.response.status_code']=lambda value:value if type(value) is int and 100<=value<=599 else _REJECT
    validators.update({key:_enum_validator(allowed) for key,allowed in REL_ENUMS.items()})
    validators.update(dict.fromkeys(NUMERIC_FIELDS,_number_validator(86400)))
    return MappingProxyType(validators)


def safe_span_attributes(attributes):
    """Exact typed projection in one present-field pass; unknown fields deny."""
    values=attributes or {}
    if not isinstance(values,Mapping):
        raise ValueError("Telemetry metadata must be a mapping")
    from .archive import CLOSURE_FIELDS,closure_attributes
    validators=_span_validators()
    safe={};closure=False
    for key,value in values.items():
        validate=validators.get(key)
        if validate is not None:
            projected=validate(value)
            if projected is not _REJECT:safe[key]=projected
        if key in CLOSURE_FIELDS:closure=True
    if closure:safe.update(closure_attributes(values))
    return safe



@lru_cache(maxsize=1)
def _model_enums():
    from .model_context import ModelAgent, ModelPurpose
    from .errors import ErrorCode
    return MappingProxyType({
        "slopanoc.agent": frozenset(v.value for v in ModelAgent),
        "slopanoc.model_operation": frozenset(v.value for v in ModelPurpose),
        "gen_ai.provider.name": frozenset({"gcp.vertex_ai","gcp.gemini","other"}),
        "gen_ai.operation.name": frozenset({"generate_content","embeddings"}),
        "slopanoc.workload": frozenset({"user_turn","warmup","ingestion","background"}),
        "slopanoc.usage_availability": frozenset({"KNOWN","PARTIAL","UNKNOWN"}),
        "slopanoc.ttft_availability": frozenset({"available","unavailable"}),
        "slopanoc.ttft_boundary": frozenset({"first_provider_output"}),
        "error.type": frozenset(v.value for v in ErrorCode),
    })

MODEL_INT_FIELDS = frozenset({'slopanoc.attempt','slopanoc.retry_count','gen_ai.usage.input_tokens',
    'gen_ai.usage.output_tokens','gen_ai.usage.cache_read.input_tokens',
    'slopanoc.total_tokens','slopanoc.candidate_tokens','slopanoc.thought_tokens',
    'slopanoc.tool_input_tokens','slopanoc.billable_characters'})


def safe_model_attributes(attributes):
    """Exact M3 projection, visiting present fields instead of absent fields."""
    import re
    import math
    from .model_context import FINISH_REASONS
    safe = {}
    enums = _model_enums()
    for key,value in attributes.items():
        allowed = enums.get(key)
        if allowed is not None:
            if isinstance(value,str) and value in allowed:safe[key] = value
        elif key in ('gen_ai.request.model','gen_ai.response.model','slopanoc.model_version'):
            if isinstance(value,str) and re.fullmatch(MODEL_PATTERN,value):safe[key] = value
        elif key in ('slopanoc.logical_call_id','slopanoc.attempt_id'):
            if isinstance(value,str) and re.fullmatch(r'[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}',value):safe[key] = value
        elif key in MODEL_INT_FIELDS:
            if type(value) is int and 0 <= value <= 2**63-1:safe[key] = value
        elif key in ('slopanoc.duration_ms','slopanoc.ttft_ms'):
            if type(value) in (int,float) and math.isfinite(value) and value >= 0:safe[key] = value
        elif key == 'slopanoc.streaming':
            if type(value) is bool:safe[key] = value
        elif key == 'gen_ai.response.finish_reasons':
            if isinstance(value,(tuple,list)) and len(value) <= 16 and all(isinstance(v,str) and v in FINISH_REASONS for v in value):
                safe[key] = tuple(value)
    return safe


@lru_cache(maxsize=1)
def _execution_enums():
    from .tool_instrumentation import TOOLS, CATEGORIES, RESULTS
    from .model_context import ModelPurpose
    return MappingProxyType({
        "slopanoc.tool": frozenset(TOOLS) | {"other"},
        "slopanoc.tool_category": frozenset(CATEGORIES),
        "slopanoc.execution_role": frozenset({"primary","supporting","coordination","system","unknown"}),
        "slopanoc.execution_purpose": frozenset(v.value for v in ModelPurpose),
        "slopanoc.result_category": frozenset(RESULTS),
        "slopanoc.execution_disposition": frozenset({"executed","not_executed","unknown"}),
    })

def safe_execution_attributes(attributes):
    safe = {}
    values = _execution_enums()
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
