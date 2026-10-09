from dataclasses import replace
import pytest
from backend.observability.runtime import Runtime
from backend.tests.test_observability_runtime import config
from backend.observability.finops.reconciliation_metrics import Metrics,UNITS,safe_point,COUNTERS,GAUGES
from backend.observability.config import ObservabilityConfig


def test_bounded_metrics_export_and_identifier_poison():
    runtime=Runtime(config())
    try:
        producer=Metrics(runtime)
        for name in COUNTERS+GAUGES:producer.record(name,.5 if name.endswith(('coverage','ratio')) else 1)
        producer.record('duration_seconds',.001)
        runtime.flush(1)
        metrics=[m for d in runtime.exporters['metric'].records for r in d.resource_metrics for s in r.scope_metrics for m in s.metrics if m.name in UNITS]
        assert {m.name for m in metrics}==set(UNITS)
        for metric in metrics:
            point=metric.data.data_points[0]
            assert safe_point(metric,point,runtime.config).exemplars==[]
            with pytest.raises(ValueError):safe_point(metric,replace(point,attributes=dict(point.attributes)|{'sku_id':'SECRET_SENTINEL'}),runtime.config)
        for name,value in [('generation_id',1),('direct_ratio',2),('runs',-1)]:
            with pytest.raises(ValueError):producer.record(name,value)
    finally:runtime.close()


def test_reconciliation_disabled_default_and_explicit_configuration():
    assert not ObservabilityConfig().finops_reconciliation_enabled
    config=ObservabilityConfig.from_settings_mapping({'SLOPANOC_FINOPS_RECONCILIATION_ENABLED':'true'},graph_fallback=10,knowledge_fallback=10)
    assert config.finops_reconciliation_enabled
