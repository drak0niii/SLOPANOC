"""Bounded model instruments; no prices/accounting, no identifier dimensions."""
from dataclasses import replace
import math
from opentelemetry.sdk.metrics.export import HistogramDataPoint, NumberDataPoint, Histogram, Sum
from .model_context import ModelAgent, ModelPurpose
from .attributes import validate_metric_labels
from .tracing import SCOPE

LABELS = frozenset({'environment','provider','model','agent','operation','status'})
COUNTERS = ('slopanoc.model.requests', 'slopanoc.model.retries', 'slopanoc.model.failures',
    'slopanoc.model.timeouts', 'slopanoc.model.usage_unavailable')
HISTOGRAMS = {'gen_ai.client.operation.duration':'s', 'slopanoc.model.ttft':'s',
    'slopanoc.model.input_tokens':'{token}', 'slopanoc.model.output_tokens':'{token}',
    'slopanoc.model.cached_tokens':'{token}', 'slopanoc.model.candidate_tokens':'{token}'}
UNITS = dict.fromkeys(COUNTERS, '1') | HISTOGRAMS


def registry(config):
    # Fixed registered model names; unlisted configurations map to other.
    return {'environment':frozenset({config.otel_environment}),
        'provider':frozenset({'gcp.vertex_ai','gcp.gemini','other'}),
        'model':frozenset({'gemini-2.5-flash','gemini-2.5-pro','gemini-3-pro-preview',
            'gemini-3-flash-preview','text-embedding-005','other'}),
        'agent':frozenset(a.value for a in ModelAgent),
        'operation':frozenset(a.value for a in ModelPurpose),
        'status':frozenset({'COMPLETED','FAILED','TIMEOUT','CANCELLED'})}


def register(runtime):
    meter = runtime.meter_provider.get_meter(SCOPE)
    runtime.model_metric_registry = registry(runtime.config)
    runtime.model_instruments = {name:meter.create_counter(name, unit='1') for name in COUNTERS}
    runtime.model_instruments.update({name:meter.create_histogram(name,unit=unit) for name,unit in HISTOGRAMS.items()})


def record(runtime, observation):
    labels = {'environment':runtime.config.otel_environment,'provider':observation.provider,
        'model':observation.model if observation.model in runtime.model_metric_registry['model'] else 'other',
        'agent':observation.agent.value,'operation':observation.operation.value,'status':observation.status.value}
    labels = validate_metric_labels(labels, value_registry=runtime.model_metric_registry)
    instruments = runtime.model_instruments
    def add(name, value):
        instruments[name].add(value,labels)
    def sample(name, value):
        if value is not None:
            instruments[name].record(value,labels)
    add('slopanoc.model.requests',1)
    if observation.attempt > 1:
        add('slopanoc.model.retries',1)
    if observation.status.value in ('FAILED','TIMEOUT'):
        add('slopanoc.model.failures',1)
    if observation.status.value == 'TIMEOUT':
        add('slopanoc.model.timeouts',1)
    if observation.usage.availability != 'KNOWN':
        add('slopanoc.model.usage_unavailable',1)
    sample('gen_ai.client.operation.duration', observation.duration_ms/1000)
    sample('slopanoc.model.ttft', observation.ttft_ms/1000 if observation.ttft_ms is not None else None)
    for key in ('input_tokens','output_tokens','cached_tokens','candidate_tokens'):
        sample('slopanoc.model.'+key, getattr(observation.usage,key))


def safe_point(metric, point, config):
    if set(point.attributes) != LABELS:
        raise ValueError('Model metric dimensions')
    labels = validate_metric_labels(dict(point.attributes),value_registry=registry(config))
    if metric.name in COUNTERS:
        if not isinstance(metric.data,Sum) or not metric.data.is_monotonic or not isinstance(point,NumberDataPoint):
            raise ValueError('Model counter shape')
        if type(point.value) not in (int,float) or not math.isfinite(point.value) or point.value < 0:
            raise ValueError('Invalid counter')
    else:
        if not isinstance(metric.data,Histogram) or not isinstance(point,HistogramDataPoint):
            raise ValueError('Model histogram shape')
        if type(point.count) is not int or point.count < 0 or not math.isfinite(point.sum) or point.sum < 0:
            raise ValueError('Invalid histogram')
        if len(point.bucket_counts) != len(point.explicit_bounds)+1 or sum(point.bucket_counts) != point.count:
            raise ValueError('Invalid histogram buckets')
        if any(type(n) is not int or n < 0 for n in point.bucket_counts):
            raise ValueError('Invalid bucket counts')
        if any(not math.isfinite(v) or v < 0 for v in point.explicit_bounds) or list(point.explicit_bounds)!=sorted(set(point.explicit_bounds)):
            raise ValueError('Invalid histogram boundaries')
        if any(v is not None and (not math.isfinite(v) or v < 0) for v in (point.min,point.max)):
            raise ValueError('Invalid histogram extrema')
    return replace(point, attributes=labels, exemplars=[])
