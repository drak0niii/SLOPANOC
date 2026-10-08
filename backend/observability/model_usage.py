"""Metadata-only provider quantities and the inert M10 handoff contract."""
from typing import Literal, Protocol
from pydantic import AwareDatetime, Field, field_validator
from .schemas import Contract, Count, Duration, Identifier, TraceId, SpanId
from .model_context import ModelAgent, ModelPurpose, FINISH_REASONS
from .errors import ErrorCode
from .stages import RunStatus


class ModelUsage(Contract):
    input_tokens: Count | None = None
    output_tokens: Count | None = None
    candidate_tokens: Count | None = None
    cached_tokens: Count | None = None
    total_tokens: Count | None = None
    thought_tokens: Count | None = None
    tool_input_tokens: Count | None = None
    billable_characters: Count | None = None
    availability: Literal['UNKNOWN', 'PARTIAL', 'KNOWN'] = 'UNKNOWN'
    source: Literal['provider', 'unavailable'] = 'unavailable'


class ModelUsageObservation(Contract):
    telemetry_schema_version: Literal[1] = 1
    observation_id: Identifier
    logical_call_id: Identifier
    provider_request_id: Identifier
    attempt_id: Identifier
    attempt: Count
    provider: Literal['gcp.vertex_ai', 'gcp.gemini', 'other']
    model: Identifier
    response_model: Identifier | None = None
    agent: ModelAgent
    operation: ModelPurpose
    workload: Literal['user_turn', 'warmup', 'ingestion', 'background']
    streaming: bool
    run_id: Identifier | None = None
    session_id: Identifier | None = None
    turn_id: Identifier | None = None
    trace_id: TraceId | None = None
    span_id: SpanId | None = None
    started_at: AwareDatetime
    completed_at: AwareDatetime
    duration_ms: Duration
    ttft_ms: Duration | None = None
    ttft_boundary: Literal['first_provider_output'] | None = None
    status: RunStatus
    error_code: ErrorCode | None = None
    finish_reasons: tuple[str, ...] = Field(default=(), max_length=16)
    usage: ModelUsage = Field(default_factory=ModelUsage)

    @field_validator('finish_reasons')
    @classmethod
    def safe_finish_reasons(cls, values):
        if any(value not in FINISH_REASONS for value in values):
            raise ValueError('Unregistered finish reason')
        return values


class UsageObservationSink(Protocol):
    def __call__(self, observation: ModelUsageObservation) -> None: ...


def count(value):
    # Provider embedding stats use float; accept only exact integral quantities.
    if type(value) is float and value.is_integer():
        value = int(value)
    return value if type(value) is int and 0 <= value <= 2**63 - 1 else None


def extract_usage(payload, *, streaming=False, complete=True):
    """Project selected scalar fields only; never retain the raw response."""
    if not isinstance(payload, dict):
        return ModelUsage()
    raw = payload.get('usageMetadata', payload.get('usage_metadata'))
    if isinstance(raw, dict):
        mapping = {
            'input_tokens': ('promptTokenCount', 'prompt_token_count'),
            'candidate_tokens': ('candidatesTokenCount', 'candidates_token_count'),
            'cached_tokens': ('cachedContentTokenCount', 'cached_content_token_count'),
            'total_tokens': ('totalTokenCount', 'total_token_count'),
            'thought_tokens': ('thoughtsTokenCount', 'thoughts_token_count'),
            'tool_input_tokens': ('toolUsePromptTokenCount', 'tool_use_prompt_token_count'),
        }
        values = {key: count(raw.get(camel, raw.get(snake))) for key, (camel, snake) in mapping.items()}
        # Missing thought count is not an authoritative zero. Retain candidate
        # component while leaving full output unknown until both are reported.
        candidate, thought = values['candidate_tokens'], values['thought_tokens']
        if candidate is not None and thought is not None:
            values['output_tokens'] = count(candidate + thought)
        known = values['input_tokens'] is not None and values.get('output_tokens') is not None and values['total_tokens'] is not None
        invalid = any(raw.get(camel, raw.get(snake)) is not None and
                      count(raw.get(camel, raw.get(snake))) is None
                      for camel, snake in mapping.values())
        # Preserve provider-reported quantities, but conflicting subsets/totals
        # cannot be classified as a complete authoritative observation.
        input_count, cached, total = values['input_tokens'], values['cached_tokens'], values['total_tokens']
        output = values.get('output_tokens')
        invalid |= input_count is not None and cached is not None and cached > input_count
        invalid |= total is not None and any(v is not None and v > total for v in (input_count, output))
        invalid |= total is not None and input_count is not None and output is not None and input_count + output > total
        return ModelUsage(**values, availability='KNOWN' if known and complete and not invalid else 'PARTIAL'
            if any(v is not None for v in values.values()) else 'UNKNOWN', source='provider')
    embeddings = payload.get('embeddings')
    if embeddings is None and isinstance(payload.get('predictions'), list):
        embeddings = [p.get('embeddings', {}) for p in payload['predictions'] if isinstance(p, dict)]
    if isinstance(embeddings, list):
        tokens = [count(e.get('statistics', {}).get('token_count', e.get('statistics', {}).get('tokenCount')))
                  for e in embeddings if isinstance(e, dict) and isinstance(e.get('statistics', {}), dict)]
        input_tokens = count(sum(tokens)) if tokens and len(tokens) == len(embeddings) and all(t is not None for t in tokens) else None
        metadata = payload.get('metadata') or {}
        chars = count(metadata.get('billable_character_count', metadata.get('billableCharacterCount'))) if isinstance(metadata, dict) else None
        return ModelUsage(input_tokens=input_tokens, billable_characters=chars, source='provider',
            availability='KNOWN' if complete and input_tokens is not None else 'PARTIAL' if chars is not None or input_tokens is not None else 'UNKNOWN')
    return ModelUsage()
