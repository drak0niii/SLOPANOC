"""Fixed-cardinality foundation health; never a run registry or SLO engine."""
from dataclasses import replace
import math
from datetime import datetime, timezone
import json
import sys
import time
from threading import Lock, Thread
from opentelemetry.metrics import Observation
from opentelemetry.sdk.metrics.export import MetricExporter, MetricExportResult
from opentelemetry.sdk.metrics.view import View, DropAggregation, ExplicitBucketHistogramAggregation, DefaultAggregation
from .attributes import validate_metric_labels
from .errors import ErrorCode
from .schemas import OperationalEvent, TELEMETRY_SCHEMA_VERSION
from .tracing import SCOPE, SCHEMA_URL, safe_scope

SIGNALS = ('trace', 'metric', 'log')
HEALTH_FIELDS = ('accepted', 'attempts', 'exported', 'failed', 'dropped', 'filtered',
                 'queue_size', 'export_duration_ms', 'shutdown_failed')
INSTRUMENTS = tuple('slopanoc.telemetry.'+field for field in HEALTH_FIELDS)


class Health:
    def __init__(self, config=None):
        self.config = config
        self._lock = Lock()
        self._values = {(signal, field):0 for signal in SIGNALS for field in HEALTH_FIELDS}
        self.initialized = False
        self.shutdown_complete = False
        self._last_fallback = 0
        self._fallback_pending = False

    def add(self, signal, field, amount=1):
        with self._lock:
            self._values[signal, field] += amount
        if field in {'failed', 'shutdown_failed'}:
            self._fallback()

    def set(self, signal, field, value):
        with self._lock:
            self._values[signal, field] = value

    def snapshot(self):
        with self._lock:
            return {signal:{field:self._values[signal,field] for field in HEALTH_FIELDS} for signal in SIGNALS}

    def _fallback(self):
        # Local visibility still works if metric delivery is down. Never log via
        # the failing exporter, nor include exception/endpoint text.
        with self._lock:
            now = time.monotonic()
            if self._fallback_pending or now-self._last_fallback < 30:
                return
            self._last_fallback = now
            self._fallback_pending = True
        Thread(target=self._write_fallback, name='slopanoc-telemetry-fallback',daemon=True).start()

    def _write_fallback(self):
        try:
            sys.stderr.write(json.dumps({'timestamp':datetime.now(timezone.utc).isoformat(),
                'service':self.config.otel_service_name if self.config else 'slopanoc',
                'environment':self.config.otel_environment if self.config else 'development',
                'message':OperationalEvent.DEGRADED.value, 'event_name':OperationalEvent.DEGRADED.value,
                'severity':'WARNING','error_code':ErrorCode.TELEMETRY_DROPPED.value,
                'trace_id':None,'span_id':None,'run_id':None,'metadata':{},
                'telemetry_schema_version':TELEMETRY_SCHEMA_VERSION})+'\n')
        except Exception:
            pass
        finally:
            with self._lock:
                self._fallback_pending = False


def health_views():
    # Matching specific views coexist with drop-all, so only these instruments
    # from this scope survive. Foreign scopes/names produce no export record.
    return [View(instrument_name='*', aggregation=DropAggregation())] + [
        View(instrument_name=name, meter_name=SCOPE,
             attribute_keys={'environment', 'operation'}) for name in INSTRUMENTS] + model_views() + execution_views() + dependency_views() + reliability_views() + slo_views()


def slo_views():
    from .slo_metrics import views
    return views()


def reliability_views():
    from .reliability_metrics import views
    return views()


def dependency_views():
    from .dependency_metrics import views
    return views()


def execution_views():
    from .execution_metrics import views
    return views()


def model_views():
    from .model_metrics import UNITS, LABELS
    from .slo_contract import DURATION_BOUNDS
    return [View(instrument_name=name, meter_name=SCOPE, attribute_keys=LABELS, aggregation=ExplicitBucketHistogramAggregation(DURATION_BOUNDS) if UNITS[name]=='s' else DefaultAggregation()) for name in UNITS]


def register_health(provider, config, health):
    meter = provider.get_meter(SCOPE)
    registry = {'environment':frozenset({config.otel_environment}),
                'operation':frozenset(signal+'_export' for signal in SIGNALS)}
    for field in HEALTH_FIELDS:
        def observe(options, field=field):
            snapshot = health.snapshot()
            return [Observation(snapshot[signal][field], validate_metric_labels(
                {'environment':config.otel_environment, 'operation':signal+'_export'},
                value_registry=registry)) for signal in SIGNALS]
        meter.create_observable_gauge('slopanoc.telemetry.'+field, callbacks=[observe])


class HealthMetricExporter(MetricExporter):
    def __init__(self, delegate, health, config):
        super().__init__(preferred_temporality=delegate._preferred_temporality,
                         preferred_aggregation=delegate._preferred_aggregation)
        self.delegate, self.health, self.config = delegate, health, config

    def export(self, metrics_data, timeout_millis=10000, **kwargs):
        # Defense-in-depth immediately before the encoder; strip exemplars'
        # arbitrary filtered attributes. M1 health gauges need no exemplars.
        from .model_metrics import UNITS, safe_point
        from .execution_metrics import UNITS as EXEC_UNITS, safe_point as safe_execution_point
        from .dependency_metrics import UNITS as DEP_UNITS, safe_point as safe_dependency_point
        from .reliability_metrics import UNITS as REL_UNITS, safe_point as safe_reliability_point
        from .slo_metrics import UNITS as SRE_UNITS, safe_point as safe_sre_point
        resources = []
        registry = {'environment':frozenset({self.config.otel_environment}),
                    'operation':frozenset(signal+'_export' for signal in SIGNALS)}
        for resource in metrics_data.resource_metrics:
            scopes = []
            for scope in resource.scope_metrics:
                if scope.scope.name != SCOPE:
                    continue
                instruments = []
                for metric in scope.metrics:
                    if metric.name not in INSTRUMENTS and metric.name not in UNITS and metric.name not in EXEC_UNITS and metric.name not in DEP_UNITS and metric.name not in REL_UNITS and metric.name not in SRE_UNITS:
                        continue
                    points = []
                    for point in metric.data.data_points:
                        try:
                            if metric.name in SRE_UNITS:
                                points.append(safe_sre_point(metric, point, self.config))
                                continue
                            if metric.name in REL_UNITS:
                                points.append(safe_reliability_point(metric, point, self.config))
                                continue
                            if metric.name in DEP_UNITS:
                                points.append(safe_dependency_point(metric, point, self.config))
                                continue
                            if metric.name in EXEC_UNITS:
                                points.append(safe_execution_point(metric, point, self.config))
                                continue
                            if metric.name in UNITS:
                                points.append(safe_point(metric, point, self.config))
                                continue
                            if set(point.attributes) != {'environment','operation'}:
                                raise ValueError('Missing registered health dimensions')
                            labels = validate_metric_labels(dict(point.attributes), value_registry=registry)
                            if not math.isfinite(point.value) or point.value < 0:
                                raise ValueError('Invalid health value')
                        except (ValueError, TypeError, AttributeError):
                            self.health.add('metric','filtered')
                            continue
                        points.append(replace(point, attributes=labels, exemplars=[]))
                    if points:
                        instruments.append(replace(metric, description=metric.name,
                            unit=(UNITS | EXEC_UNITS | DEP_UNITS | REL_UNITS | SRE_UNITS).get(metric.name, 'ms' if metric.name.endswith('export_duration_ms') else '1'),
                            data=replace(metric.data, data_points=points)))
                if instruments:
                    scopes.append(replace(scope, metrics=instruments, scope=safe_scope(), schema_url=SCHEMA_URL))
            if scopes:
                resources.append(replace(resource, scope_metrics=scopes))
        metrics_data = replace(metrics_data, resource_metrics=resources)
        started = time.monotonic()
        self.health.add('metric', 'attempts')
        try:
            result = self.delegate.export(metrics_data, timeout_millis=timeout_millis, **kwargs)
        except Exception:
            result = MetricExportResult.FAILURE
        self.health.add('metric', 'exported' if result == MetricExportResult.SUCCESS else 'failed')
        if result != MetricExportResult.SUCCESS:
            self.health.add('metric', 'dropped')
        self.health.set('metric', 'export_duration_ms', (time.monotonic()-started)*1000)
        return result

    def force_flush(self, timeout_millis=10000):
        return True

    def shutdown(self, timeout_millis=30000, **kwargs):
        try:
            self.delegate.shutdown(timeout_millis=timeout_millis, **kwargs)
        except Exception:
            self.health.add('metric', 'shutdown_failed')
