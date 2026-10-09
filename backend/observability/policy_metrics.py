"""Bounded M15 operational counters; never financial or SLO truth."""
from dataclasses import replace
import math
from opentelemetry.sdk.metrics.view import View
from opentelemetry.sdk.metrics.export import Sum
from .tracing import SCOPE
from .slo_metrics import LABELS
from .finops.billing_metrics import labels

DATASETS=('active_terminal','ordinary_summary','exceptional_summary','slo_receipts','slo_working_buckets',
    'finops_control_input','finops_control_generation','finops_forecast_backtest_generation','finops_unit_denominator_snapshot')
RETENTION_FIELDS=('scanned','eligible','blocked','unknown','deleted','dry_runs','failures')
UNITS={f'slopanoc.retention.{dataset}.{field}':'1' for dataset in DATASETS for field in RETENTION_FIELDS}
UNITS.update({f'slopanoc.archive.{field}':'1' for field in ('eligible','confirmed','failures','incomplete')})

def views():return [View(instrument_name=name,meter_name=SCOPE,attribute_keys=LABELS) for name in UNITS]
def register(runtime):
    meter=runtime.meter_provider.get_meter(SCOPE)
    runtime.policy_instruments={name:meter.create_counter(name,unit='1') for name in UNITS}
def record(runtime,name,value=1):
    if name not in UNITS or type(value) not in (int,float) or not math.isfinite(value) or value<0:
        raise ValueError('Invalid policy metric')
    if runtime is not None and runtime.enabled and hasattr(runtime,'policy_instruments'):
        runtime.policy_instruments[name].add(value,labels(runtime.config))
def safe_point(metric,point,config):
    if metric.name not in UNITS or metric.unit!='1' or not isinstance(metric.data,Sum) or not metric.data.is_monotonic:
        raise ValueError('Invalid policy metric')
    if dict(point.attributes)!=labels(config) or not math.isfinite(point.value) or point.value<0:
        raise ValueError('Invalid policy dimensions')
    return replace(point,attributes=labels(config),exemplars=[])
