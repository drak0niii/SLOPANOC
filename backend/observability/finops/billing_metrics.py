"""Finite source-specific instrument names; same protected projection dimensions."""
from collections import Counter
from dataclasses import replace
import math
from opentelemetry.sdk.metrics.view import View,ExplicitBucketHistogramAggregation
from opentelemetry.sdk.metrics.export import Sum,Histogram,Gauge
from opentelemetry.metrics import Observation
from ..slo_contract import DURATION_BOUNDS
from ..slo_metrics import LABELS,registry
from ..attributes import validate_metric_labels
from ..tracing import SCOPE

COUNTERS=('extractions','publications','failures','rows','identical_generations','late_rows','correction_rows',
          'unmapped_rows','schema_incompatible','stale_reads','bytes_processed')
GAUGES=('source_age','unmapped_ratio')
PREFIX={'DETAILED_BILLING':'billing','PRICING_EXPORT':'pricing','FOCUS':'focus'}
UNITS={'slopanoc.finops.'+source+'.'+name:('s' if name in ('ingestion_duration','source_age') else '1')
       for source in PREFIX.values() for name in COUNTERS+GAUGES+('ingestion_duration',)}

def labels(config):
    return validate_metric_labels(dict(environment=config.otel_environment,operation='projection',status='RUNNING',window='event'),value_registry=registry(config))

def views():
    return [View(instrument_name=n,meter_name=SCOPE,attribute_keys=LABELS,
        **({'aggregation':ExplicitBucketHistogramAggregation(DURATION_BOUNDS)} if n.endswith('.ingestion_duration') else {})) for n,unit in UNITS.items()]

def register(runtime):
    meter=runtime.meter_provider.get_meter(SCOPE)
    runtime.billing_instruments={n:(meter.create_histogram(n,unit='s') if unit=='s' else meter.create_counter(n,unit='1')) for n,unit in UNITS.items() if n.rsplit('.',1)[-1] not in GAUGES}
    runtime.billing_gauges={}
    for n in UNITS:
        if n.rsplit('.',1)[-1] in GAUGES:
            def observe(options,n=n):
                value=runtime.billing_gauges.get(n)
                return [] if value is None else [Observation(value,labels(runtime.config))]
            meter.create_observable_gauge(n,callbacks=[observe],unit=UNITS[n])

class Metrics:
    def __init__(self,runtime,environment):self.runtime=runtime;self.environment=environment;self.counts=Counter()
    def add(self,name,source,value=1):
        if source not in PREFIX or name not in COUNTERS or not math.isfinite(value) or value<0:raise ValueError('Invalid source metric')
        self.counts[source,name]+=value
        self._record(name,source,value)
    def gauge(self,name,source,value):
        if name not in GAUGES or source not in PREFIX or not math.isfinite(value) or value<0 or name=='unmapped_ratio' and value>1:raise ValueError('Invalid source gauge')
        if self.runtime and self.runtime.enabled:self.runtime.billing_gauges['slopanoc.finops.'+PREFIX[source]+'.'+name]=value
    def duration(self,source,value):self._record('ingestion_duration',source,value)
    def _record(self,name,source,value):
        if source not in PREFIX or not math.isfinite(value) or value<0:raise ValueError('Invalid source metric')
        runtime=self.runtime
        if runtime is None or not runtime.enabled or not hasattr(runtime,'billing_instruments'):return
        instrument=runtime.billing_instruments['slopanoc.finops.'+PREFIX[source]+'.'+name]
        (instrument.record if name=='ingestion_duration' else instrument.add)(value,labels(runtime.config))

def safe_point(metric,point,config):
    if set(point.attributes)!=LABELS or dict(point.attributes)!=labels(config) or metric.unit!=UNITS[metric.name]:raise ValueError('Financial dimensions')
    if metric.name.endswith('.ingestion_duration'):
        if not isinstance(metric.data,Histogram) or tuple(point.explicit_bounds)!=DURATION_BOUNDS or point.count<0 or not math.isfinite(point.sum) or point.sum<0 or sum(point.bucket_counts)!=point.count:raise ValueError('Financial histogram')
    elif metric.name.rsplit('.',1)[-1] in GAUGES:
        if not isinstance(metric.data,Gauge) or not math.isfinite(point.value) or point.value<0 or metric.name.endswith('.unmapped_ratio') and point.value>1:raise ValueError('Financial gauge')
    elif not isinstance(metric.data,Sum) or not metric.data.is_monotonic or not math.isfinite(point.value) or point.value<0:raise ValueError('Financial counter')
    return replace(point,attributes=labels(config),exemplars=[])
