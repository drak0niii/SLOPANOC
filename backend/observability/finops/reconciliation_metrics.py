"""Bounded M12 instruments, exact accounting remains in durable Decimal records."""
from dataclasses import replace
import math
from opentelemetry.sdk.metrics.view import View,ExplicitBucketHistogramAggregation
from opentelemetry.sdk.metrics.export import Sum,Histogram,Gauge
from opentelemetry.metrics import Observation
from ..slo_contract import DURATION_BOUNDS
from ..tracing import SCOPE
from .billing_metrics import labels

COUNTERS=('runs','publications','failures','control_failures','identical_generations','superseded')
GAUGES=('mapping_coverage','runtime_coverage','allocation_coverage','direct_ratio','shared_ratio','unallocated_ratio',
        'unresolved_periods','source_stale','accounting_debt','lag_seconds')
PREFIX='slopanoc.finops.reconciliation.'
UNITS={PREFIX+n:('s' if n in ('duration_seconds','lag_seconds') else '1') for n in COUNTERS+GAUGES+('duration_seconds',)}

def views():
    from ..slo_metrics import LABELS
    return [View(instrument_name=n,meter_name=SCOPE,attribute_keys=LABELS,
        **({'aggregation':ExplicitBucketHistogramAggregation(DURATION_BOUNDS)} if n.endswith('.duration_seconds') else {})) for n in UNITS]

def register(runtime):
    meter=runtime.meter_provider.get_meter(SCOPE)
    runtime.reconciliation_points={}
    runtime.reconciliation_instruments={n:meter.create_counter(PREFIX+n,unit='1') for n in COUNTERS}
    runtime.reconciliation_instruments['duration_seconds']=meter.create_histogram(PREFIX+'duration_seconds',unit='s')
    for name in GAUGES:
        def observe(options,name=name):
            v=runtime.reconciliation_points.get(name)
            return [] if v is None else [Observation(v,labels(runtime.config))]
        meter.create_observable_gauge(PREFIX+name,callbacks=[observe],unit=UNITS[PREFIX+name])

class Metrics:
    def __init__(self,runtime):self.runtime=runtime
    def record(self,name,value=1):
        if name not in COUNTERS+GAUGES+('duration_seconds',) or not math.isfinite(value) or value<0:
            raise ValueError('Invalid reconciliation metric')
        if (name.endswith('coverage') or name.endswith('ratio')) and value>1:raise ValueError('Invalid ratio')
        r=self.runtime
        if r is None or not r.enabled or not hasattr(r,'reconciliation_instruments'):return
        if name in GAUGES:r.reconciliation_points[name]=value
        else:
            i=r.reconciliation_instruments[name]
            (i.record if name=='duration_seconds' else i.add)(value,labels(r.config))

def safe_point(metric,point,config):
    from ..slo_metrics import LABELS
    n=metric.name.removeprefix(PREFIX)
    if set(point.attributes)!=LABELS or dict(point.attributes)!=labels(config) or metric.unit!=UNITS[metric.name]:raise ValueError('Reconciliation dimensions')
    if n=='duration_seconds':
        if not isinstance(metric.data,Histogram) or tuple(point.explicit_bounds)!=DURATION_BOUNDS or point.count<0 or point.sum<0 or not math.isfinite(point.sum):raise ValueError('Invalid histogram')
    else:
        if not isinstance(metric.data,Sum if n in COUNTERS else Gauge) or not math.isfinite(point.value) or point.value<0:raise ValueError('Invalid value')
        if n in COUNTERS and not metric.data.is_monotonic:raise ValueError('Invalid counter')
        if (n.endswith('coverage') or n.endswith('ratio')) and point.value>1:raise ValueError('Invalid ratio')
    return replace(point,attributes=labels(config),exemplars=[])
