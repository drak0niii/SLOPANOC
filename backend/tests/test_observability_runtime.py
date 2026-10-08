"""M1 factories are private; irreversible process installation is subprocess-only."""
import importlib.metadata
import json
import subprocess
import sys
import time
from contextlib import contextmanager
from pathlib import Path
from threading import Event
import pytest
from fastapi.testclient import TestClient
from backend.observability.config import ObservabilityConfig
from backend.observability.runtime import Runtime, Lease
from backend.observability.tracing import span, current_trace_ids
from backend.observability.schemas import OperationalEvent
from backend.config.settings import Settings, ConfigurationError


def config(**changes):
    return ObservabilityConfig(observability_enabled=True, otel_enabled=True,
        otel_exporter_mode='local', **changes)


@pytest.mark.parametrize('mode', ['local','none'])
def test_enabled_private_providers_and_metadata(mode):
    c=ObservabilityConfig(observability_enabled=True,otel_enabled=True,otel_exporter_mode=mode,
        otel_service_version='v1',otel_region='europe-west1',otel_git_sha='abcdef123',
        otel_release_id='release-1',otel_cloud_run_revision='rev-1',otel_cloud_run_sidecar=True)
    r=Runtime(c)
    try:
        assert r.tracer_provider and r.meter_provider and r.logger_provider
        assert r.resource.attributes['slopanoc.telemetry_schema_version']=='1'
        assert r.resource.attributes['slopanoc.cloud_run_revision']=='rev-1'
        assert r.resource.attributes['cloud.platform']=='gcp_cloud_run'
        assert r.resource.attributes['service.version']=='v1'
        assert r.resource.attributes['slopanoc.git_sha']=='abcdef123'
        assert r.resource.attributes['slopanoc.release_id']=='release-1'
        assert r.resource.attributes['cloud.region']=='europe-west1'
        with span(r,OperationalEvent.INITIALIZED):
            assert all(current_trace_ids())
    finally:
        r.close();r.close()


def test_disabled_has_no_providers_or_workers():
    r=Runtime(ObservabilityConfig())
    assert not r.enabled and not r.queues and not r.exporters
    assert r.tracer_provider is r.meter_provider is r.logger_provider is None
    assert 'cloud.provider' not in r.resource.attributes
    assert 'slopanoc.git_sha' not in r.resource.attributes
    r.close()


def test_settings_revision_effective_readonly_and_secret_free():
    c=Settings({'SLOPANOC_OBSERVABILITY_ENABLED':'true','SLOPANOC_OTEL_ENABLED':'true',
        'SLOPANOC_OTEL_EXPORTER_OTLP_ENDPOINT':'http://private-collector:4318','K_REVISION':'rev-1'}).observability_config
    assert c.otel_cloud_run_revision=='rev-1'
    projection=c.effective_configuration()
    assert all(value.read_only for value in projection)
    assert 'private-collector' not in str(projection)
    assert {x.setting:x.value for x in projection}['collector_endpoint_configured'] is True


@pytest.mark.parametrize('key,value',[
 ('OTEL_EXPORTER_MODE','wrong'), ('OTEL_PROTOCOL','grpc'), ('OTEL_QUEUE_CAPACITY','0'),
 ('OTEL_EXPORT_TIMEOUT_SECONDS','NaN'), ('OTEL_SHUTDOWN_SECONDS','inf'),
 ('OTEL_BATCH_SIZE','9999'), ('OTEL_REGION','Bearer FAKE_SECRET'),
 ('OTEL_CLOUD_RUN_SIDECAR','yes'),('OTEL_EXPORTER_OTLP_ENDPOINT','https://host/v1/traces'),
 ('OTEL_EXPORTER_OTLP_ENDPOINT','http://host:0')])
def test_invalid_config_sanitized(key,value):
    with pytest.raises(ConfigurationError) as exc:
        Settings({'SLOPANOC_'+key:value}).observability_config
    assert value not in str(exc.value)


def test_production_modes_fail_closed_and_loopback_exception():
    with pytest.raises(ValueError):
        config(otel_environment='production')
    with pytest.raises(ValueError):
        ObservabilityConfig(observability_enabled=True,otel_enabled=True,otel_environment='production',
            otel_exporter_otlp_endpoint='http://remote:4318')
    assert ObservabilityConfig(observability_enabled=True,otel_enabled=True,otel_environment='production',
        otel_cloud_run_sidecar=True,otel_exporter_otlp_endpoint='http://127.0.0.1:4318').otel_enabled


def run_subprocess(code,timeout=10):
    result=subprocess.run([sys.executable,'-c',code],text=True,capture_output=True,timeout=timeout)
    assert result.returncode==0,result.stderr
    return result


def test_process_install_idempotent_conflicts_terminal_and_w3c():
    run_subprocess('''
from backend.observability.runtime import acquire
from backend.observability.config import ObservabilityConfig
from opentelemetry import trace,metrics,_logs,propagate
c=ObservabilityConfig(observability_enabled=True,otel_enabled=True,otel_exporter_mode='none')
a=acquire(c);b=acquire(c)
assert a.runtime is b.runtime
assert trace.get_tracer_provider() is a.runtime.tracer_provider
assert metrics.get_meter_provider() is a.runtime.meter_provider
assert _logs.get_logger_provider() is a.runtime.logger_provider
assert propagate.get_global_textmap().__class__.__name__=='TraceContextTextMapPropagator'
try: acquire(c.model_copy(update={'otel_environment':'staging'}))
except RuntimeError: pass
else: raise AssertionError('configuration conflict accepted')
a.close();a.close();assert not b.runtime.closed
b.close();assert b.runtime.closed
try: acquire(c)
except RuntimeError: pass
else: raise AssertionError('terminal provider reused')
''')


def test_foreign_provider_not_adopted_or_replaced():
    run_subprocess('''
from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from backend.observability.runtime import acquire
from backend.observability.config import ObservabilityConfig
p=TracerProvider(shutdown_on_exit=False);trace.set_tracer_provider(p)
try: acquire(ObservabilityConfig(observability_enabled=True,otel_enabled=True,otel_exporter_mode='none'))
except RuntimeError: pass
else: raise AssertionError('foreign provider accepted')
assert trace.get_tracer_provider() is p
p.shutdown()
''')


def test_pinned_dependencies_match_manifest_and_installed_cohort():
    text=Path('requirements.txt').read_text()
    for name,version in [('opentelemetry-api','1.41.1'),('opentelemetry-sdk','1.41.1'),
        ('opentelemetry-exporter-otlp-proto-http','1.41.1'),('opentelemetry-exporter-otlp-proto-common','1.41.1'),
        ('opentelemetry-proto','1.41.1'),('opentelemetry-semantic-conventions','0.62b1')]:
        assert name+'=='+version in text
        assert importlib.metadata.version(name)==version


@pytest.mark.parametrize('mode',['disabled','none','local'])
def test_app_boot_preserves_policy_warmup_order_and_repeated_tests(monkeypatch,mode):
    import backend.api.app as app_module
    calls=[]
    c=ObservabilityConfig() if mode=='disabled' else ObservabilityConfig(
        observability_enabled=True,otel_enabled=True,otel_exporter_mode=mode)
    monkeypatch.setattr(app_module,'get_settings',lambda:Settings({}))
    monkeypatch.setattr(app_module,'validate_runtime_database_configuration',lambda settings:(calls.append('db') or ('postgresql','postgresql')))
    async def warmup(): calls.append('warmup')
    monkeypatch.setattr(app_module,'warmup_shared_model',warmup)
    import backend.observability.runtime as runtime_module
    monkeypatch.setattr(runtime_module,'acquire',lambda ignored:Lease(Runtime(c)))
    for _ in range(2):
        with TestClient(app_module.create_app()) as client:
            assert client.get('/health').json()=={'status':'ok'}
            assert client.app.state.observability.enabled==(mode!='disabled')
        assert client.app.state.observability.closed
    assert calls==['db','warmup','db','warmup']


def test_failed_warmup_releases_telemetry(monkeypatch):
    import backend.api.app as app_module
    import backend.observability.runtime as runtime_module
    r=Runtime(config())
    monkeypatch.setattr(runtime_module,'acquire',lambda ignored:Lease(r))
    async def warmup(): raise RuntimeError('fake')
    monkeypatch.setattr(app_module,'warmup_shared_model',warmup)
    with pytest.raises(RuntimeError):
        with TestClient(app_module.create_app()): pass
    assert r.closed


def test_hanging_export_and_shutdown_do_not_hold_process_exit():
    result=run_subprocess('''
import time
from threading import Event
from backend.observability.runtime import Runtime
from backend.observability.config import ObservabilityConfig
from backend.observability.tracing import span
from backend.observability.schemas import OperationalEvent
class Hanging:
 def export(self,batch): Event().wait()
 def shutdown(self): Event().wait()
c=ObservabilityConfig(observability_enabled=True,otel_enabled=True,otel_exporter_mode='none',otel_shutdown_seconds=.05,otel_batch_interval_seconds=.001)
r=Runtime(c,{'trace':Hanging()})
with span(r,OperationalEvent.INITIALIZED): pass
time.sleep(.01)
start=time.monotonic();r.close();assert time.monotonic()-start < .2
assert r.health.snapshot()['trace']['shutdown_failed']>0
''',timeout=3)


def test_foundation_primitive_overhead(capsys):
    r=Runtime(config())
    try:
        start=time.perf_counter()
        for _ in range(500):
            with span(r,OperationalEvent.INITIALIZED): pass
        per_span=(time.perf_counter()-start)*1000/500
        print(f'M1 span producer overhead: {per_span:.3f} ms/span')
        assert per_span < 10
    finally: r.close()


def test_hanging_metric_export_cannot_hold_shutdown_or_interpreter():
    run_subprocess('''
import time
from threading import Event
from opentelemetry.sdk.metrics.export import MetricExporter
from backend.observability.runtime import Runtime
from backend.observability.config import ObservabilityConfig
class Hanging(MetricExporter):
 def export(self,*args,**kwargs): Event().wait()
 def shutdown(self,*args,**kwargs): Event().wait()
 def force_flush(self,*args,**kwargs): return False
r=Runtime(ObservabilityConfig(observability_enabled=True,otel_enabled=True,otel_exporter_mode='none',otel_shutdown_seconds=.05),{'metric':Hanging()})
start=time.monotonic();r.close();assert time.monotonic()-start < .2
assert r.health.snapshot()['metric']['shutdown_failed']>0
''',timeout=3)


@pytest.mark.parametrize('key',['OTEL_PYTHON_TRACER_PROVIDER','OTEL_PYTHON_METER_PROVIDER','OTEL_PYTHON_LOGGER_PROVIDER'])
def test_settings_reject_implicit_sdk_provider_selection(key):
    with pytest.raises(ConfigurationError) as exc:
        Settings({'SLOPANOC_OBSERVABILITY_ENABLED':'true','SLOPANOC_OTEL_ENABLED':'true',
            'SLOPANOC_OTEL_EXPORTER_MODE':'none',key:'FAKE_SECRET_PLUGIN'}).observability_config
    assert 'FAKE_SECRET_PLUGIN' not in str(exc.value)


def test_slow_fallback_writer_does_not_block_health_or_shutdown(monkeypatch):
    from backend.observability.metrics import Health
    entered=Event();release=Event()
    class BlockedStderr:
        def write(self,text): entered.set();release.wait(2)
    monkeypatch.setattr('backend.observability.metrics.sys.stderr',BlockedStderr())
    health=Health(config())
    try:
        start=time.monotonic();health.add('trace','failed')
        assert time.monotonic()-start < .1
        assert entered.wait(.5)
        for _ in range(20):health.add('trace','failed')
        assert health.snapshot()['trace']['failed']==21
    finally:release.set()
