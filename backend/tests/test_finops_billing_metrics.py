from dataclasses import replace
import pytest
from backend.observability.runtime import Runtime
from backend.tests.test_observability_runtime import config
from backend.observability.finops.billing_metrics import Metrics,UNITS,safe_point,COUNTERS

def test_financial_pipeline_metrics_and_poisoned_ids():
    runtime=Runtime(config())
    try:
        m=Metrics(runtime,'development')
        for source in ('DETAILED_BILLING','PRICING_EXPORT','FOCUS'):
            for name in COUNTERS:m.add(name,source,1)
            m.duration(source,.001)
            m.gauge('source_age',source,10)
            m.gauge('unmapped_ratio',source,.5)
        runtime.flush(1)
        metrics=[m for d in runtime.exporters['metric'].records for r in d.resource_metrics for s in r.scope_metrics for m in s.metrics if m.name in UNITS]
        assert len(metrics)==len(UNITS)
        for metric in metrics:
            point=metric.data.data_points[0];assert safe_point(metric,point,runtime.config).exemplars==[]
            with pytest.raises(ValueError):safe_point(metric,replace(point,attributes=dict(point.attributes)|{'billing_account':'SENTINEL'}),runtime.config)
        with pytest.raises(ValueError):m.add('sku_id','DETAILED_BILLING')
    finally:runtime.close()
