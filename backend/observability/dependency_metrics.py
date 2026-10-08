"""Finite unsampled dependency aggregates, distinct from tool/model accounting."""
from dataclasses import replace
import math
from weakref import WeakKeyDictionary
from threading import RLock
from opentelemetry.metrics import Observation
from opentelemetry.sdk.metrics.view import View, ExplicitBucketHistogramAggregation, DefaultAggregation
from .slo_contract import DURATION_BOUNDS
from .dependency_contract import DEPENDENCIES, OPERATIONS, DB_OWNERS
from .attributes import validate_metric_labels
from .tracing import SCOPE

COUNTERS = ('slopanoc.dependency.requests', 'slopanoc.dependency.failures',
    'slopanoc.dependency.timeouts', 'slopanoc.dependency.rate_limits', 'slopanoc.dependency.retries',
    'slopanoc.dependency.pages', 'slopanoc.db.pool.exhaustion', 'slopanoc.knowledge.no_result',
    'slopanoc.knowledge.fallback')
BYTE_COUNTERS = ('slopanoc.storage.bytes_uploaded', 'slopanoc.storage.bytes_downloaded')
HISTOGRAMS = ('slopanoc.dependency.duration', 'slopanoc.db.connection_acquire_duration', 'slopanoc.knowledge.stage.duration')
GAUGES = tuple('slopanoc.db.pool.' + n for n in ('size', 'checked_out', 'overflow', 'checked_in', 'connections'))
UNITS = dict.fromkeys(COUNTERS, '1') | dict.fromkeys(BYTE_COUNTERS, 'By') | dict.fromkeys(HISTOGRAMS, 's') | dict.fromkeys(GAUGES, '1')
LABELS = frozenset({'environment', 'dependency', 'operation', 'status'})
POOL_LABELS = frozenset({'environment', 'dependency'})
_pools = WeakKeyDictionary()
_lock = RLock()


def registry(config):
    return {'environment': frozenset({config.otel_environment}), 'dependency': DEPENDENCIES,
        'operation': OPERATIONS, 'status': frozenset({'COMPLETED', 'FAILED', 'TIMEOUT', 'CANCELLED'})}


def views():
    return [View(instrument_name=n, meter_name=SCOPE, aggregation=ExplicitBucketHistogramAggregation(DURATION_BOUNDS) if n in HISTOGRAMS else DefaultAggregation(), attribute_keys=POOL_LABELS if n in GAUGES else LABELS) for n in UNITS]


def pool_registered(pool, owner):
    with _lock:
        _pools[pool] = [owner, 0]


def pool_connection(pool, delta):
    with _lock:
        if pool in _pools:
            _pools[pool][1] = max(0, _pools[pool][1] + delta)


def pool_points(config, name):
    with _lock:
        pools = list(_pools.items())
    totals = {}
    for pool, (owner, connections) in pools:
        try:
            field = name.rsplit('.', 1)[-1]
            method = {'size': 'size', 'checked_out': 'checkedout', 'overflow': 'overflow', 'checked_in': 'checkedin'}.get(field)
            value = connections if field == 'connections' else getattr(pool, method)()
            if type(value) is not int:
                continue
            totals[owner] = totals.get(owner, 0) + value
        except (AttributeError, TypeError):
            continue  # unsupported StaticPool state is absent, never invented
    for owner, value in totals.items():
        yield Observation(value, {"environment": config.otel_environment, "dependency": owner})


def register(runtime):
    meter = runtime.meter_provider.get_meter(SCOPE)
    runtime.dependency_instruments = {n: meter.create_counter(n, unit=UNITS[n]) for n in COUNTERS + BYTE_COUNTERS}
    runtime.dependency_instruments.update({n: meter.create_histogram(n, unit='s') for n in HISTOGRAMS})
    for name in GAUGES:
        def observe(options, name=name):
            return list(pool_points(runtime.config, name))
        meter.create_observable_gauge(name, callbacks=[observe], unit='1')


def record(scope, duration_ms):
    r = scope.runtime
    if r is None or r.closed or not hasattr(r, 'dependency_instruments'):
        return
    labels = validate_metric_labels({'environment': r.config.otel_environment, 'dependency': scope.dependency,
        'operation': scope.operation, 'status': scope.status.value}, value_registry=registry(r.config))
    instruments = r.dependency_instruments
    if scope.kind == 'local':
        instruments['slopanoc.knowledge.stage.duration'].record(duration_ms / 1000, labels)
        return
    if scope.kind == 'acquisition':
        instruments['slopanoc.db.connection_acquire_duration'].record(duration_ms / 1000, labels)
        if scope.attrs.get('slopanoc.failure_kind') == 'pool_timeout':
            instruments['slopanoc.db.pool.exhaustion'].add(1, labels)
        return
    if scope.kind == 'transaction':
        return  # lifecycle groups do not inflate external request denominator
    instruments['slopanoc.dependency.requests'].add(1, labels)
    instruments['slopanoc.dependency.duration'].record(duration_ms / 1000, labels)
    if scope.status.value in {'FAILED', 'TIMEOUT'}:
        instruments['slopanoc.dependency.failures'].add(1, labels)
    if scope.status.value == 'TIMEOUT':
        instruments['slopanoc.dependency.timeouts'].add(1, labels)
    if scope.attrs.get('slopanoc.rate_limited'):
        instruments['slopanoc.dependency.rate_limits'].add(1, labels)
    if scope.dependency == 'power_automate_gateway' and scope.operation == 'teams.getMessages':
        instruments['slopanoc.dependency.pages'].add(1, labels)
    size = scope.attrs.get('slopanoc.bytes')
    if type(size) is int and 0 <= size <= 2**63 - 1 and scope.operation in {'upload', 'download'}:
        instruments['slopanoc.storage.bytes_' + ('uploaded' if scope.operation == 'upload' else 'downloaded')].add(size, labels)


def outcome(scope, name, count=1):
    r = scope.runtime
    if r and not r.closed and hasattr(r, 'dependency_instruments') and name in COUNTERS:
        labels = validate_metric_labels({'environment': r.config.otel_environment, 'dependency': scope.dependency,
            'operation': scope.operation, 'status': 'COMPLETED'}, value_registry=registry(r.config))
        if type(count) is int and 0 <= count <= 2**63 - 1:
            r.dependency_instruments[name].add(count, labels)


def safe_point(metric, point, config):
    from opentelemetry.sdk.metrics.export import Sum, Gauge, Histogram
    dimensions = POOL_LABELS if metric.name in GAUGES else LABELS
    if set(point.attributes) != dimensions:
        raise ValueError('Dependency dimensions')
    labels = validate_metric_labels(dict(point.attributes), value_registry=registry(config))
    if metric.name in GAUGES:
        if not isinstance(metric.data, Gauge) or type(point.value) is not int or (point.value < 0 and metric.name != 'slopanoc.db.pool.overflow'):
            raise ValueError('Pool gauge')
    elif metric.name in COUNTERS + BYTE_COUNTERS:
        if not isinstance(metric.data, Sum) or not metric.data.is_monotonic or type(point.value) not in (int, float) or not math.isfinite(point.value) or point.value < 0:
            raise ValueError('Dependency counter')
    else:
        if not isinstance(metric.data, Histogram) or point.count < 0 or not math.isfinite(point.sum) or point.sum < 0 or sum(point.bucket_counts) != point.count:
            raise ValueError('Dependency histogram')
        if any(not math.isfinite(b) for b in point.explicit_bounds) or tuple(sorted(set(point.explicit_bounds))) != tuple(point.explicit_bounds):
            raise ValueError('Dependency bounds')
    return replace(point, attributes=labels, exemplars=[])
