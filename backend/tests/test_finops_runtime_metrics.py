"""Finite metric pipeline and canonical M9 coverage-alert behavior."""
from dataclasses import replace
from datetime import timedelta
import pytest
from backend.observability.runtime import Runtime
from backend.tests.test_observability_runtime import config
from backend.observability.finops.metrics import record,UNITS,safe_point
from backend.observability.slo_evaluator import Snapshot,Bucket,Counts,evaluate
from backend.observability.slo_alerts import cost_coverage_condition,DryRun,cost_coverage_promql
from backend.tests.test_finops_runtime_ledger import NOW

def test_accounting_metric_export_and_poisoned_labels():
    runtime=Runtime(config())
    try:
        for name in ('admissions_attempted','provider_started','ledger_persisted','duplicates','pending','debt'):record(runtime,name,1)
        record(runtime,'admission_duration',.005);record(runtime,'capture_duration',.007)
        assert runtime.flush(1)
        exported=tuple(runtime.exporters['metric'].records)
        metrics=[m for data in exported for resource in data.resource_metrics for scope in resource.scope_metrics for m in scope.metrics if m.name in UNITS]
        assert len(metrics)>=8
        for m in metrics:
            point=m.data.data_points[0]
            assert set(point.attributes)=={'environment','operation','status','window'}
            assert not safe_point(m,point,runtime.config).exemplars
            with pytest.raises(ValueError):safe_point(m,replace(point,attributes=dict(point.attributes)|{'run_id':'PRIVATE_MARKER'}),runtime.config)
        with pytest.raises(ValueError):record(runtime,'user_id',1)
    finally:runtime.close()

@pytest.mark.parametrize('good,bad,active',[(100,0,False),(99,1,True),(9999,1,False),(9998,2,True),(0,0,False)])
def test_m9_24h_coverage_alert_not_finite_burn(good,bad,active):
    snap=Snapshot('development',(Bucket(NOW-timedelta(minutes=1),Counts(good,bad)),),updated_at=NOW,coverage_start=NOW-timedelta(days=29),source='DURABLE_ACCOUNTING')
    result=evaluate('cost_ledger_completeness',snap,now=NOW,window_seconds=86400)
    assert result['allowed_bad'] is None and result['remaining_fraction'] is None and not result['burn_tiers']
    condition=cost_coverage_condition(result,'development');assert condition.active==active
    alert=DryRun().update([condition]);assert bool(alert['opened'])==active
    stale=evaluate('cost_ledger_completeness',snap,now=NOW+timedelta(seconds=301),window_seconds=86400)
    assert not cost_coverage_condition(stale,'development').active
    assert '86400' in cost_coverage_promql() and '> 0.0001' in cost_coverage_promql()
