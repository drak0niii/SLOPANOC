"""Unsampled, finite M6 metrics. No payload, correlation ID or exemplars."""
from dataclasses import replace
import math
from .reliability_contract import Category
from .stages import RunStatus
from .attributes import validate_metric_labels
from .tracing import SCOPE
from opentelemetry.sdk.metrics.view import View

NAMES = {
    'turn_timeout':'slopanoc.turn.timeouts', 'deadline_exceeded':'slopanoc.stage.timeouts',
    'stall':'slopanoc.turn.stalls','stall_duration':'slopanoc.turn.stall_duration',
    'progress_resumed':'slopanoc.turn.progress_resumed','cancellation':'slopanoc.turn.cancellations',
    'cleanup_timeout':'slopanoc.cleanup.timeouts','cleanup_failure':'slopanoc.cleanup.failures',
    'cleanup_duration':'slopanoc.cleanup.duration','late_completion':'slopanoc.work.late_completions',
    'worker_running':'slopanoc.work.detached','saturation':'slopanoc.executor.saturation',
    'admission_rejected':'slopanoc.executor.admission_rejected','queue_saturation':'slopanoc.queue.saturation',
    'lock_wait':'slopanoc.session.lock_wait','retry':'slopanoc.reliability.retries',
    'retry_denied':'slopanoc.reliability.retry_denied',
    'retry_exhausted':'slopanoc.reliability.retry_exhausted',
    'controller_failure':'slopanoc.reliability.controller_failures',
    'deadline_overshoot':'slopanoc.reliability.deadline_overshoot'}
HISTOGRAMS = frozenset(NAMES[k] for k in ('stall_duration','cleanup_duration','lock_wait','deadline_overshoot'))
UNITS = {name:'s' if name in HISTOGRAMS else '1' for name in NAMES.values()}
LABELS = frozenset({'environment','operation','status'})

def registry(config):
    return {'environment':frozenset({config.otel_environment}),
        'operation':frozenset(c.value for c in Category), 'status':frozenset(s.value for s in RunStatus)}

def views():
    return [View(instrument_name=n,meter_name=SCOPE,attribute_keys=LABELS) for n in UNITS]

def register(runtime):
    meter=runtime.meter_provider.get_meter(SCOPE)
    runtime.reliability_instruments={n:(meter.create_histogram(n,unit='s') if n in HISTOGRAMS
        else meter.create_counter(n,unit='1')) for n in UNITS}

def record(kind, category, value=1, status='RUNNING', runtime=None):
    from .deadlines import current_controller
    owner=current_controller()
    if runtime is not None:
        pass
    elif owner is not None and owner.turn is not None:
        runtime=owner.turn.runtime
    else:
        from .agent_instrumentation import runtime_for_execution
        runtime=runtime_for_execution()
    if runtime is None or not runtime.enabled or runtime.closed:
        return
    name=NAMES[kind]
    labels=validate_metric_labels({'environment':runtime.config.otel_environment,
        'operation':category.value,'status':status},value_registry=registry(runtime.config))
    instrument=runtime.reliability_instruments[name]
    (instrument.record if name in HISTOGRAMS else instrument.add)(value,labels)

def safe_point(metric, point, config):
    if metric.unit != UNITS[metric.name] or set(point.attributes) != LABELS:
        raise ValueError('Invalid reliability metric')
    labels=validate_metric_labels(dict(point.attributes),value_registry=registry(config))
    from opentelemetry.sdk.metrics.export import Sum, Histogram
    if metric.name in HISTOGRAMS:
        if not isinstance(metric.data, Histogram) or type(point.count) is not int or point.count < 0 or not math.isfinite(point.sum) or point.sum < 0 or sum(point.bucket_counts) != point.count:
            raise ValueError('Invalid reliability histogram')
        if any(not math.isfinite(b) for b in point.explicit_bounds) or tuple(sorted(set(point.explicit_bounds))) != tuple(point.explicit_bounds):
            raise ValueError('Invalid reliability bounds')
    elif not isinstance(metric.data, Sum) or not metric.data.is_monotonic or type(point.value) not in (int,float) or not math.isfinite(point.value) or point.value < 0:
        raise ValueError('Invalid reliability counter')
    return replace(point,attributes=labels,exemplars=[])
