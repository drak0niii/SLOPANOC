import time
from threading import Event
import pytest
from opentelemetry.sdk.trace.export import SpanExportResult
from opentelemetry.proto.collector.trace.v1.trace_service_pb2 import ExportTraceServiceRequest
from opentelemetry.proto.collector.metrics.v1.metrics_service_pb2 import ExportMetricsServiceRequest
from opentelemetry.proto.collector.logs.v1.logs_service_pb2 import ExportLogsServiceRequest
from backend.observability.config import ObservabilityConfig
from backend.observability.runtime import Runtime, Lease
from backend.observability.tracing import span, SCOPE
from backend.observability.logging import emit
from backend.observability.schemas import OperationalEvent
from backend.observability.exporters.queue import ExportQueue
from backend.observability.metrics import Health
from backend.tests._m1_otel import receiver
from backend.tests.test_observability_runtime import config

SENTINELS=['Bearer FAKE_BEARER_TOKEN','FAKE_SECRET_123','FAKE_PROMPT_BODY','FAKE_TEAMS_TEXT','FAKE_AUTH_HEADER']


def test_actual_local_otlp_three_signals_and_no_inherited_credentials(monkeypatch):
    monkeypatch.setenv('OTEL_EXPORTER_OTLP_HEADERS','Authorization=FAKE_AUTH_HEADER')
    monkeypatch.setenv('OTEL_EXPORTER_OTLP_TRACES_CLIENT_CERTIFICATE','/fake/cert')
    monkeypatch.setenv('OTEL_EXPORTER_OTLP_ENDPOINT','https://never-contact.invalid')
    with receiver() as sink:
        c=ObservabilityConfig(observability_enabled=True,otel_enabled=True,
            otel_exporter_otlp_endpoint=sink.endpoint,otel_batch_interval_seconds=.01)
        r=Runtime(c)
        try:
            with span(r,OperationalEvent.INITIALIZED,{'message_count':3,'prompt':SENTINELS[2]}):
                value=emit(r,OperationalEvent.INITIALIZED,metadata={'message_count':3,'authorization':SENTINELS[4]})
            assert r.flush(1)
            assert r.meter_provider.force_flush(timeout_millis=1000)
            trace_request=ExportTraceServiceRequest.FromString(sink.wait('/v1/traces')[0][1])
            sample=trace_request.resource_spans[0].scope_spans[0].spans[0]
            assert sample.name=='telemetry.initialized'
            assert sample.trace_id.hex()==value.trace_id
            assert sample.span_id.hex()==value.span_id
            assert sample.attributes[0].key=='message_count'
            logs=ExportLogsServiceRequest.FromString(sink.wait('/v1/logs')[0][1])
            log=logs.resource_logs[0].scope_logs[0].log_records[0]
            assert log.trace_id==sample.trace_id and log.span_id==sample.span_id
            metrics=ExportMetricsServiceRequest.FromString(sink.wait('/v1/metrics')[0][1])
            assert metrics.resource_metrics
            for _,body,headers in sink.snapshot():
                assert not any(s.encode() in body for s in SENTINELS)
                assert 'Authorization' not in headers
            assert r.exporters['trace']._client_cert is None
        finally: r.close()


@pytest.mark.parametrize('signal',['trace','log'])
def test_thrown_exporter_error_nonfatal_and_health(signal):
    class Broken:
        def export(self,batch): raise RuntimeError('Bearer FAKE_SECRET')
        def shutdown(self): raise RuntimeError('FAKE_SECRET')
    r=Runtime(config(),{signal:Broken()})
    try:
        with span(r,OperationalEvent.INITIALIZED):
            emit(r,OperationalEvent.INITIALIZED)
        assert r.flush(1)
        assert r.health.snapshot()[signal]['failed']==1
        assert r.health.snapshot()[signal]['dropped']==1
    finally: r.close()


def test_queue_overflow_is_counted_and_shutdown_is_bounded():
    entered=Event();release=Event()
    class Slow:
        def export(self,batch):
            entered.set();release.wait(2)
            return SpanExportResult.SUCCESS
        def shutdown(self): pass
    c=config(otel_queue_capacity=2,otel_batch_size=1,otel_batch_interval_seconds=.001)
    health=Health();q=ExportQueue(Slow(),'trace',c,health)
    try:
        assert q.put('safe1');assert entered.wait(.5)
        assert q.put('safe2');assert q.put('safe3');assert not q.put('safe4')
        assert health.snapshot()['trace']['dropped']==1
        assert health.snapshot()['trace']['queue_size']==2
        assert not q.flush(10)
        started=time.monotonic();q.shutdown(10)
        assert time.monotonic()-started < .1
        assert health.snapshot()['trace']['shutdown_failed']==1
        assert not q.put('safe5')
    finally: release.set()


def test_returned_failure_and_http_retry_bounds():
    with receiver(status=503) as sink:
        c=ObservabilityConfig(observability_enabled=True,otel_enabled=True,
            otel_exporter_otlp_endpoint=sink.endpoint,otel_export_timeout_seconds=.1,
            otel_batch_interval_seconds=.001,otel_shutdown_seconds=.15)
        r=Runtime(c)
        with span(r,OperationalEvent.INITIALIZED): pass
        start=time.monotonic()
        r.flush(.5);r.close()
        assert time.monotonic()-start < 1
        assert r.health.snapshot()['trace']['failed']>0
        assert len(sink.snapshot()) < 10


def test_actual_app_outage_preserves_fake_sync_and_sse_turns(monkeypatch):
    import backend.api.app as app_module
    import backend.observability.runtime as runtime_module
    from backend.api.chat_service import ChatService, get_chat_service
    from backend.api.session_service import ApiSessionService, get_session_service
    from backend.tests._api_fakes import FakeRunner
    from fastapi.testclient import TestClient
    with receiver(status=503) as sink:
        c=ObservabilityConfig(observability_enabled=True,otel_enabled=True,
            otel_exporter_otlp_endpoint=sink.endpoint,otel_export_timeout_seconds=.05,
            otel_batch_interval_seconds=.001,otel_shutdown_seconds=.2)
        r=Runtime(c)
        monkeypatch.setattr(runtime_module,'acquire',lambda ignored:Lease(r))
        app=app_module.create_app()
        sessions=ApiSessionService()
        app.dependency_overrides[get_session_service]=lambda:sessions
        app.dependency_overrides[get_chat_service]=lambda:ChatService(sessions,runner=FakeRunner(sessions))
        with TestClient(app) as client:
            with span(r,OperationalEvent.INITIALIZED): pass
            emit(r,OperationalEvent.INITIALIZED)
            session=client.post('/api/sessions').json()['session_id']
            response=client.post(f'/api/sessions/{session}/messages',json={'message':'hello'})
            assert response.status_code==200
            assert response.json()['message']['content']=='echo: hello'
            stream=client.post(f'/api/sessions/{session}/messages/stream',json={'message':'hello again'})
            assert stream.status_code==200
            assert 'message.completed' in stream.text and 'run.completed' in stream.text
            r.flush(.5)
            assert r.health.snapshot()['trace']['failed']>0
