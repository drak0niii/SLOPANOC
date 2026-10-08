"""Pure configuration contract. Settings owns the environment; no runtime activation."""
from collections.abc import Mapping
from typing import Literal
from urllib.parse import urlsplit
from pydantic import BaseModel, ConfigDict, Field, SecretStr, model_validator

SETTING_NAMES = (
    'SLO_ENABLED',
    'PROJECTION_ENABLED', 'PROJECTION_WRITE_SPACING_SECONDS', 'PROJECTION_CHECKPOINT_SECONDS',
    'PROJECTION_STALE_SECONDS', 'PROJECTION_CLOCK_GRACE_SECONDS', 'PROJECTION_CAPACITY',
    'PROJECTION_ATTEMPT_SECONDS', 'PROJECTION_TERMINAL_ATTEMPTS', 'PROJECTION_RETRY_SECONDS',
    'PROJECTION_SHUTDOWN_SECONDS', 'PROJECTION_TERMINAL_HOURS', 'PROJECTION_SUCCESS_DAYS',
    'PROJECTION_EXCEPTIONAL_DAYS',
    'OBSERVABILITY_ENABLED', 'OTEL_ENABLED', 'OTEL_EXPORTER_OTLP_ENDPOINT',
    'OTEL_SERVICE_NAME', 'OTEL_ENVIRONMENT', 'TRACE_SAMPLE_RATE',
    'TRACE_ERRORS_ALWAYS', 'TRACE_GOVERNED_TURNS_ALWAYS', 'MODEL_TIMEOUT_SECONDS',
    'GRAPH_TIMEOUT_SECONDS', 'KNOWLEDGE_TIMEOUT_SECONDS', 'DATABASE_TIMEOUT_SECONDS',
    'STORAGE_TIMEOUT_SECONDS', 'WATCHDOG_STALL_SECONDS', 'HEARTBEAT_SECONDS',
    'TURN_TIMEOUT_SECONDS',
    'CLEANUP_TIMEOUT_SECONDS',
    'SESSION_LOAD_TIMEOUT_SECONDS',
    'SESSION_LOCK_TIMEOUT_SECONDS',
    'PLANNING_TIMEOUT_SECONDS',
    'ORCHESTRATION_TIMEOUT_SECONDS',
    'AGENT_TIMEOUT_SECONDS',
    'TOOL_TIMEOUT_SECONDS',
    'GATEWAY_TIMEOUT_SECONDS',
    'DATABASE_ACQUIRE_TIMEOUT_SECONDS',
    'DATABASE_QUERY_TIMEOUT_SECONDS',
    'SECRET_TIMEOUT_SECONDS',
    'PERSISTENCE_TIMEOUT_SECONDS',
    'SSE_DELIVERY_TIMEOUT_SECONDS',
    'QUEUE_WAIT_TIMEOUT_SECONDS',
    'RETRY_MINIMUM_SECONDS', 'WATCHDOG_CHECK_SECONDS', 'BLOCKING_WORKERS',
    'BLOCKING_PENDING', 'BUSINESS_QUEUE_CAPACITY', 'BUSINESS_QUEUE_BYTES',
    'FINOPS_WRITE_SECONDS', 'FINOPS_WRITE_ATTEMPTS', 'FINOPS_WRITE_CONCURRENCY',
    'FINOPS_ADMISSION_WAIT_SECONDS', 'FINOPS_RECOVERY_BATCH', 'FINOPS_RECOVERY_SECONDS',
    'FINOPS_RECOVERY_ATTEMPTS', 'FINOPS_CHECKPOINT_SECONDS', 'FINOPS_SHUTDOWN_SECONDS',
    'FINOPS_RETENTION_MONTHS',
    'FINOPS_BILLING_INGESTION_ENABLED', 'FINOPS_BILLING_DELAYED_SECONDS',
    'FINOPS_BILLING_STALE_SECONDS', 'FINOPS_PRICING_DELAYED_SECONDS', 'FINOPS_PRICING_STALE_SECONDS',
    'FINOPS_BILLING_CADENCE_SECONDS', 'FINOPS_PRICING_CADENCE_SECONDS',
    'FINOPS_ENABLED', 'FINOPS_CURRENCY', 'FINOPS_PRICE_SOURCE',
    'FINOPS_BILLING_PROJECT', 'FINOPS_BILLING_DATASET',
    'OTEL_EXPORTER_MODE', 'OTEL_PROTOCOL', 'OTEL_EXPORT_TIMEOUT_SECONDS',
    'OTEL_QUEUE_CAPACITY', 'OTEL_BATCH_SIZE', 'OTEL_BATCH_INTERVAL_SECONDS',
    'OTEL_METRIC_INTERVAL_SECONDS', 'OTEL_SHUTDOWN_SECONDS',
    'OTEL_SERVICE_VERSION', 'OTEL_REGION', 'OTEL_GIT_SHA', 'OTEL_RELEASE_ID',
    'OTEL_CLOUD_RUN_REVISION', 'OTEL_CLOUD_RUN_SIDECAR',
)

class ObservabilityConfig(BaseModel):
    model_config = ConfigDict(extra='forbid', frozen=True, allow_inf_nan=False, hide_input_in_errors=True)
    slo_enabled: bool = False
    # M7 diagnostic-only policy; independent of trace sampling and exporters.
    projection_enabled: bool = False
    projection_write_spacing_seconds: float = Field(default=1, gt=0, le=30)
    projection_checkpoint_seconds: float = Field(default=15, gt=0, le=300)
    projection_stale_seconds: float = Field(default=60, gt=0, le=3600)
    projection_clock_grace_seconds: float = Field(default=5, ge=0, le=60)
    projection_capacity: int = Field(default=2048, gt=0, le=8192)
    projection_attempt_seconds: float = Field(default=1, gt=0, le=10)
    projection_terminal_attempts: int = Field(default=3, gt=0, le=5)
    projection_retry_seconds: float = Field(default=5, gt=0, le=30)
    projection_shutdown_seconds: float = Field(default=5, gt=0, le=30)
    projection_terminal_hours: int = Field(default=24, gt=0, le=168)
    projection_success_days: int = Field(default=30, gt=0, le=365)
    projection_exceptional_days: int = Field(default=90, gt=0, le=365)
    observability_enabled: bool = False
    otel_enabled: bool = False
    otel_exporter_otlp_endpoint: SecretStr | None = None
    otel_service_name: str = Field(default='slopanoc', pattern=r'^[a-zA-Z0-9_.-]{1,64}$')
    otel_environment: str = Field(default='development', pattern=r'^[a-zA-Z0-9_.-]{1,64}$')
    otel_exporter_mode: Literal['otlp', 'local', 'none'] = 'otlp'
    otel_protocol: Literal['http/protobuf'] = 'http/protobuf'
    otel_export_timeout_seconds: float = Field(default=2, gt=0, le=30)
    otel_queue_capacity: int = Field(default=2048, gt=0, le=16384)
    otel_batch_size: int = Field(default=256, gt=0, le=1024)
    otel_batch_interval_seconds: float = Field(default=5, gt=0, le=60)
    otel_metric_interval_seconds: float = Field(default=30, gt=0, le=300)
    otel_shutdown_seconds: float = Field(default=5, gt=0, le=30)
    otel_service_version: str | None = Field(default=None, pattern=r'^[a-zA-Z0-9_.-]{1,64}$')
    otel_region: str | None = Field(default=None, pattern=r'^[a-zA-Z0-9_.-]{1,64}$')
    otel_git_sha: str | None = Field(default=None, pattern=r'^[a-fA-F0-9]{7,64}$')
    otel_release_id: str | None = Field(default=None, pattern=r'^[a-zA-Z0-9_.-]{1,64}$')
    otel_cloud_run_revision: str | None = Field(default=None, pattern=r'^[a-zA-Z0-9_.-]{1,64}$')
    otel_cloud_run_sidecar: bool = False
    trace_sample_rate: float = Field(default=1.0, ge=0, le=1)
    trace_errors_always: Literal[True] = True
    trace_governed_turns_always: Literal[True] = True
    model_timeout_seconds: float = Field(default=120, gt=0)
    graph_timeout_seconds: float = Field(default=10, gt=0)
    knowledge_timeout_seconds: float = Field(default=15, gt=0)
    database_timeout_seconds: float = Field(default=30, gt=0)
    storage_timeout_seconds: float = Field(default=30, gt=0)
    watchdog_stall_seconds: float = Field(default=30, gt=0)
    heartbeat_seconds: float = Field(default=5, gt=0)
    # M6 PROVISIONAL hard caps; these are NOT latency SLOs.
    turn_timeout_seconds: float = Field(default=180, gt=0, le=86400)
    cleanup_timeout_seconds: float = Field(default=5, gt=0, le=86400)
    session_load_timeout_seconds: float = Field(default=10, gt=0, le=86400)
    session_lock_timeout_seconds: float = Field(default=10, gt=0, le=86400)
    planning_timeout_seconds: float = Field(default=30, gt=0, le=86400)
    orchestration_timeout_seconds: float = Field(default=150, gt=0, le=86400)
    agent_timeout_seconds: float = Field(default=150, gt=0, le=86400)
    tool_timeout_seconds: float = Field(default=30, gt=0, le=86400)
    gateway_timeout_seconds: float = Field(default=10, gt=0, le=86400)
    database_acquire_timeout_seconds: float = Field(default=5, gt=0, le=86400)
    database_query_timeout_seconds: float = Field(default=15, gt=0, le=86400)
    secret_timeout_seconds: float = Field(default=10, gt=0, le=86400)
    persistence_timeout_seconds: float = Field(default=10, gt=0, le=86400)
    sse_delivery_timeout_seconds: float = Field(default=10, gt=0, le=86400)
    queue_wait_timeout_seconds: float = Field(default=1, gt=0, le=86400)
    retry_minimum_seconds: float = Field(default=1, gt=0, le=60)
    watchdog_check_seconds: float = Field(default=1, gt=0, le=5)
    blocking_workers: int = Field(default=8, gt=0, le=64)
    blocking_pending: int = Field(default=16, ge=0, le=256)
    business_queue_capacity: int = Field(default=256, gt=0, le=4096)
    business_queue_bytes: int = Field(default=4194304, gt=0, le=67108864)
    finops_enabled: bool = False
    finops_write_seconds: float = Field(default=2, gt=0, le=5)
    finops_write_attempts: int = Field(default=3, ge=1, le=3)
    finops_write_concurrency: int = Field(default=8, ge=1, le=16)
    finops_admission_wait_seconds: float = Field(default=.25, gt=0, le=1)
    finops_recovery_batch: int = Field(default=100, ge=1, le=100)
    finops_recovery_seconds: float = Field(default=2, gt=0, le=5)
    finops_recovery_attempts: int = Field(default=5, ge=1, le=10)
    finops_checkpoint_seconds: float = Field(default=30, gt=0, le=60)
    finops_shutdown_seconds: float = Field(default=3, gt=0, le=5)
    finops_retention_months: int = Field(default=24, ge=24, le=120)
    finops_billing_ingestion_enabled: bool = False
    finops_billing_delayed_seconds: int = Field(default=86400,ge=1,le=2592000)
    finops_billing_stale_seconds: int = Field(default=259200,ge=1,le=2592000)
    finops_pricing_delayed_seconds: int = Field(default=172800,ge=1,le=2592000)
    finops_pricing_stale_seconds: int = Field(default=604800,ge=1,le=2592000)
    finops_billing_cadence_seconds: int = Field(default=3600,ge=60,le=86400)
    finops_pricing_cadence_seconds: int = Field(default=86400,ge=60,le=604800)
    finops_currency: Literal['EUR'] = 'EUR'
    finops_price_source: Literal['gcp_detailed_billing_and_pricing'] = 'gcp_detailed_billing_and_pricing'
    finops_billing_project: str | None = Field(default=None, pattern=r'^[a-z][a-z0-9-]{4,61}[a-z0-9]$')
    finops_billing_dataset: str | None = Field(default=None, pattern=r'^[A-Za-z_][A-Za-z0-9_]{0,1023}$')

    @model_validator(mode='after')
    def policy(self):
        if self.finops_billing_delayed_seconds >= self.finops_billing_stale_seconds or self.finops_pricing_delayed_seconds >= self.finops_pricing_stale_seconds:
            raise ValueError('Financial stale policy must exceed delayed policy')
        if self.projection_stale_seconds <= self.projection_checkpoint_seconds + self.projection_retry_seconds + self.projection_attempt_seconds:
            raise ValueError('Stale threshold must exceed checkpoint and retry envelope')
        if self.projection_success_days > self.projection_exceptional_days:
            raise ValueError('Exceptional retention must not be shorter')
        if self.cleanup_timeout_seconds >= self.turn_timeout_seconds:
            raise ValueError('Cleanup reserve must be smaller than turn deadline')
        if self.heartbeat_seconds >= self.watchdog_stall_seconds:
            raise ValueError('Heartbeat must be shorter than stall threshold')
        if self.trace_sample_rate != 1:
            raise ValueError('M0 stabilization policy requires 100% trace capture')
        if self.otel_enabled and not self.observability_enabled:
            raise ValueError('OTel requires observability enabled')
        if self.otel_enabled and self.otel_exporter_mode == 'otlp' and self.otel_exporter_otlp_endpoint is None:
            raise ValueError('Enabled OTel requires explicit endpoint')
        if self.finops_enabled and self.otel_environment not in ('local','development','staging','production'):
            raise ValueError('Accounting requires a canonical environment')
        if self.otel_environment == 'production' and self.otel_enabled and self.otel_service_name != 'slopanoc':
            raise ValueError('Production service name must be slopanoc')
        if self.otel_batch_size > self.otel_queue_capacity:
            raise ValueError('Batch exceeds queue capacity')
        if self.otel_enabled and self.otel_environment == 'production' and self.otel_exporter_mode != 'otlp':
            raise ValueError('Production requires OTLP')
        if self.otel_exporter_otlp_endpoint is not None:
            endpoint = self.otel_exporter_otlp_endpoint.get_secret_value()
            parsed = urlsplit(endpoint)
            if (parsed.scheme not in {'http', 'https'} or not parsed.hostname
                    or parsed.username or parsed.password or parsed.query or parsed.fragment
                    or any(c.isspace() for c in endpoint) or parsed.path not in {'', '/'}):
                raise ValueError('Invalid OTLP endpoint')
            if self.otel_enabled and self.otel_environment == 'production' and parsed.scheme != 'https':
                if not (self.otel_cloud_run_sidecar and parsed.hostname in {'127.0.0.1', 'localhost', '::1'}):
                    raise ValueError('Production requires TLS or declared loopback sidecar')
            try:
                if parsed.port == 0:
                    raise ValueError('Invalid port')
                parsed.port
            except ValueError:
                raise ValueError('Invalid OTLP endpoint') from None
        return self

    @classmethod
    def from_settings_mapping(cls, env: Mapping[str, str], *, graph_fallback: float, knowledge_fallback: float):
        values: dict[str, object] = {
            'graph_timeout_seconds': graph_fallback,
            'knowledge_timeout_seconds': cls.model_fields['knowledge_timeout_seconds'].default,
        }
        for suffix in SETTING_NAMES:
            key = 'SLOPANOC_' + suffix
            if key not in env:
                continue
            raw = env[key]
            if suffix.endswith('_ENABLED') or suffix in {'TRACE_ERRORS_ALWAYS', 'TRACE_GOVERNED_TURNS_ALWAYS', 'OTEL_CLOUD_RUN_SIDECAR'}:
                if raw.strip().lower() not in {'true', 'false'}:
                    raise ValueError('Invalid boolean configuration')
                values[suffix.lower()] = raw.strip().lower() == 'true'
            else:
                values[suffix.lower()] = raw
        if 'otel_cloud_run_revision' not in values and env.get('K_REVISION'):
            values['otel_cloud_run_revision'] = env['K_REVISION']
        if 'gateway_timeout_seconds' not in values:
            values['gateway_timeout_seconds'] = values['graph_timeout_seconds']
        if 'database_acquire_timeout_seconds' not in values and 'database_timeout_seconds' in values:
            values['database_acquire_timeout_seconds'] = values['database_timeout_seconds']
        if 'database_query_timeout_seconds' not in values and 'database_timeout_seconds' in values:
            values['database_query_timeout_seconds'] = values['database_timeout_seconds']
        config = cls.model_validate(values)
        if config.otel_enabled and any(env.get(key) for key in (
                'OTEL_PYTHON_TRACER_PROVIDER', 'OTEL_PYTHON_METER_PROVIDER', 'OTEL_PYTHON_LOGGER_PROVIDER')):
            raise ValueError('Implicit provider ownership is prohibited')
        return config

    def effective_configuration(self):
        """Internal read-only projection. Endpoint/credentials never leave config."""
        import hashlib
        import json
        from .schemas import EffectiveConfiguration, ConfigurationOwner, TELEMETRY_SCHEMA_VERSION
        values = dict(observability_enabled=self.observability_enabled, otel_enabled=self.otel_enabled,
            exporter_mode=self.otel_exporter_mode if self.otel_enabled else 'disabled',
            collector_endpoint_configured=self.otel_exporter_otlp_endpoint is not None,
            service_name=self.otel_service_name, environment=self.otel_environment,
            telemetry_schema_version=TELEMETRY_SCHEMA_VERSION)
        values.update({name: getattr(self, name) for name in type(self).model_fields
            if name.endswith('_timeout_seconds') and not name.startswith('otel_')})
        values.update({name: getattr(self, name) for name in (
            'heartbeat_seconds', 'watchdog_stall_seconds',
            'retry_minimum_seconds', 'watchdog_check_seconds', 'blocking_workers',
            'blocking_pending', 'business_queue_capacity', 'business_queue_bytes')})
        values.update({name:getattr(self,name) for name in type(self).model_fields if name.startswith('projection_')})
        version = hashlib.sha256(json.dumps(values, sort_keys=True).encode()).hexdigest()[:16]
        return tuple(EffectiveConfiguration(setting=k, value=v,
            owner=ConfigurationOwner.ENVIRONMENT, config_version=version) for k,v in values.items())
