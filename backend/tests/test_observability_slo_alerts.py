from datetime import timedelta
import pytest
from backend.observability.slo_alerts import DryRun,burn_conditions,safety_condition,Signals,signal_conditions,promql
from backend.observability.slo_contract import SAFETY_KINDS,BURN_TIERS
from backend.observability.slo_evaluator import Counts,evaluate
from backend.tests.test_observability_slo_evaluator import source,NOW

@pytest.mark.parametrize('kind',sorted(SAFETY_KINDS))
def test_hard_safety_is_critical_no_budget(kind):
    c=safety_condition(kind,True)
    assert c.active and c.severity=='CRITICAL' and c.action=='PAGE'
    assert not safety_condition(kind).active

def test_activation_resolution_grouping_and_stale_hold():
    dry=DryRun();conditions=[]
    for key in ('availability','terminal_completion'):
        conditions+=burn_conditions(evaluate(key,source(Counts(good=900,bad=100)),now=NOW),'development')
    first=dry.update(conditions)
    assert len(first['opened'])>=2 and len(first['notifications'])==1
    assert not dry.update([],fresh=False)['resolved']
    assert not dry.update(conditions)['opened']
    clear=[]
    for key in ('availability','terminal_completion'):clear+=burn_conditions(evaluate(key,source(Counts(good=1000)),now=NOW),'development')
    assert dry.update(clear)['resolved']

@pytest.mark.parametrize('kwargs,key',[({'terminal_integrity_failures':1},'terminal_integrity'),({'turns':100,'timeouts':6},'timeout_stall_spike'),({'model_calls':1000,'model_failures':6},'model_degradation'),({'gateway_calls':1000,'gateway_failures':6},'gateway_degradation'),({'pool_exhaustions':3},'db_acquisition_degradation'),({'persistence_failures':1,'pending_persistence':1},'persistence_degradation'),({'exporter_degraded_seconds':300},'telemetry_export_failure'),({'trace_calls':1000,'trace_incomplete':2},'trace_completeness'),({'sse_calls':1000,'sse_missing':2},'sse_delivery'),({'queue_failures':3},'queue_saturation')])
def test_signal_alerts_fresh_activate_clear(kwargs,key):
    assert {c.alert_id for c in signal_conditions(Signals(**kwargs)) if c.active}=={key}
    assert not any(c.active for c in signal_conditions(Signals(**kwargs,fresh=False)))
    assert not any(c.active for c in signal_conditions(Signals()))

@pytest.mark.parametrize('key',['availability','terminal_completion','latency_general'])
def test_generated_query_one_logical_condition(key):
    page=promql(key,'PAGE');ticket=promql(key,'TICKET')
    assert all('window="'+str(w)+'"' in page for w in (3600,300,21600,1800))
    assert ' > 14.4' in page and ' > 6.0' in page and ' or on (environment, operation) ' in page
    assert 'window="259200"' in ticket and 'window="21600"' in ticket and ' > 1.0' in ticket
