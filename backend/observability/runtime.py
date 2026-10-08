"""Explicit SDK ownership for the sole FastAPI lifespan; imports are inert.

Factories use private providers. Global providers are installed deliberately once
by acquire(); tests of that irreversible API run in subprocesses. No ADK bootstrap.
"""
from threading import Lock, Thread, Event
import time
from opentelemetry import trace, metrics, _logs, propagate
from opentelemetry.trace.propagation.tracecontext import TraceContextTextMapPropagator
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.sampling import ALWAYS_ON
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader
from opentelemetry.sdk._logs import LoggerProvider
from .attributes import Attribute
from .schemas import ResourceMetadata, TELEMETRY_SCHEMA_VERSION, OperationalEvent
from .metrics import Health, HealthMetricExporter, health_views, register_health
from .tracing import SafeSpanProcessor, SCOPE, SCHEMA_URL
from .logging import SafeLogProcessor, ProductionLogging, emit
from .exporters.queue import ExportQueue
from .exporters.local import LocalExporter, LocalMetricExporter


def resource_metadata(config):
    attributes = {Attribute.SERVICE_NAME:config.otel_service_name,
        Attribute.ENVIRONMENT:config.otel_environment,
        Attribute.TELEMETRY_SCHEMA_VERSION:str(TELEMETRY_SCHEMA_VERSION)}
    for key, value in ((Attribute.SERVICE_VERSION,config.otel_service_version),
        (Attribute.REGION,config.otel_region), (Attribute.GIT_SHA,config.otel_git_sha),
        (Attribute.RELEASE_ID,config.otel_release_id),
        (Attribute.CLOUD_RUN_REVISION,config.otel_cloud_run_revision)):
        if value is not None:
            attributes[key] = value
    if config.otel_cloud_run_sidecar:
        attributes.update({Attribute.CLOUD_PROVIDER:'gcp',Attribute.CLOUD_PLATFORM:'gcp_cloud_run'})
    return ResourceMetadata(attributes=attributes)


class Runtime:
    def __init__(self, config, exporters=None):
        from .model_instrumentation import protect_content
        protect_content()
        self.config, self.health = config, Health(config)
        from .model_provider import compatible
        try:
            if not compatible():
                self.health.add("trace", "failed")
        except Exception:
            self.health.add("trace", "failed")
        self.enabled = config.observability_enabled and config.otel_enabled
        self.closed = False
        self._close_lock = Lock()
        self.queues = []
        self.exporters = {}
        self.tracer_provider = self.meter_provider = self.logger_provider = None
        self.logger = None
        self.logging = None
        self.tracer = trace.NoOpTracer()
        self.resource = Resource({key.value:value for key,value in resource_metadata(config).attributes.items()})
        if not self.enabled:
            return
        if exporters is None:
            if config.otel_exporter_mode == 'otlp':
                from .exporters.otlp import exporters as create_exporters
                exporters = create_exporters(config)
            elif config.otel_exporter_mode == 'local':
                exporters = {'trace':LocalExporter('trace',config.otel_queue_capacity),
                    'log':LocalExporter('log',config.otel_queue_capacity), 'metric':LocalMetricExporter()}
            else:
                exporters = {}
        self.exporters = exporters
        self.tracer_provider = TracerProvider(resource=self.resource, sampler=ALWAYS_ON, shutdown_on_exit=False)
        readers = []
        if 'metric' in exporters:
            readers.append(PeriodicExportingMetricReader(HealthMetricExporter(exporters['metric'],self.health,config),
                export_interval_millis=config.otel_metric_interval_seconds*1000,
                export_timeout_millis=config.otel_export_timeout_seconds*1000))
        self.meter_provider = MeterProvider(resource=self.resource, metric_readers=readers,
            views=health_views(), shutdown_on_exit=False)
        self.logger_provider = LoggerProvider(resource=self.resource, shutdown_on_exit=False)
        for signal, processor, provider in (
            ('trace',SafeSpanProcessor,self.tracer_provider), ('log',SafeLogProcessor,self.logger_provider)):
            if signal in exporters:
                queue = ExportQueue(exporters[signal],signal,config,self.health)
                self.queues.append(queue)
                if signal == 'trace':
                    provider.add_span_processor(processor(queue,self.resource))
                else:
                    provider.add_log_record_processor(processor(queue,self.resource))
        self.tracer = self.tracer_provider.get_tracer(SCOPE, schema_url=SCHEMA_URL)
        self.logger = self.logger_provider.get_logger(SCOPE, schema_url=SCHEMA_URL)
        register_health(self.meter_provider,config,self.health)
        from .model_metrics import register
        register(self)
        from .execution_metrics import register as register_execution
        register_execution(self)
        from .dependency_metrics import register as register_dependency
        register_dependency(self)
        from .reliability_metrics import register as register_reliability
        register_reliability(self)
        from .slo_metrics import register as register_slo
        register_slo(self)
        from .finops.metrics import register as register_accounting
        register_accounting(self)
        self.health.initialized = True

    def start_logging(self):
        if self.enabled and self.logging is None:
            self.logging = ProductionLogging(self.config)
            emit(self,OperationalEvent.INITIALIZED)

    def flush(self, timeout_seconds=None):
        deadline = time.monotonic()+(self.config.otel_shutdown_seconds if timeout_seconds is None else timeout_seconds)
        ok = True
        for queue in self.queues:
            ok = queue.flush(max(0,deadline-time.monotonic())*1000) and ok
        if self.meter_provider is not None:
            done = Event()
            result = [False]
            remaining = max(0,deadline-time.monotonic())
            def flush_metrics():
                try:
                    result[0] = self.meter_provider.force_flush(timeout_millis=max(1,remaining*1000))
                except Exception:
                    self.health.add('metric','failed')
                finally:
                    done.set()
            Thread(target=flush_metrics,name='slopanoc-metric-flush',daemon=True).start()
            ok = done.wait(remaining) and result[0] and ok
        return ok

    def close(self):
        with self._close_lock:
            if self.closed:
                return
            self.closed = True
        if not self.enabled:
            self.health.shutdown_complete = True
            return
        deadline = time.monotonic()+self.config.otel_shutdown_seconds
        emit(self,OperationalEvent.SHUTDOWN)
        if not self.flush(max(0,deadline-time.monotonic())):
            self.health.add('trace','shutdown_failed')
        for queue in self.queues:
            queue.shutdown(max(0,deadline-time.monotonic())*1000)
        # SDK metric reader joins/exporter shutdown and hostile injected exporters
        # are isolated on a daemon. No executor join or atexit hook can extend exit.
        done = Event()
        def cleanup():
            try:
                self.tracer_provider.shutdown()
                self.logger_provider.shutdown()
                self.meter_provider.shutdown(timeout_millis=max(1,deadline-time.monotonic())*1000)
            except Exception:
                self.health.add('metric','shutdown_failed')
            finally:
                done.set()
        Thread(target=cleanup, name='slopanoc-telemetry-shutdown',daemon=True).start()
        if not done.wait(max(0,deadline-time.monotonic())):
            self.health.add('metric','shutdown_failed')
        if self.logging:
            self.logging.close()
        self.health.shutdown_complete = done.is_set()


_lock = Lock()
_runtime = None
_leases = 0
_fingerprint = None


class Lease:
    def __init__(self, runtime, process_owned=False):
        self.runtime, self.process_owned, self.closed = runtime, process_owned, False

    def close(self):
        global _leases
        with _lock:
            if self.closed:
                return
            self.closed = True
            if self.process_owned:
                _leases -= 1
                should_close = _leases == 0
            else:
                should_close = True
        if should_close:
            self.runtime.close()


def acquire(config):
    """One irreversible global installation, no silent adoption of foreign SDKs."""
    global _runtime, _leases, _fingerprint
    if not (config.observability_enabled and config.otel_enabled):
        return Lease(Runtime(config))
    # Secret values participate only in private equality, never logging/UI output.
    fingerprint = config.model_dump() | {'endpoint':config.otel_exporter_otlp_endpoint.get_secret_value()
        if config.otel_exporter_otlp_endpoint else None}
    with _lock:
        if _runtime is not None:
            if _runtime.closed or _leases == 0 or fingerprint != _fingerprint:
                raise RuntimeError('Telemetry process ownership conflict; restart required')
            _leases += 1
            return Lease(_runtime,True)
        # Read documented getters; inspect proxy types without mutating private
        # SDK globals. Foreign providers are neither replaced nor silently exported.
        providers = (trace.get_tracer_provider(), metrics.get_meter_provider(), _logs.get_logger_provider())
        if any(type(provider).__name__ not in {'ProxyTracerProvider','_ProxyMeterProvider','ProxyLoggerProvider','_ProxyLoggerProvider'}
               for provider in providers):
            raise RuntimeError('Telemetry provider already owned by another runtime')
        runtime = Runtime(config)
        trace.set_tracer_provider(runtime.tracer_provider)
        metrics.set_meter_provider(runtime.meter_provider)
        _logs.set_logger_provider(runtime.logger_provider)
        propagate.set_global_textmap(TraceContextTextMapPropagator())
        runtime.start_logging()
        _runtime, _fingerprint, _leases = runtime, fingerprint, 1
        return Lease(runtime,True)


def current_runtime():
    """Read existing process ownership without initializing/replacing providers."""
    with _lock:
        return _runtime if _runtime is not None and not _runtime.closed else None
