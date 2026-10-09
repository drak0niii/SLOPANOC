"""Reviewed application-owned budgets; immutable revisions, empty configuration valid."""
from datetime import date
from typing import Literal
from pydantic import Field, AwareDatetime, model_validator
from ..schemas import Contract
from .control_contracts import Money, Policy
from .unit_economics_contracts import Hash, Version, Environment
from .billing_contracts import decimal, digest

class Budget(Contract):
    budget_id: Version
    version: Version
    environment: Environment
    financial_binding: Hash
    scope: Version='TOTAL'
    currency: str=Field(pattern=r'^[A-Z]{3}$')
    amount: Money
    calendar_period: Literal['CALENDAR_MONTH']='CALENDAR_MONTH'
    basis: Literal['UTC_USAGE']='UTC_USAGE'
    effective_from: date
    effective_to: date
    recorded_at: AwareDatetime
    enabled: bool=True
    status: Literal['PROVISIONAL']='PROVISIONAL'
    provenance: Literal['REVIEWED_APPLICATION_CONFIG']='REVIEWED_APPLICATION_CONFIG'
    provenance_digest: Hash
    policy_version: Version='control-policy-v1'
    supersedes: Version|None=None
    @model_validator(mode='after')
    def valid(self):
        if decimal(self.amount)<0 or self.effective_from>=self.effective_to or self.effective_from.day!=1 or self.effective_to.day!=1:raise ValueError('Invalid budget')
        if self.supersedes==self.version:raise ValueError('Invalid revision')
        return self
    @property
    def identity(self):return digest(self.model_dump(mode='json'))

class Registry(Contract):
    version: Version='budgets-v1'
    budgets: tuple[Budget,...]=Field(default=(),max_length=100)
    @model_validator(mode='after')
    def valid(self):
        ids=set()
        for a in self.budgets:
            if (a.budget_id,a.version) in ids:raise ValueError('Duplicate version')
            ids.add((a.budget_id,a.version))
            for b in self.budgets:
                if a is b or a.budget_id!=b.budget_id or not a.enabled or not b.enabled:continue
                if a.environment!=b.environment:raise ValueError('Budget identity crosses environment')
                overlap=max(a.effective_from,b.effective_from)<min(a.effective_to,b.effective_to)
                if overlap and a.supersedes!=b.version and b.supersedes!=a.version:raise ValueError('Conflicting budgets')
        return self
    def active(self,environment,month,issue):
        from .control_contracts import month_bounds
        start,end=month_bounds(month)
        selected=[b for b in self.budgets if b.enabled and b.environment==environment and b.recorded_at<=issue and b.effective_from<=start and end<=b.effective_to]
        superseded={b.supersedes for b in selected}
        return tuple(b for b in selected if b.version not in superseded or not any(x.budget_id==b.budget_id and x.supersedes==b.version for x in selected))

def load(path):
    from pathlib import Path
    return Registry.model_validate_json(Path(path).read_text())
