"""Reader-only configuration; resolving it performs no secret/cloud access."""
import os
from pathlib import Path
from urllib.parse import urlsplit
from pydantic import BaseModel, ConfigDict, Field, SecretStr, model_validator
from .config import ObservabilityConfig


class ReadConfiguration(BaseModel):
    model_config = ConfigDict(extra='forbid', frozen=True)
    database_url: SecretStr | None = None
    pool_size: int = Field(default=2, ge=1, le=8)
    pool_timeout: float = Field(default=1, ge=.05, le=2)
    read_deadline: float = Field(default=2, ge=.05, le=2)
    read_concurrency: int = Field(default=4, ge=1, le=8)
    allowed_origins: tuple[str, ...] = ()
    archive_root: Path | None = None
    # Explicit immutable producer policy snapshot, not reader environment defaults.
    producer_policy_file: Path | None = None
    billing_scope: str | None = Field(default=None, pattern=r'^[0-9a-f]{64}$')
    pricing_scope: str | None = Field(default=None, pattern=r'^[0-9a-f]{64}$')
    focus_scope: str | None = Field(default=None, pattern=r'^[0-9a-f]{64}$')
    source_mode: str = Field(default='BIGQUERY', pattern=r'^(BIGQUERY|TEST_FIXTURE)$')

    @model_validator(mode='after')
    def origins(self):
        if len(self.allowed_origins)>16 or len(set(self.allowed_origins))!=len(self.allowed_origins):
            raise ValueError('Invalid reader origins')
        for origin in self.allowed_origins:
            p=urlsplit(origin)
            if (p.scheme not in ('http','https') or not p.hostname or p.username or p.password
                or p.path or p.query or p.fragment or '*' in origin or any(c.isspace() for c in origin)):
                raise ValueError('Invalid reader origin')
            if p.port == 0: raise ValueError('Invalid reader origin')
        return self

    @classmethod
    def from_environment(cls, env=None, *, environment='local'):
        env=os.environ if env is None else env
        values={}
        for key in ('DATABASE_URL','POOL_SIZE','POOL_TIMEOUT','READ_DEADLINE','READ_CONCURRENCY',
                    'ARCHIVE_ROOT','PRODUCER_POLICY_FILE','BILLING_SCOPE','PRICING_SCOPE','FOCUS_SCOPE','SOURCE_MODE'):
            value=env.get('SLOPANOC_OBSERVABILITY_READ_'+key)
            if value: values[key.lower()]=value
        if 'database_url' not in values and environment in ('local','development','test'):
            # Explicit local URL only: no Secret Manager fallback or credential discovery.
            values['database_url']=env.get('SLOPANOC_DATABASE_URL') or None
        values['allowed_origins']=tuple(filter(None,env.get('SLOPANOC_OBSERVABILITY_ALLOWED_ORIGINS','').split(',')))
        result=cls(**values)
        if environment in ('production','staging') and (not result.database_url or result.source_mode!='BIGQUERY'
            or any(not v.startswith('https://') for v in result.allowed_origins)):
            raise ValueError('Explicit secure production reader bindings required')
        return result


def producer_policy(settings, environment):
    if settings.producer_policy_file is None: return None
    path=settings.producer_policy_file
    if path.is_symlink() or path.stat().st_size>65536: raise ValueError('Invalid producer policy snapshot')
    policy=ObservabilityConfig.model_validate_json(path.read_bytes())
    if policy.otel_environment!=environment: raise ValueError('Producer policy environment mismatch')
    return policy
