"""Model operations and provider attempts. No execution authority or accounting."""
import asyncio
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime, timezone
import time
from uuid import uuid4, uuid5, NAMESPACE_URL
from opentelemetry import context, trace
from opentelemetry.trace import SpanKind, Status, StatusCode
from .model_context import Attribution, _override, _sink, _runtime, current_attribution
from .model_usage import ModelUsage, ModelUsageObservation, extract_usage
from .stages import RunStatus, Stage
from .errors import ErrorCode
from .turn_trace import current_turn, degraded

_active = ContextVar('slopanoc_model_operation', default=None)
from .model_context import FINISH_REASONS


def protect_content():
    # Hard privacy policy, not a user-configurable content logging feature.
    import os
    os.environ['ADK_CAPTURE_MESSAGE_CONTENT_IN_SPANS'] = 'false'
    os.environ['OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT'] = 'false'


def error_category(exc=None, code=None):
    from .deadlines import cancellation_cause
    exc = cancellation_cause(exc)
    if isinstance(exc, (asyncio.CancelledError, GeneratorExit)):
        return RunStatus.CANCELLED, ErrorCode.TURN_CANCELLED
    import httpx
    if code is None:
        code = getattr(exc, 'code', None)
    if isinstance(exc, (TimeoutError, httpx.TimeoutException)) or code in (408, 504):
        return RunStatus.TIMEOUT, ErrorCode.MODEL_TIMEOUT
    if code == 429:
        return RunStatus.FAILED, ErrorCode.MODEL_RATE_LIMIT
    if code in (401, 403):
        return RunStatus.FAILED, ErrorCode.MODEL_AUTH_ERROR
    from google.auth.exceptions import GoogleAuthError
    if isinstance(exc, GoogleAuthError):
        return RunStatus.FAILED, ErrorCode.MODEL_AUTH_ERROR
    if isinstance(exc, (ValueError, TypeError)):
        return RunStatus.FAILED, ErrorCode.MODEL_INVALID_RESPONSE
    return RunStatus.FAILED, ErrorCode.MODEL_PROVIDER_ERROR


def guarded(runtime, fn, *args, **kwargs):
    try:
        return fn(*args, **kwargs)
    except Exception:
        degraded(runtime)
        return None


def native_ids(span):
    c = span.get_span_context()
    return (format(c.trace_id, '032x'), format(c.span_id, '016x')) if c.is_valid else (None, None)


class ModelOperation:
    def __init__(self, model, attribution, *, streaming=False, provider='other', runtime=None, clock=time.monotonic):
        protect_content()
        self.id = str(uuid4())
        override = current_attribution()
        self.attribution = override if override is not None and override.agent == attribution.agent else attribution
        self.turn = current_turn() if self.attribution.workload not in ('warmup', 'ingestion', 'background') else None
        self.runtime = runtime or _runtime.get() or (self.turn.runtime if self.turn else None)
        if self.runtime is None:
            from .runtime import current_runtime
            self.runtime = current_runtime()
        self.workload = self.attribution.workload or ('user_turn' if self.turn else 'background')
        self.model, self.provider, self.streaming = model, provider, streaming
        self.clock, self.started, self.started_at = clock, clock(), datetime.now(timezone.utc)
        self.attempts = []
        self.requests = {}
        self.responses = []
        self.sink = _sink.get()
        self.span = trace.INVALID_SPAN
        self.closed = False
        if self.runtime is not None and self.runtime.enabled:
            from .agent_instrumentation import execution_parent
            from .dependency_instrumentation import dependency_parent
            parent = self.turn.model_parent() if self.turn else dependency_parent(None) or execution_parent(None) or trace.INVALID_SPAN
            self.span = guarded(self.runtime, self.runtime.tracer.start_span, 'slopanoc.model.operation',
                context=trace.set_span_in_context(parent, context.Context()), attributes=self.attributes()) or trace.INVALID_SPAN

    def attributes(self):
        attrs = {'gen_ai.provider.name': self.provider, 'gen_ai.request.model': self.model,
            'gen_ai.operation.name': 'embeddings' if self.attribution.operation.value.startswith('embedding') else 'generate_content',
            'slopanoc.agent': self.attribution.agent.value, 'slopanoc.model_operation': self.attribution.operation.value,
            'slopanoc.workload': self.workload, 'slopanoc.streaming': self.streaming,
            'slopanoc.logical_call_id': self.id}
        if self.turn is not None:
            attrs.update(self.turn.attributes())
        return attrs

    @contextmanager
    def attached(self):
        token = _active.set(self)
        native = guarded(self.runtime, context.attach, trace.set_span_in_context(self.span, context.Context()))
        try:
            yield
        finally:
            if native is not None:
                guarded(self.runtime, context.detach, native)
            _active.reset(token)

    def attempt(self, request_id=None):
        request_id = request_id or str(uuid4())
        self.requests[request_id] = self.requests.get(request_id, 0)+1
        attempt = ModelAttempt(self, request_id, self.requests[request_id])
        self.attempts.append(attempt)
        return attempt

    def finish(self, exc=None):
        if self.closed:
            return
        self.closed = True
        _last_model.set((self.turn.run_id if self.turn else None, self.attribution.agent.value, self.id))
        for attempt in self.attempts:
            if not attempt.closed:
                guarded(self.runtime, attempt.finish, exc or GeneratorExit())
        status, error = error_category(exc) if exc is not None else (RunStatus.COMPLETED, None)
        attrs = self.attributes() | {'slopanoc.status': status.value,
            'slopanoc.retry_count': sum(a.number > 1 for a in self.attempts),
            'slopanoc.duration_ms': (self.clock()-self.started)*1000}
        if error:
            attrs['slopanoc.error_code'] = error.value
        guarded(self.runtime, self.span.set_attributes, attrs)
        guarded(self.runtime, self.span.set_status, Status(StatusCode.ERROR if status in (RunStatus.FAILED, RunStatus.TIMEOUT) else StatusCode.UNSET))
        guarded(self.runtime, self.span.end)


class ModelAttempt:
    def __init__(self, operation, request_id, number):
        self.operation = operation
        self.request_id = request_id
        self.number = number
        self.id = str(uuid5(NAMESPACE_URL, f'{operation.id}:{operation.provider}:{request_id}:{self.number}'))
        self.started, self.started_at = operation.clock(), datetime.now(timezone.utc)
        self.usage = ModelUsage()
        self.ttft = self.response_model = None
        self.finish_reasons = ()
        self.closed = False
        self.span = trace.INVALID_SPAN
        r = operation.runtime
        from .slo_sources import observe
        observe(operation.turn, "require", "model")
        if r is not None and r.enabled:
            self.span = guarded(r, r.tracer.start_span, 'gen_ai.request', kind=SpanKind.CLIENT,
                context=trace.set_span_in_context(operation.span, context.Context()), attributes=self.attributes()) or trace.INVALID_SPAN
        guarded(r, self.span.add_event, Stage.MODEL_REQUEST_STARTED.value, self.attributes())

    def attributes(self):
        return self.operation.attributes() | {'slopanoc.attempt': self.number,
            'slopanoc.retry_count': self.number-1, 'slopanoc.attempt_id': self.id}

    def observe(self, payload):
        if self.closed or not isinstance(payload, dict):
            return
        r = self.operation.runtime
        try:
            usage = extract_usage(payload, streaming=self.operation.streaming)
            if usage.source == 'provider':
                self.usage = usage  # cumulative snapshot replaces, never adds
            model = payload.get('modelVersion', payload.get('model_version'))
            from .model_provider import safe_model
            if model is not None:
                self.response_model = safe_model(model)
            reasons = []
            first = False
            for candidate in payload.get('candidates') or []:
                if not isinstance(candidate, dict):
                    continue
                reason = candidate.get('finishReason', candidate.get('finish_reason'))
                if isinstance(reason, str) and reason.lower() in FINISH_REASONS:
                    reasons.append(reason.lower())
                content = candidate.get('content') or {}
                for part in content.get('parts') or []:
                    if not isinstance(part, dict) or part.get('thought'):
                        continue
                    text, call, inline = part.get('text'), part.get('functionCall'), part.get('inlineData')
                    if ((isinstance(text, str) and bool(text)) or
                            (isinstance(call, dict) and isinstance(call.get('name'), str) and bool(call['name'])) or
                            (isinstance(inline, dict) and bool(inline.get('data')))):
                        first = True
            if first and self.operation.turn is not None:
                guarded(r, self.operation.turn.progress)
            if reasons:
                self.finish_reasons = tuple(dict.fromkeys(reasons))
            if self.operation.streaming and first and self.ttft is None:
                self.ttft = (self.operation.clock()-self.started)*1000
                guarded(r, self.span.add_event, Stage.MODEL_FIRST_TOKEN.value,
                    {'slopanoc.ttft_ms': self.ttft, 'slopanoc.ttft_boundary': 'first_provider_output'})
        except Exception:
            self.usage = ModelUsage()
            degraded(r)

    def finish(self, exc=None, *, code=None):
        if self.closed:
            return
        self.closed = True
        op, r = self.operation, self.operation.runtime
        status, error = error_category(exc, code) if exc is not None or code is not None else (RunStatus.COMPLETED, None)
        if status != RunStatus.COMPLETED and self.usage.availability != 'UNKNOWN':
            self.usage = self.usage.model_copy(update={'availability': 'PARTIAL'})
        duration = (op.clock()-self.started)*1000
        attrs = self.attributes() | {'slopanoc.status': status.value, 'slopanoc.duration_ms': duration,
            'slopanoc.usage_availability': self.usage.availability,
            'slopanoc.ttft_availability': 'available' if self.ttft is not None else 'unavailable'}
        usage_keys = {'input_tokens':'gen_ai.usage.input_tokens', 'output_tokens':'gen_ai.usage.output_tokens',
            'cached_tokens':'gen_ai.usage.cache_read.input_tokens', 'total_tokens':'slopanoc.total_tokens',
            'candidate_tokens':'slopanoc.candidate_tokens', 'thought_tokens':'slopanoc.thought_tokens',
            'tool_input_tokens':'slopanoc.tool_input_tokens', 'billable_characters':'slopanoc.billable_characters'}
        attrs.update({key:getattr(self.usage,field) for field,key in usage_keys.items() if getattr(self.usage,field) is not None})
        if self.ttft is not None:
            attrs.update({'slopanoc.ttft_ms':self.ttft, 'slopanoc.ttft_boundary':'first_provider_output'})
        if self.response_model:
            attrs['gen_ai.response.model'] = self.response_model
        if self.finish_reasons:
            attrs['gen_ai.response.finish_reasons'] = self.finish_reasons
        if error:
            attrs.update({'slopanoc.error_code':error.value, 'error.type':error.value})
        guarded(r, self.span.set_attributes, attrs)
        event = Stage.MODEL_REQUEST_COMPLETED if status == RunStatus.COMPLETED else Stage.MODEL_REQUEST_TIMEOUT if status == RunStatus.TIMEOUT else Stage.MODEL_REQUEST_FAILED
        guarded(r, self.span.add_event, event.value, attrs)
        guarded(r, self.span.set_status, Status(StatusCode.ERROR if status in (RunStatus.FAILED,RunStatus.TIMEOUT) else StatusCode.UNSET))
        guarded(r, self.span.end)
        def handoff():
            trace_id, span_id = native_ids(self.span)
            value = ModelUsageObservation(observation_id=self.id, logical_call_id=op.id,
                provider_request_id=self.request_id, attempt_id=self.id, attempt=self.number, provider=op.provider, model=op.model,
                response_model=self.response_model, agent=op.attribution.agent, operation=op.attribution.operation,
                workload=op.workload, streaming=op.streaming, run_id=op.turn.run_id if op.turn else None,
                session_id=op.turn.session_id if op.turn else None, turn_id=op.turn.turn_id if op.turn else None,
                trace_id=trace_id, span_id=span_id, started_at=self.started_at,
                completed_at=datetime.now(timezone.utc), duration_ms=duration, ttft_ms=self.ttft,
                ttft_boundary='first_provider_output' if self.ttft is not None else None,
                status=status,error_code=error,finish_reasons=self.finish_reasons,usage=self.usage)
            from .slo_sources import model_observation
            guarded(r, model_observation, r, value, op.turn)
            if r is not None and r.enabled:
                from .logging import emit_model_observation
                guarded(r, emit_model_observation, r, value)
                from .model_metrics import record
                guarded(r, record, r, value)
            if op.sink is not None:
                guarded(r, op.sink, value)
        guarded(r, handoff)

_last_model = ContextVar('slopanoc_last_model_call', default=None)


def observe_invalid_output(agent):
    """Structured validation failed AFTER successful provider transport.

    Separate metadata span; no new provider attempt/usage observation. Called only
    by the existing server validator, never by inspecting model text here.
    """
    guarded(None, _observe_invalid_output, agent)


def _observe_invalid_output(agent):
    turn = current_turn()
    last = _last_model.get()
    if turn is None or last is None or last[0] != turn.run_id or last[1] != agent:
        return
    runtime = turn.runtime
    if runtime is None or not runtime.enabled:
        return
    attrs = turn.attributes() | {'slopanoc.agent':agent,'slopanoc.logical_call_id':last[2],
        'slopanoc.error_code':ErrorCode.MODEL_INVALID_RESPONSE.value,'slopanoc.status':RunStatus.FAILED.value}
    native = guarded(runtime,runtime.tracer.start_span,'slopanoc.model.validation',
        context=trace.set_span_in_context(turn.model_parent(),context.Context()),attributes=attrs)
    if native is not None:
        guarded(runtime,native.add_event,Stage.MODEL_REQUEST_FAILED.value,attrs)
        guarded(runtime,native.set_status,Status(StatusCode.ERROR))
        guarded(runtime,native.end)
