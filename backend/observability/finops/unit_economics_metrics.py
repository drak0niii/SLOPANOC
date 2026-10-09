"""Finite operational pipeline counters only; financial truth stays in Decimal records."""
from dataclasses import replace
import math
from opentelemetry.sdk.metrics.view import View
from opentelemetry.sdk.metrics.export import Sum
from .billing_metrics import labels
from ..tracing import SCOPE
from ..slo_metrics import LABELS

PREFIX='slopanoc.finops.unit_economics.'
COUNTERS=('snapshot_successes','snapshot_failures','source_expiry_gaps','publications','generation_failures',
          'driver_unavailable','control_failures','identical_generations','superseded')
UNITS={PREFIX+n:'1' for n in COUNTERS}

def views():
    return [View(instrument_name=n,meter_name=SCOPE,attribute_keys=LABELS) for n in UNITS]

def register(runtime):
    meter=runtime.meter_provider.get_meter(SCOPE)
    runtime.unit_economics_instruments={n:meter.create_counter(PREFIX+n,unit='1') for n in COUNTERS}

class Metrics:
    def __init__(self,runtime):self.runtime=runtime
    def record(self,name,value=1):
        if name not in COUNTERS or isinstance(value,bool) or not isinstance(value,(int,float)) or not math.isfinite(value) or value<0:
            raise ValueError('Invalid unit economics metric')
        r=self.runtime
        if r is None or not r.enabled or not hasattr(r,'unit_economics_instruments'):return
        r.unit_economics_instruments[name].add(value,labels(r.config))

def safe_point(metric,point,config):
    if metric.name not in UNITS or metric.unit!='1' or not isinstance(metric.data,Sum) or not metric.data.is_monotonic:
        raise ValueError('Invalid unit economics counter')
    if dict(point.attributes)!=labels(config) or not math.isfinite(point.value) or point.value<0:
        raise ValueError('Invalid unit economics dimensions')
    return replace(point,attributes=labels(config),exemplars=[])
