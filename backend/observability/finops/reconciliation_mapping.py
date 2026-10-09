"""Immutable effective-dated model/component and authoritative ownership rules."""
from datetime import datetime
from typing import Literal
from pydantic import Field, AwareDatetime, model_validator
from ..schemas import Contract
from .contracts import QUANTITIES, ENVIRONMENTS
from .billing_contracts import digest, instant, SourceError

class Mapping(Contract):
    provider: Literal['gcp.vertex_ai','gcp.gemini','other']
    service: str=Field(pattern=r'^[a-f0-9]{64}$')
    sku: str=Field(pattern=r'^[a-f0-9]{64}$')
    project: str=Field(pattern=r'^[a-f0-9]{64}$')
    model: str=Field(default='other',pattern=r'^(other|(?:gemini-|text-embedding-|embedding-)[A-Za-z0-9_.-]{1,100})$')
    field: Literal['input_tokens','output_tokens','candidate_tokens','cached_tokens','total_tokens','thought_tokens','tool_input_tokens','billable_characters']|None=None
    unit: Literal['tokens','characters','requests','seconds','bytes','byte-seconds']='tokens'
    region: str=Field(pattern=r'^[A-Za-z0-9_.-]{1,64}$')
    consumption_model: str|None=Field(default=None,pattern=r'^[a-f0-9]{64}$')
    operation_type: Literal['GENERATION','EMBEDDING']='GENERATION'
    effective_from: AwareDatetime
    effective_to: AwareDatetime
    classification: Literal['DIRECT','SHARED']
    evidence: Literal['DEDICATED_WORKLOAD','DEDICATED_RESOURCE','SUPPORTING_POOL']
    category: Literal['MODEL_AI','CLOUD_RUN','CLOUD_SQL','BIGQUERY','GCS','MONITORING_LOGGING','NETWORK','OTHER']='MODEL_AI'
    @model_validator(mode='after')
    def valid(self):
        if any(v in (digest(''),digest('UNKNOWN')) for v in (self.service,self.sku,self.project)):raise ValueError('Missing ownership identity')
        if instant(self.effective_from)>=instant(self.effective_to):raise ValueError('Invalid mapping period')
        if (self.classification=='SHARED')!=(self.evidence=='SUPPORTING_POOL'):raise ValueError('Invalid ownership')
        if self.field and self.evidence!='DEDICATED_WORKLOAD':raise ValueError('Invalid quantity evidence')
        if self.field and self.unit!=('characters' if self.field=='billable_characters' else 'tokens'):
            raise ValueError('Invalid quantity unit')
        return self

class Registry(Contract):
    version: str=Field(default='map-v1',pattern=r'^[A-Za-z0-9_.-]{1,32}$')
    ownership_version: str=Field(default='ownership-v1',pattern=r'^[A-Za-z0-9_.-]{1,32}$')
    environment: Literal['local','development','staging','production']='local'
    # Trusted deployment attestation, not a project identity fabricated in M10.
    runtime_scope: str|None=Field(default=None,pattern=r'^[a-f0-9]{64}$')
    mappings: tuple[Mapping,...]=Field(default=(),max_length=1000)
    @model_validator(mode='after')
    def validate_overlap(self):
        for i,a in enumerate(self.mappings):
            for b in self.mappings[i+1:]:
                if max(a.effective_from,b.effective_from)>=min(a.effective_to,b.effective_to):continue
                if (a.service,a.sku,a.project,a.region)==(b.service,b.sku,b.project,b.region):
                    raise ValueError('Overlapping mapping')
                if a.field and b.field and (a.provider,a.model,a.region,a.operation_type)==(b.provider,b.model,b.region,b.operation_type):
                    # One provider quantity cannot be assigned to multiple SKUs;
                    # total and nested token categories cannot coexist either.
                    if a.field==b.field or 'total_tokens' in (a.field,b.field) or {a.field,b.field}<={'input_tokens','cached_tokens','tool_input_tokens'} or {a.field,b.field}<={'output_tokens','candidate_tokens','thought_tokens'}:
                        raise ValueError('Overlapping runtime quantity')
        return self
    @property
    def identity(self):return digest(self.model_dump(mode='json'))
    def lookup(self,bucket,at,end):
        matches=[m for m in self.mappings if (m.service,m.sku,m.project,m.region)==
                 (bucket['service'],bucket['sku'],bucket['project'],bucket['region']) and
                 m.effective_from<=at and end<=m.effective_to and (m.consumption_model is None or m.consumption_model==bucket['consumption_model'])]
        if len(matches)>1:raise SourceError('MAPPING_INVALID')
        return matches[0] if matches else None
