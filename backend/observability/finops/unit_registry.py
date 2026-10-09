"""One finite immutable definition registry for all M13 analytical views."""
from typing import Literal
from pydantic import AwareDatetime, Field, model_validator
from ..schemas import Contract
from .unit_economics_contracts import Version, BEGIN, END
from .billing_contracts import digest

class Unit(Contract):
    unit_id: Version
    name: str
    denominator_source: Literal['M9', 'M10', 'M19', 'ITSM', 'UNAVAILABLE']
    eligibility: str
    operational_scope: Literal['PROVIDER_POPULATION', 'USER_TURN_WORKLOAD', 'REQUEST_CLASS', 'OUTCOME']
    required_financial_scope: Literal['PROVIDER_POPULATION', 'USER_TURN_WORKLOAD', 'REQUEST_CLASS', 'OUTCOME']
    allowed_numerators: tuple[Literal['DIRECT', 'DIRECT_PLUS_APPROVED_SHARED'], ...] = ('DIRECT', 'DIRECT_PLUS_APPROVED_SHARED')
    permitted_drivers: tuple[Literal['ACCEPTED_TURNS', 'PROVIDER_ATTEMPTS'], ...]
    version: Literal['units-v1'] = 'units-v1'
    availability: Literal['CONDITIONAL', 'DEFINED_NOT_AVAILABLE'] = 'CONDITIONAL'
    required_coverage: Literal['COMPLETE'] = 'COMPLETE'
    effective_from: AwareDatetime = BEGIN
    effective_to: AwareDatetime = END
    yield_adjusted: bool = False

def unit(key, name, source, scope, eligibility, deferred=False, yield_adjusted=False):
    return Unit(unit_id=key, name=name, denominator_source=source,
        operational_scope=scope, required_financial_scope=scope, eligibility=eligibility,
        permitted_drivers=('PROVIDER_ATTEMPTS',) if source=='M10' else ('ACCEPTED_TURNS',) if source=='M9' else (),
        availability='DEFINED_NOT_AVAILABLE' if deferred else 'CONDITIONAL', yield_adjusted=yield_adjusted)

UNITS = (
    unit('PROVIDER_ATTEMPT', 'Provider attempt', 'M10', 'PROVIDER_POPULATION', 'Distinct started physical provider attempts in UTC usage-start day; retries are separate attempts.'),
    unit('MODEL_OPERATION', 'Model operation', 'M10', 'PROVIDER_POPULATION', 'Distinct logical model calls with provider activity in UTC usage-start day; no exact operation billing lineage.'),
    unit('ACCEPTED_TURN', 'Accepted turn', 'M9', 'USER_TURN_WORKLOAD', 'Distinct accepted valid canonical turns in UTC acceptance day; includes post-acceptance user cancellations.'),
    unit('AVAILABLE_TURN', 'Available turn', 'M9', 'USER_TURN_WORKLOAD', 'M9 availability good outcome in acceptance-day population; total eligible workload cost per good outcome.', yield_adjusted=True),
    unit('TIMELY_TERMINAL_TURN', 'Timely terminal turn', 'M9', 'USER_TURN_WORKLOAD', 'M9 exactly one terminal state by deadline in acceptance-day population; includes failed and cancelled terminals.', yield_adjusted=True),
    *(unit(c+'_TURN', n, 'M9', 'REQUEST_CLASS', 'Accepted turns with canonical M9 '+c+' class, attributed to UTC acceptance day.') for c,n in (
        ('GENERAL','General turn'), ('TEAMS_LOOKUP','Teams lookup turn'), ('GOVERNED_TROUBLESHOOTING','Governed troubleshooting turn'), ('COMPLEX_MULTI_AGENT','Complex multi-agent turn'))),
    *(unit(k,n,s,'OUTCOME','Not available — authoritative outcome data and compatible cost ownership required.', True) for k,n,s in (
        ('RESOLVED_INCIDENT','Resolved incident','M19'),('FIRST_TIME_CORRECT','First-time-correct outcome','M19'),
        ('ACCEPTED_RECOMMENDATION','Accepted recommendation','M19'),('SUCCESSFUL_REMEDIATION','Successful remediation','M19'),
        ('AUTOMATION_ARTIFACT','Successful automation artifact','M19'),('GOVERNED_ANSWER','Governed answer','M19'),
        ('TICKET','Ticket','ITSM'),('TROUBLESHOOTING_CASE','Troubleshooting case','M19'),
        ('AGENT_INVOCATION','Agent invocation','UNAVAILABLE'),('TOOL_INVOCATION','Tool invocation','UNAVAILABLE'),
        ('KNOWLEDGE_RETRIEVAL','Knowledge retrieval','UNAVAILABLE'))),
)
IDS = frozenset(u.unit_id for u in UNITS)

class Registry(Contract):
    version: Literal['units-v1'] = 'units-v1'
    units: tuple[Unit, ...] = UNITS
    @model_validator(mode='after')
    def canonical(self):
        if self.units != UNITS:
            raise ValueError('Noncanonical unit definitions')
        return self
    @property
    def identity(self):
        return digest(self.model_dump(mode='json'))
