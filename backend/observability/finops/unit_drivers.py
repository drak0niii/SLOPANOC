"""Reviewed deployment-owned scope attestations and SHARED policies; empty by default."""
from decimal import Decimal, localcontext, ROUND_FLOOR
from typing import Literal
from pydantic import AwareDatetime, Field, model_validator
from ..schemas import Contract
from .unit_economics_contracts import Hash, Version, Environment, BEGIN, END
from .billing_contracts import digest, decimal, money, SourceError
from .unit_registry import IDS

Workload = Literal['PROVIDER_POPULATION', 'USER_TURN_WORKLOAD', 'SYSTEM_WARMUP', 'INGESTION', 'BACKGROUND', 'UNKNOWN']

class ScopeRule(Contract):
    service: Hash
    sku: Hash
    project: Hash
    region: str = Field(pattern=r'^[A-Za-z0-9_.-]{1,64}$')
    financial_scope: Workload
    request_class: Literal['GENERAL','TEAMS_LOOKUP','GOVERNED_TROUBLESHOOTING','COMPLEX_MULTI_AGENT'] | None = None
    provenance: Literal['DEPLOYMENT_OWNERSHIP_ATTESTATION'] = 'DEPLOYMENT_OWNERSHIP_ATTESTATION'
    evidence_version: Version
    effective_from: AwareDatetime = BEGIN
    effective_to: AwareDatetime = END
    @model_validator(mode='after')
    def valid(self):
        if self.effective_from >= self.effective_to or self.request_class and self.financial_scope!='USER_TURN_WORKLOAD':
            raise ValueError('Invalid scope attestation')
        if any(x in (digest(''),digest('UNKNOWN')) for x in (self.service,self.sku,self.project)):
            raise ValueError('Missing scope identity')
        return self

class Driver(Contract):
    category: Literal['MODEL_AI','CLOUD_RUN','CLOUD_SQL','BIGQUERY','GCS','MONITORING_LOGGING','NETWORK','OTHER']
    # Exact ownership selector; category alone cannot authorize distribution.
    ownership: ScopeRule
    units: tuple[Version, ...] = Field(min_length=1, max_length=9)
    driver: Literal['ACCEPTED_TURNS', 'PROVIDER_ATTEMPTS']
    version: Version
    provenance: Literal['APPROVED_DEPLOYMENT_POLICY'] = 'APPROVED_DEPLOYMENT_POLICY'
    status: Literal['APPROVED'] = 'APPROVED'
    fallback: Literal['UNDISTRIBUTED_SHARED'] = 'UNDISTRIBUTED_SHARED'
    effective_from: AwareDatetime = BEGIN
    effective_to: AwareDatetime = END
    @model_validator(mode='after')
    def valid(self):
        from .unit_registry import UNITS
        definitions = {u.unit_id:u for u in UNITS}
        if len(set(self.units))!=len(self.units) or not set(self.units)<=IDS or self.effective_from>=self.effective_to:
            raise ValueError('Invalid shared driver')
        if any(self.driver not in definitions[u].permitted_drivers for u in self.units):
            raise ValueError('Incompatible driver')
        if self.driver=='ACCEPTED_TURNS' and self.ownership.financial_scope!='USER_TURN_WORKLOAD':
            raise ValueError('Incompatible driver scope')
        if self.driver=='PROVIDER_ATTEMPTS' and self.ownership.financial_scope!='PROVIDER_POPULATION':
            raise ValueError('Incompatible driver scope')
        return self

def selector(rule):
    return (rule.service, rule.sku, rule.project, rule.region)

def matches(rule, bucket, start, end):
    return selector(rule)==tuple(bucket[k] for k in ('service','sku','project','region')) and rule.effective_from<=start and end<=rule.effective_to

class Registry(Contract):
    environment: Environment = 'local'
    financial_scope: Hash | None = None
    version: Version = 'drivers-v1'
    scope_rules: tuple[ScopeRule, ...] = Field(default=(), max_length=1000)
    drivers: tuple[Driver, ...] = Field(default=(), max_length=1000)
    residual_policy: Literal['LARGEST_REMAINDER_STABLE_KEY_38_V1'] = 'LARGEST_REMAINDER_STABLE_KEY_38_V1'
    @model_validator(mode='after')
    def overlaps(self):
        for rules in (self.scope_rules, self.drivers):
            for i,a in enumerate(rules):
                for b in rules[i+1:]:
                    x=a.ownership if isinstance(a,Driver) else a
                    y=b.ownership if isinstance(b,Driver) else b
                    overlap=max(a.effective_from,b.effective_from,x.effective_from,y.effective_from)<min(a.effective_to,b.effective_to,x.effective_to,y.effective_to)
                    if selector(x)==selector(y) and overlap and (not isinstance(a,Driver) or set(a.units)&set(b.units)):
                        raise ValueError('Conflicting active scope/driver rules')
        return self
    @property
    def identity(self):
        return digest(self.model_dump(mode='json'))

def distribute(amount, weights):
    """Exact sign-preserving largest remainder at source precision; stable tie order."""
    amount=decimal(amount)
    parsed={k:decimal(v) for k,v in weights.items()}
    if not parsed or any(v<0 for v in parsed.values()) or sum(parsed.values())==0:
        return {},money(amount)
    with localcontext() as ctx:
        ctx.prec=100
        quantum=Decimal('1e-38')
        absolute=abs(amount);total=sum(parsed.values());quotas={k:absolute*v/total for k,v in parsed.items()}
        values={k:(v/quantum).to_integral_value(rounding=ROUND_FLOOR)*quantum for k,v in quotas.items()}
        residual=int((absolute-sum(values.values()))/quantum)
        if not 0<=residual<len(values):
            raise SourceError('FINANCIAL_IMBALANCE')
        for k in sorted(values,key=lambda k:(-(quotas[k]-values[k]),k))[:residual]:
            values[k]+=quantum
        sign=-1 if amount<0 else 1
        result={k:money(v*sign) for k,v in sorted(values.items())}
        if sum(map(decimal,result.values()))!=amount:
            raise SourceError('FINANCIAL_IMBALANCE')
        return result,'0'
