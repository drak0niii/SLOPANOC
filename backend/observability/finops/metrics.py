"""Finite accounting pipeline instruments with existing SRE-safe dimensions."""
from dataclasses import replace
import math
from opentelemetry.sdk.metrics.view import View, ExplicitBucketHistogramAggregation
from opentelemetry.sdk.metrics.export import Sum, Gauge, Histogram
from opentelemetry.metrics import Observation
from ..tracing import SCOPE
from ..slo_metrics import LABELS, registry
from ..slo_contract import DURATION_BOUNDS
from ..attributes import validate_metric_labels
COUNTERS=('admissions_attempted','admissions_failed','provider_started','observations','inbox_accepted',
          'ledger_persisted','duplicates','conflicts','replayed','persistence_failures','metadata_missing')
GAUGES=('pending','debt')
HISTOGRAMS=('capture_duration','admission_duration')
UNITS={'slopanoc.accounting.'+n:'s' if n in HISTOGRAMS else '1' for n in COUNTERS+GAUGES+HISTOGRAMS}

def views():
    return [View(instrument_name=name,meter_name=SCOPE,attribute_keys=LABELS,
        **({'aggregation':ExplicitBucketHistogramAggregation(DURATION_BOUNDS)} if name.rsplit('.',1)[-1] in HISTOGRAMS else {})) for name in UNITS]

def register(runtime):
    meter=runtime.meter_provider.get_meter(SCOPE)
    runtime.accounting_instruments={n:meter.create_counter('slopanoc.accounting.'+n,unit='1') for n in COUNTERS}
    runtime.accounting_instruments.update({n:meter.create_histogram('slopanoc.accounting.'+n,unit='s') for n in HISTOGRAMS})
    runtime.accounting_points={}
    for name in GAUGES:
        def observe(options,name=name):
            value=runtime.accounting_points.get(name)
            if value is not None:yield Observation(value,labels(runtime.config))
        meter.create_observable_gauge('slopanoc.accounting.'+name,callbacks=[observe],unit='1')

def labels(config):
    return validate_metric_labels(dict(environment=config.otel_environment,operation='cost_ledger_completeness',status='RUNNING',window='event'),value_registry=registry(config))

def record(runtime,name,value):
    if runtime is None or not runtime.enabled or not hasattr(runtime,'accounting_instruments'):return
    if name not in COUNTERS+GAUGES+HISTOGRAMS or not math.isfinite(value) or value<0:raise ValueError('Invalid accounting metric')
    if name in GAUGES:runtime.accounting_points[name]=value
    else:
        instrument=runtime.accounting_instruments[name]
        (instrument.record if name in HISTOGRAMS else instrument.add)(value,labels(runtime.config))

def safe_point(metric,point,config):
    name=metric.name.rsplit('.',1)[-1]
    if set(point.attributes)!=LABELS or dict(point.attributes)!=labels(config) or metric.unit!=UNITS[metric.name]:raise ValueError('Accounting dimensions')
    if name in HISTOGRAMS:
        if not isinstance(metric.data,Histogram) or tuple(point.explicit_bounds)!=DURATION_BOUNDS or point.count<0 or not math.isfinite(point.sum) or point.sum<0 or sum(point.bucket_counts)!=point.count:raise ValueError('Accounting histogram')
    elif not isinstance(metric.data,Sum if name in COUNTERS else Gauge) or not math.isfinite(point.value) or point.value<0:raise ValueError('Accounting value')
    elif name in COUNTERS and not metric.data.is_monotonic:raise ValueError('Accounting counter')
    return replace(point,attributes=labels(config),exemplars=[])
