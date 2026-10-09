from dataclasses import replace
import pytest
from backend.observability.runtime import Runtime
from backend.tests.test_observability_runtime import config
from backend.observability.finops.control_metrics import Metrics,UNITS,safe_point,COUNTERS
from backend.observability.config import ObservabilityConfig

def test_bounded_counters_export_and_poison_ids_rejected():
    runtime=Runtime(config())
    try:
        producer=Metrics(runtime)
        for name in COUNTERS:producer.record(name)
        runtime.flush(1)
        metrics=[m for d in runtime.exporters['metric'].records for r in d.resource_metrics for s in r.scope_metrics for m in s.metrics if m.name in UNITS]
        assert {m.name for m in metrics}==set(UNITS)
        for m in metrics:
            p=m.data.data_points[0];assert safe_point(m,p,runtime.config).exemplars==[]
            for key in ('generation_id','snapshot_id','run_id','billing_account','unit_id'):
                with pytest.raises(ValueError):safe_point(m,replace(p,attributes=dict(p.attributes)|{key:'SECRET'}),runtime.config)
        for name,value in [('amount',10),('publications',-1),('publications',float('inf'))]:
            with pytest.raises(ValueError):producer.record(name,value)
    finally:runtime.close()

def test_disabled_default_and_explicit_effective_configuration():
    assert not ObservabilityConfig().finops_controls_enabled
    cfg=ObservabilityConfig.from_settings_mapping({'SLOPANOC_FINOPS_CONTROLS_ENABLED':'true'},graph_fallback=10,knowledge_fallback=10)
    assert cfg.finops_controls_enabled
