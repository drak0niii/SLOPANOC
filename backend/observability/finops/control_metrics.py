"""Finite operational pipeline counters only; financial truth stays in Decimal records."""
from dataclasses import replace
import math
from opentelemetry.sdk.metrics.view import View
from opentelemetry.sdk.metrics.export import Sum
from .billing_metrics import labels
from ..tracing import SCOPE
from ..slo_metrics import LABELS

PREFIX='slopanoc.finops.controls.'
COUNTERS=('publications','publication_failures','detector_failures','identical_generations','superseded',
 'budget_evaluations','budget_exceeded','forecast_generations','forecast_quality_unavailable',
 'projected_overrun','anomaly_evaluations','cost_anomalies','unit_cost_anomalies','unallocated_surges','source_degradation')
BUDGET_STATES=('HEALTHY','INFORMATIONAL','WATCH','AT_RISK','AT_LIMIT','EXCEEDED','ZERO_BUDGET','SOURCE_STALE','RECONCILIATION_INCOMPLETE','SCOPE_UNAVAILABLE','CURRENCY_UNAVAILABLE','INSUFFICIENT_DATA','POLICY_UNAVAILABLE')
from .anomaly_registry import DETECTORS
COUNTERS+=tuple('budget_state_'+n.lower() for n in BUDGET_STATES)+tuple('anomaly_type_'+n.lower() for n in DETECTORS)+tuple('anomaly_severity_'+n for n in ('none','watch','high','critical'))
UNITS={PREFIX+n:'1' for n in COUNTERS}

def views():
    return [View(instrument_name=n,meter_name=SCOPE,attribute_keys=LABELS) for n in UNITS]

def register(runtime):
    meter=runtime.meter_provider.get_meter(SCOPE)
    runtime.controls_instruments={n:meter.create_counter(PREFIX+n,unit='1') for n in COUNTERS}

class Metrics:
    def __init__(self,runtime):self.runtime=runtime
    def record(self,name,value=1):
        if name not in COUNTERS or isinstance(value,bool) or not isinstance(value,(int,float)) or not math.isfinite(value) or value<0:
            raise ValueError('Invalid financial controls metric')
        r=self.runtime
        if r is None or not r.enabled or not hasattr(r,'controls_instruments'):return
        r.controls_instruments[name].add(value,labels(r.config))

def safe_point(metric,point,config):
    if metric.name not in UNITS or metric.unit!='1' or not isinstance(metric.data,Sum) or not metric.data.is_monotonic:
        raise ValueError('Invalid financial controls counter')
    if dict(point.attributes)!=labels(config) or not math.isfinite(point.value) or point.value<0:
        raise ValueError('Invalid financial controls dimensions')
    return replace(point,attributes=labels(config),exemplars=[])
