"""Unsampled SRE aggregates; only registered names/keys/values survive export."""
from dataclasses import replace
import math
import time
from threading import Lock
from opentelemetry.metrics import Observation
from opentelemetry.sdk.metrics.view import View, ExplicitBucketHistogramAggregation
from opentelemetry.sdk.metrics.export import Gauge, Sum, Histogram
from .tracing import SCOPE
from .attributes import validate_metric_labels
from .slo_contract import DEFINITIONS, RequestClass, State, BURN_WINDOWS, WINDOW_SECONDS, DURATION_BOUNDS, SAFETY_KINDS

COUNTERS=('slopanoc.turn.accepted','slopanoc.safety.violations')
HISTOGRAMS=('slopanoc.turn.duration',)
GAUGES=('slopanoc.turn.active','slopanoc.slo.bad_fraction','slopanoc.slo.eligible','slopanoc.slo.unknown','slopanoc.slo.budget_remaining','slopanoc.slo.source_age','slopanoc.persistence.pending','slopanoc.persistence.failed')
UNITS=dict.fromkeys(COUNTERS+GAUGES,'1')|{'slopanoc.turn.duration':'s','slopanoc.slo.source_age':'s'}
LABELS=frozenset({'environment','operation','status','window'})
_lock=Lock()


def registry(config):
    return {'environment':frozenset({config.otel_environment}),
        'operation':frozenset(DEFINITIONS)|frozenset(c.value for c in RequestClass)|SAFETY_KINDS|{'accepted','projection'},
        'status':frozenset(s.value for s in State)|{'RUNNING','COMPLETED','FAILED','TIMEOUT','CANCELLED','CONFIRMED'},
        'window':frozenset(str(w) for w in (*BURN_WINDOWS,WINDOW_SECONDS))|{'event'}}


def views():
    return [View(instrument_name=name,meter_name=SCOPE,attribute_keys=LABELS,
        **({'aggregation':ExplicitBucketHistogramAggregation(DURATION_BOUNDS)} if name in HISTOGRAMS else {})) for name in UNITS]


def register(runtime):
    meter=runtime.meter_provider.get_meter(SCOPE)
    runtime.slo_instruments={name:meter.create_counter(name,unit=UNITS[name]) for name in COUNTERS}
    runtime.slo_instruments.update({name:meter.create_histogram(name,unit='s') for name in HISTOGRAMS})
    runtime.slo_points={}
    for name in GAUGES:
        def observe(options,name=name):
            if name=='slopanoc.turn.active':
                from .active_runs import active_runs
                attrs=dict(environment=runtime.config.otel_environment,operation='accepted',status='RUNNING',window='event')
                yield Observation(len(active_runs),attrs)
                return
            with _lock: values=tuple(runtime.slo_points.get(name,{}).values())
            for value,attrs,recorded in values:
                if name=="slopanoc.slo.source_age": value+=max(0,time.monotonic()-recorded)
                yield Observation(value,attrs)
        meter.create_observable_gauge(name,callbacks=[observe],unit=UNITS[name])


def record(runtime,name,value,operation,status,window='event'):
    if runtime is None or not getattr(runtime,'enabled',False) or not hasattr(runtime,'slo_instruments'):return
    attrs=validate_metric_labels(dict(environment=runtime.config.otel_environment,operation=operation,status=status,window=window),value_registry=registry(runtime.config))
    if name in GAUGES:
        if not math.isfinite(value):return
        with _lock:runtime.slo_points.setdefault(name,{})[(operation,window)]=(value,attrs,time.monotonic())
    else:
        instrument=runtime.slo_instruments[name]
        (instrument.record if name in HISTOGRAMS else instrument.add)(value,attrs)


def publish_result(runtime,result):
    for window in result['burn_windows']:
        seconds=str(window['seconds']);n=window['eligible']
        if n:record(runtime,'slopanoc.slo.bad_fraction',window['bad']/n,result['slo_id'],result['state'],seconds)
        record(runtime,'slopanoc.slo.eligible',n,result['slo_id'],result['state'],seconds)
        record(runtime,'slopanoc.slo.unknown',window['unknown'],result['slo_id'],result['state'],seconds)
    remaining=result['remaining_fraction']
    if remaining is not None:record(runtime,'slopanoc.slo.budget_remaining',remaining,result['slo_id'],result['state'],str(WINDOW_SECONDS))
    if result['source_last_updated'] is not None:
        record(runtime,'slopanoc.slo.source_age',max(0,(result['evaluated_at']-result['source_last_updated']).total_seconds()),result['slo_id'],result['state'],str(WINDOW_SECONDS))


def safe_point(metric,point,config):
    if metric.unit!=UNITS[metric.name] or set(point.attributes)!=LABELS:raise ValueError('SRE metric dimensions/unit')
    attrs=validate_metric_labels(dict(point.attributes),value_registry=registry(config))
    if metric.name in HISTOGRAMS:
        if not isinstance(metric.data,Histogram) or point.count<0 or not math.isfinite(point.sum) or point.sum<0 or sum(point.bucket_counts)!=point.count or tuple(point.explicit_bounds)!=DURATION_BOUNDS:raise ValueError('SRE histogram shape')
    else:
        if not isinstance(metric.data,Sum if metric.name in COUNTERS else Gauge):raise ValueError('SRE instrument shape')
        if not math.isfinite(point.value) or point.value<0 and metric.name!='slopanoc.slo.budget_remaining':raise ValueError('Invalid SRE value')
        if metric.name in COUNTERS and not metric.data.is_monotonic:raise ValueError('SRE counter must be monotonic')
    return replace(point,attributes=attrs,exemplars=[])
