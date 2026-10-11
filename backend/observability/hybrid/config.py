"""Disabled-default validated settings; explicit environment parsing, no clients."""
from pydantic import Field, StrictBool, model_validator
from ..schemas import Contract
from .contracts import Environment

class HybridConfig(Contract):
    environment: Environment = 'local'
    audit_publication_enabled: StrictBool = False
    operational_pubsub_enabled: StrictBool = False
    projector_enabled: StrictBool = False
    capture_enabled: StrictBool = False
    execution_journal_enabled: StrictBool = False
    controlled_egress_enabled: StrictBool = False
    transport_queue_depth: int = Field(default=128, strict=True, ge=1, le=4096)
    retry_initial_seconds: int = Field(default=1, strict=True, ge=1, le=60)
    retry_max_seconds: int = Field(default=60, strict=True, ge=1, le=300)
    shutdown_seconds: int = Field(default=3, strict=True, ge=1, le=30)
    project: str | None = Field(default=None, pattern=r'^[a-z][a-z0-9-]{4,61}[a-z0-9]$')
    audit_topic: str | None = None
    telemetry_topic: str | None = None
    publisher_batch_size: int = Field(default=100, strict=True, ge=1, le=1000)
    publisher_lease_seconds: int = Field(default=30, strict=True, ge=5, le=300)
    publisher_max_attempts: int = Field(default=10, strict=True, ge=1, le=100)
    projector_max_inflight: int = Field(default=32, strict=True, ge=1, le=256)
    projector_deadline_seconds: int = Field(default=10, strict=True, ge=1, le=60)

    @model_validator(mode='after')
    def bindings(self):
        import re
        if self.execution_journal_enabled and not self.capture_enabled:
            raise ValueError('Execution journal requires canonical capture')
        if self.controlled_egress_enabled and not self.execution_journal_enabled:
            raise ValueError('Controlled egress requires execution journal')
        if self.retry_initial_seconds > self.retry_max_seconds:
            raise ValueError('Retry bounds')
        for topic, lane in ((self.audit_topic, 'audit'), (self.telemetry_topic, 'telemetry')):
            if topic is not None and (self.project is None or not re.fullmatch(
                rf'projects/{re.escape(self.project)}/topics/slopanoc-{self.environment}-{lane}-events-v1', topic)):
                raise ValueError('Environment-specific topic binding rejected')
        if self.audit_publication_enabled and self.audit_topic is None:
            raise ValueError('Audit topic required')
        if self.operational_pubsub_enabled and self.telemetry_topic is None:
            raise ValueError('Telemetry topic required')
        if self.projector_enabled and self.audit_topic is None:
            raise ValueError('Audit projector lane required')
        return self

    publish_concurrency: int = Field(default=4, strict=True, ge=1, le=32)
    publish_timeout_seconds: int = Field(default=2, strict=True, ge=1, le=10)
    push_audience: str | None = Field(default=None, pattern=r'^https://[A-Za-z0-9.-]+/?$')
    push_service_account: str | None = Field(default=None, pattern=r'^[a-z][a-z0-9-]{4,28}[a-z0-9]@[a-z][a-z0-9-]+\.iam\.gserviceaccount\.com$')

    @classmethod
    def from_environment(cls, values=None):
        import os
        values = os.environ if values is None else values
        names = {'EXECUTION_JOURNAL_ENABLED':'execution_journal_enabled','CONTROLLED_EGRESS_ENABLED':'controlled_egress_enabled','AUDIT_ENABLED':'capture_enabled', 'PUBLISHER_ENABLED':'audit_publication_enabled',
            'PROJECTOR_ENABLED':'projector_enabled', 'ENVIRONMENT':'environment',
            'PROJECT':'project', 'AUDIT_TOPIC':'audit_topic', 'PUSH_AUDIENCE':'push_audience',
            'PUSH_SERVICE_ACCOUNT':'push_service_account', 'PUBLISH_CONCURRENCY':'publish_concurrency',
            'PUBLISH_TIMEOUT_SECONDS':'publish_timeout_seconds', 'PUBLISHER_BATCH_SIZE':'publisher_batch_size',
            'PUBLISHER_LEASE_SECONDS':'publisher_lease_seconds', 'PUBLISHER_MAX_ATTEMPTS':'publisher_max_attempts',
            'RETRY_INITIAL_SECONDS':'retry_initial_seconds', 'RETRY_MAX_SECONDS':'retry_max_seconds',
            'PROJECTOR_MAX_INFLIGHT':'projector_max_inflight', 'PROJECTOR_DEADLINE_SECONDS':'projector_deadline_seconds'}
        data={}
        for name, field in names.items():
            value=values.get('SLOPANOC_HYBRID_'+name)
            if value is None:continue
            if name.endswith('ENABLED'):
                if value not in ('true','false'):raise ValueError('Explicit hybrid boolean required')
                value=value=='true'
            elif name.endswith(('SECONDS','SIZE','ATTEMPTS','CONCURRENCY','INFLIGHT')):
                value=int(value)
            data[field]=value
        return cls(**data)
