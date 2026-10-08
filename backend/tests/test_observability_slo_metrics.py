from dataclasses import replace
import pytest
from backend.observability.runtime import Runtime
from backend.observability.slo_metrics import record, safe_point
from backend.observability.slo_alerts import confirm_safety, Signals, signal_conditions
from backend.tests.test_observability_runtime import config
from backend.tests._m5_dependencies import points


@pytest.mark.parametrize('label',['run_id','turn_id','session_id','user','route','release_sha','chat_id'])
def test_high_cardinality_sdk_points_fail_closed(label):
    runtime=Runtime(config())
    try:
        record(runtime,'slopanoc.turn.accepted',1,'accepted','RUNNING')
        runtime.flush()
        metric=next(m for batch in runtime.exporters['metric'].records for rm in batch.resource_metrics for sm in rm.scope_metrics for m in sm.metrics if m.name=='slopanoc.turn.accepted')
        p=metric.data.data_points[0]
        with pytest.raises(ValueError):safe_point(metric,replace(p,attributes=dict(p.attributes)|{label:'SYNTHETIC_MARKER'}),runtime.config)
        assert not p.exemplars
    finally:runtime.close()


def test_source_age_keeps_aging_when_writer_stops(monkeypatch):
    runtime=Runtime(config());clock=[100.]
    monkeypatch.setattr('backend.observability.slo_metrics.time.monotonic',lambda:clock[0])
    try:
        record(runtime,'slopanoc.slo.source_age',10,'availability','HEALTHY','2419200')
        clock[0]=401.
        assert points(runtime,'slopanoc.slo.source_age')[-1].value==311
    finally:runtime.close()


def test_confirmed_safety_is_not_suppressed_by_an_unrelated_stale_source():
    runtime=Runtime(config())
    try:
        for kind in ('unauthorized_command_exposure','command_egress_bypass','credential_telemetry_leak','hidden_cot_persistence'):
            condition=confirm_safety(runtime,kind)
            assert condition.active and condition.severity=='CRITICAL'
        assert len(points(runtime,'slopanoc.safety.violations'))==4
        conditions=signal_conditions(Signals(available=False,fresh=False,safety=frozenset({'credential_telemetry_leak'})))
        assert next(c for c in conditions if c.alert_id=='hard_safety').active
    finally:runtime.close()
