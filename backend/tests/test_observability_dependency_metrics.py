from dataclasses import replace
import pytest
from backend.observability.dependency_instrumentation import dependency_scope
from backend.observability.dependency_metrics import safe_point, LABELS, POOL_LABELS, GAUGES, UNITS
from backend.observability.dependency_contract import project
from backend.observability.errors import ErrorCode
from backend.tests._m5_dependencies import environment, points, capture


def test_registered_units_unsampled_requests_separate_categories():
    with environment() as (r,t):
        for _ in range(2):
            with dependency_scope('power_automate_gateway','teams.getMessages','http.client') as scope:
                scope.update(**{'slopanoc.pagination':True})
        with dependency_scope('chat_attachments','upload','storage.client') as scope:
            scope.update(**{'slopanoc.bytes':12})
        with dependency_scope('case_db','CHECKOUT','db.connection',kind='acquisition'):pass
        with dependency_scope('knowledge','sparse','knowledge.retrieval',kind='local'):pass
        assert points(r,'slopanoc.dependency.requests')[0].value==2
        assert points(r,'slopanoc.dependency.pages')[0].value==2
        assert points(r,'slopanoc.storage.bytes_uploaded')[0].value==12
        assert points(r,'slopanoc.db.connection_acquire_duration')
        assert points(r,'slopanoc.knowledge.stage.duration')
        for batch in r.exporters['metric'].records:
            for rm in batch.resource_metrics:
                for sm in rm.scope_metrics:
                    for m in sm.metrics:
                        if m.name in UNITS:
                            assert m.unit==UNITS[m.name]
                            for p in m.data.data_points:
                                assert set(p.attributes)==(POOL_LABELS if m.name in GAUGES else LABELS)
                                assert not p.exemplars

@pytest.mark.parametrize('key,value',[('slopanoc.dependency','SECRET'),('slopanoc.dependency_operation','SECRET'),('http.route','https://SECRET'),('http.response.status_code',True),('slopanoc.bytes',-1),('slopanoc.bytes',True),('slopanoc.retry_after_seconds',float('nan')),('authorization','SECRET'),('db.query.text','SECRET'),('vector',[1,2])])
def test_exact_projection_denies_arbitrary_values(key,value):
    assert key not in project({key:value})


def test_export_gate_blocks_malicious_labels_and_exemplars():
    with environment() as (r,t):
        meter=r.meter_provider.get_meter('slopanoc.observability')
        counter=meter.create_counter('slopanoc.dependency.requests')
        counter.add(1,{'environment':r.config.otel_environment,'dependency':'SECRET','operation':'SELECT','status':'COMPLETED','session_id':'SECRET'})
        assert 'SECRET' not in capture(r)
        assert r.health.snapshot()['metric']['filtered']>=1
