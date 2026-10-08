"""Safe JSON / OTel logging bridge. No raw message/exception serialization."""
from datetime import datetime, timezone
import json
import logging
from opentelemetry._logs import LogRecord as OTelLogRecord, SeverityNumber
from opentelemetry.sdk._logs import LogRecordProcessor, ReadableLogRecord
from .redaction import sanitize_metadata
from .schemas import LogRecord, OperationalEvent
from .stages import Stage
from .tracing import SCOPE, current_trace_ids, safe_scope


def safe_record(config, event=OperationalEvent.LEGACY, *, severity='INFO', metadata=None, error_code=None, run_id=None):
    trace_id, span_id = current_trace_ids()
    from .turn_trace import current_turn
    turn = current_turn()
    if run_id is None and turn is not None:
        run_id = turn.run_id
    event = event if isinstance(event, (Stage, OperationalEvent)) else OperationalEvent(event)
    # Delivery observation after root closure is explicitly associated with that
    # lifecycle. Ordinary late-worker logs must not inherit ended turn identity.
    if event == Stage.SSE_COMPLETED and turn is None:
        ended = current_turn(include_terminal=True)
        if ended is not None and run_id == ended.run_id:
            trace_id, span_id = ended.trace_id, ended.span_id
            turn = ended
    return LogRecord(timestamp=datetime.now(timezone.utc), severity=severity,
        service=config.otel_service_name, environment=config.otel_environment,
        event_name=event, message=event, error_code=error_code,
        trace_id=trace_id, span_id=span_id, run_id=run_id,
        turn_id=turn.turn_id if turn is not None else None,
        turn_id_origin=turn.turn_id_origin if turn is not None else None,
        metadata=sanitize_metadata(metadata or {}))


class SafeLogProcessor(LogRecordProcessor):
    def __init__(self, queue, resource):
        self.queue, self.resource = queue, resource

    def on_emit(self, record):
        try:
            if not record.instrumentation_scope or record.instrumentation_scope.name != SCOPE:
                self.queue.health.add('log', 'filtered')
                return
            # Revalidate nested data immediately, including formerly valid frozen
            # models mutated through dictionaries. Unknown raw SDK records denied.
            value = LogRecord.model_validate(record.log_record.body)
            if (value.service != self.resource.attributes['service.name'] or
                    value.environment != self.resource.attributes['deployment.environment.name']):
                self.queue.health.add('log', 'filtered')
                return
            self.queue.put(ReadableLogRecord(
                log_record=OTelLogRecord(timestamp=record.log_record.timestamp,
                    trace_id=int(value.trace_id,16) if value.trace_id else 0,
                    span_id=int(value.span_id,16) if value.span_id else 0,
                    severity_text=value.severity, severity_number=getattr(SeverityNumber, {'WARNING':'WARN','CRITICAL':'FATAL'}.get(value.severity,value.severity)),
                    body=value.model_dump(mode='json'), event_name=value.event_name.value),
                resource=self.resource, instrumentation_scope=safe_scope()))
        except (ValueError, TypeError, AttributeError):
            self.queue.health.add('log', 'filtered')

    def force_flush(self, timeout_millis=30000):
        return self.queue.flush(timeout_millis)

    def shutdown(self):
        self.queue.shutdown()


def emit(runtime, event, **fields):
    value = safe_record(runtime.config, event, **fields)
    if runtime.logger is not None:
        runtime.logger.emit(body=value.model_dump(mode='json'), event_name=value.event_name.value)
    return value


class SafeJsonFormatter(logging.Formatter):
    def __init__(self, config):
        super().__init__()
        self.config = config

    def format(self, record):
        # Never call getMessage(), inspect args, exc_info, stack_info or arbitrary
        # extras. Legacy producers keep their behavior, export only safe envelope.
        severity = 'CRITICAL' if record.levelno >= 50 else 'ERROR' if record.levelno >= 40 else 'WARNING' if record.levelno >= 30 else 'INFO' if record.levelno >= 20 else 'DEBUG'
        return safe_record(self.config, severity=severity).model_dump_json()


class ProductionLogging:
    """Format existing backend/root/Uvicorn handlers, restoring on lease release.

    No application callsite rewrite or arbitrary handler removal. Existing handler
    destinations remain owned by the host. Deployment must use this formatter for
    any additional handlers created after startup.
    """
    def __init__(self, config):
        self._saved = []
        self._added = None
        if config.otel_environment != 'production':
            return
        root = logging.getLogger()
        if not root.handlers:
            self._added = logging.StreamHandler()
            root.addHandler(self._added)
        loggers = [root]+[obj for name,obj in logging.Logger.manager.loggerDict.items()
            if isinstance(obj,logging.Logger) and name.startswith(('backend.', 'uvicorn', 'google.adk', 'opentelemetry'))]
        seen = set()
        for logger in loggers:
            for handler in logger.handlers:
                if id(handler) not in seen:
                    seen.add(id(handler))
                    self._saved.append((handler, handler.formatter))
                    handler.setFormatter(SafeJsonFormatter(config))

    def close(self):
        for handler, formatter in self._saved:
            handler.setFormatter(formatter)
        if self._added:
            logging.getLogger().removeHandler(self._added)
            self._added.close()


def emit_model_observation(runtime, observation):
    """Explicit correlation prevents warmup/background logs borrowing chat identity."""
    event = Stage.MODEL_REQUEST_COMPLETED if observation.status.value == 'COMPLETED' else (
        Stage.MODEL_REQUEST_TIMEOUT if observation.status.value == 'TIMEOUT' else Stage.MODEL_REQUEST_FAILED)
    metadata = {k:getattr(observation.usage,k) for k in ('input_tokens','output_tokens','cached_tokens')
        if getattr(observation.usage,k) is not None}
    metadata.update(request_count=1,retry_count=observation.attempt-1)
    turn = None
    if observation.run_id:
        from .turn_trace import current_turn
        turn = current_turn()
    value = LogRecord(timestamp=observation.completed_at, severity='INFO' if observation.status.value=='COMPLETED' else 'WARNING',
        service=runtime.config.otel_service_name, environment=runtime.config.otel_environment,
        event_name=event,message=event,error_code=observation.error_code,
        trace_id=observation.trace_id,span_id=observation.span_id,run_id=observation.run_id,
        turn_id=observation.turn_id,turn_id_origin=turn.turn_id_origin if turn is not None and observation.turn_id else None,
        metadata=metadata)
    if runtime.logger is not None:
        runtime.logger.emit(body=value.model_dump(mode='json'),event_name=event.value)
    return value


def emit_dependency_failure(runtime, scope):
    """Explicit dependency correlation; system workloads never borrow a user run."""
    native = scope.span.get_span_context()
    turn = scope.turn
    value = LogRecord(timestamp=datetime.now(timezone.utc), severity='ERROR',
        service=runtime.config.otel_service_name, environment=runtime.config.otel_environment,
        event_name=OperationalEvent.LEGACY, message=OperationalEvent.LEGACY,
        error_code=scope.error, trace_id=format(native.trace_id, '032x') if native.is_valid else None,
        span_id=format(native.span_id, '016x') if native.is_valid else None,
        run_id=turn.run_id if turn else None, turn_id=turn.turn_id if turn else None,
        turn_id_origin=turn.turn_id_origin if turn else None, metadata={})
    if runtime.logger is not None:
        runtime.logger.emit(body=value.model_dump(mode='json'), event_name=value.event_name.value)


def accounting_failure(runtime):
    """One safe structured failure envelope; never serialize persistence exceptions."""
    if runtime is None: return
    try:
        from .errors import ErrorCode
        value = safe_record(runtime.config, OperationalEvent.DEGRADED, severity='ERROR',
                            error_code=ErrorCode.COST_LEDGER_PERSIST_FAILED)
        if runtime.logger is not None:
            runtime.logger.emit(body=value.model_dump(mode='json'),event_name=value.event_name.value)
        else:
            import sys
            sys.stderr.write(value.model_dump_json()+'\n')
    except Exception:
        pass
