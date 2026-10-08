"""Finite semantic agent/tool aggregate metrics; never sampled by tracing."""
from dataclasses import replace
from opentelemetry.sdk.metrics.view import View, ExplicitBucketHistogramAggregation, DefaultAggregation
from .slo_contract import DURATION_BOUNDS
from .tracing import SCOPE
from .model_context import ModelAgent
from .attributes import validate_metric_labels
from .tool_instrumentation import TOOLS, CATEGORIES
from .model_metrics import safe_point as validate_point

AGENT_LABELS = frozenset({'environment','agent','status'})
TOOL_LABELS = AGENT_LABELS | {'tool','tool_category'}
COUNTERS = ('slopanoc.agent.executions','slopanoc.agent.failures', 'slopanoc.tool.invocations',
    'slopanoc.tool.failures','slopanoc.tool.timeouts')
UNITS = dict.fromkeys(COUNTERS,'1') | {'slopanoc.agent.duration':'s','slopanoc.tool.duration':'s'}


def registry(config):
    return {'environment':frozenset({config.otel_environment}),
        'agent':frozenset(v.value for v in ModelAgent),
        'status':frozenset({'COMPLETED','FAILED','TIMEOUT','CANCELLED'}),
        'tool':frozenset(TOOLS) | {'other'}, 'tool_category':CATEGORIES}


def views():
    return [View(instrument_name=name, meter_name=SCOPE,
        aggregation=ExplicitBucketHistogramAggregation(DURATION_BOUNDS) if UNITS[name]=='s' else DefaultAggregation(), attribute_keys=AGENT_LABELS if '.agent.' in name else TOOL_LABELS) for name in UNITS]


def register(runtime):
    meter = runtime.meter_provider.get_meter(SCOPE)
    runtime.execution_instruments = {name:meter.create_counter(name,unit='1') for name in COUNTERS}
    runtime.execution_instruments.update({name:meter.create_histogram(name,unit=unit)
        for name,unit in UNITS.items() if name not in COUNTERS})


def record(scope, duration_ms):
    runtime = scope.runtime
    if runtime is None or runtime.closed or not hasattr(runtime,'execution_instruments'):
        return
    labels = {'environment':runtime.config.otel_environment, 'agent':scope.agent.value,'status':scope.status.value}
    if scope.kind == 'tool':
        labels.update(tool=scope.tool, tool_category=scope.category)
    labels = validate_metric_labels(labels, value_registry=registry(runtime.config))
    instruments = runtime.execution_instruments
    prefix = 'slopanoc.'+scope.kind
    instruments[prefix+('.executions' if scope.kind=='agent' else '.invocations')].add(1,labels)
    instruments[prefix+'.duration'].record(duration_ms/1000,labels)
    if scope.status.value in ('FAILED','TIMEOUT'):
        instruments[prefix+'.failures'].add(1,labels)
    if scope.kind=='tool' and scope.status.value=='TIMEOUT':
        instruments['slopanoc.tool.timeouts'].add(1,labels)


def safe_point(metric, point, config):
    dimensions = AGENT_LABELS if '.agent.' in metric.name else TOOL_LABELS
    if set(point.attributes) != dimensions:
        raise ValueError('Execution dimensions')
    labels = validate_metric_labels(dict(point.attributes),value_registry=registry(config))
    # Reuse the proven M3 histogram/counter numeric validation without its labels.
    proxy_attrs = {'environment':config.otel_environment,'provider':'other','model':'other',
        'agent':'system','operation':'orchestration','status':'COMPLETED'}
    proxy_name = 'slopanoc.model.requests' if metric.name in COUNTERS else 'gen_ai.client.operation.duration'
    validate_point(replace(metric,name=proxy_name), replace(point,attributes=proxy_attrs),config)
    return replace(point, attributes=labels, exemplars=[])
