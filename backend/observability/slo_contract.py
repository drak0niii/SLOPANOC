"""Canonical M9 SRE policy. No lifecycle, provider calls or financial accounting."""
from dataclasses import dataclass
from enum import Enum
from types import MappingProxyType

VERSION = 1
WINDOW_SECONDS = 28 * 86400
COST_COVERAGE_WINDOW_SECONDS = 86400
COST_COVERAGE_THRESHOLD = .9999
FRESHNESS_SECONDS = 300
RECEIPT_GRACE_SECONDS = 30
SETTLEMENT_GRACE_SECONDS = 120
DURATION_BOUNDS = (0,.05,.1,.25,.5,1,2,3,5,8,10,15,20,25,30,40,45,60,90,120,180,300,600)

class RequestClass(str, Enum):
    GENERAL = 'GENERAL'
    TEAMS_LOOKUP = 'TEAMS_LOOKUP'
    GOVERNED_TROUBLESHOOTING = 'GOVERNED_TROUBLESHOOTING'
    COMPLEX_MULTI_AGENT = 'COMPLEX_MULTI_AGENT'
    UNKNOWN = 'UNKNOWN'

class CancelOrigin(str, Enum):
    USER = 'USER_OR_CLIENT_CANCEL'
    SERVICE = 'SERVICE_CANCEL'
    DEADLINE = 'DEADLINE_TIMEOUT'
    FAILURE = 'SYSTEM_FAILURE'
    UNKNOWN = 'UNKNOWN_CANCEL_ORIGIN'

class State(str, Enum):
    HEALTHY = 'HEALTHY'
    BREACHED = 'BREACHED'
    BURNING = 'BURNING'
    INSUFFICIENT = 'INSUFFICIENT_DATA'
    UNAVAILABLE = 'DATA_SOURCE_UNAVAILABLE'
    STALE = 'STALE_DATA'
    DEFINED = 'DEFINED_NOT_EVALUATED'

@dataclass(frozen=True)
class Definition:
    slo_id: str
    name: str
    description: str
    objective: float
    population: str
    good_condition: str
    bad_condition: str
    exclusions: tuple[str, ...]
    source: str = 'SRE_ROLLUPS'
    threshold_seconds: float | None = None
    minimum_events: int = 1
    freshness_seconds: int = FRESHNESS_SECONDS
    window_seconds: int = WINDOW_SECONDS
    objective_status: str = 'PROVISIONAL'
    sli_type: str = 'EVENT_RATIO'
    runbook: str = 'availability_burn'
    formal: bool = True

_DEFS = [
    Definition('availability','Availability','Valid governed application outcome, independent of HTTP status.',.995,
        'accepted valid turns','valid completed answer/clarification/source gap/approval request/safe rejection',
        'service failure/timeout/service cancellation/unknown cancellation or missing outcome',
        ('invalid or unauthorized before execution','confirmed user cancellation')),
    Definition('terminal_completion','Terminal completion','Exactly one canonical closure within recorded maximum deadline.',.999,
        'accepted valid turns','one COMPLETED/FAILED/TIMEOUT/CANCELLED at or before total deadline',
        'missing, duplicate or late terminal',('not accepted',),runbook='terminal_integrity'),
]
for ident,name,seconds in [('general','General latency',8),('teams','Teams lookup latency',20),
                          ('troubleshooting','Governed troubleshooting latency',30),('complex','Complex multi-agent latency',45)]:
    _DEFS.append(Definition('latency_'+ident,name,'Canonical M2 acceptance to backend terminal closure; 95% good event semantics.',.95,
        'classified terminal turns with measured root duration','duration <= threshold','duration > threshold',
        ('unknown class','unmeasured duration'),threshold_seconds=seconds,minimum_events=20,runbook='timeout_stall'))
_DEFS += [
    Definition('model_ttft','Streaming model TTFT','M3 first_provider_output, not literal token or UI paint.',.95,
        'streaming user provider operations with supported TTFT','first_provider_output <= threshold','first_provider_output > threshold',
        ('nonstreaming','unknown TTFT','non-user workload'),threshold_seconds=5,minimum_events=20,runbook='model_provider'),
    Definition('trace_completeness','Trace completeness','Unsampled instrumentation structure, independently of export/retention.',.999,
        'terminal turns','one correlated closed root and all applicable component/phase evidence','missing applicable structure',
        ('unused optional components',),runbook='trace_completeness'),
    Definition('sse_delivery','SSE delivery','Browser received message.completed; diagnostic only.',.999,
        'connected SSE turns expecting completion','authenticated idempotent browser completion receipt','missing eligible receipt or confirmed relay/backpressure failure',
        ('sync consumer','confirmed client disconnect before completion expected'),runbook='sse_delivery'),
    Definition('model_reliability','Model reliability','Physical provider transport operations, not validation or logical retries.',.995,
        'provider calls','COMPLETED provider operation','provider failure/timeout/service or unknown cancellation',
        ('confirmed client cancellation',),runbook='model_provider'),
]
for key,name in [('gateway','Power Automate / Teams gateway'),('database','Database'),('storage','Storage'),('knowledge','Knowledge'),('graph','Direct Microsoft Graph')]:
    _DEFS.append(Definition('dependency_'+key,name+' reliability','Distinct physical operation population.',.995,
        name+' operations','COMPLETED operation','failure/timeout/service or unknown cancellation',
        ('confirmed client cancellation','transaction/acquisition groups'),source='DIRECT_GRAPH_UNAVAILABLE' if key=='graph' else 'SRE_ROLLUPS',runbook='power_automate_gateway' if key=='gateway' else 'database_pool' if key=='database' else 'infrastructure_capacity'))
_DEFS.append(Definition('cost_ledger_completeness','Cost-ledger completeness','Exactly one persisted accounting event per billable operation; M10 owns the source.',1.,
    'billable provider operations','exactly one persisted accounting usage event','missing or duplicate persisted accounting event',(),source='M10_COST_LEDGER',objective_status='ARCHITECTURAL',runbook='cost_ledger_completeness'))
DEFINITIONS = MappingProxyType({d.slo_id:d for d in _DEFS})
LATENCY_IDS = MappingProxyType(dict(zip((RequestClass.GENERAL,RequestClass.TEAMS_LOOKUP,RequestClass.GOVERNED_TROUBLESHOOTING,RequestClass.COMPLEX_MULTI_AGENT),('latency_general','latency_teams','latency_troubleshooting','latency_complex'))))
SAFETY_KINDS = frozenset({'unauthorized_command_exposure','command_egress_bypass','credential_telemetry_leak','hidden_cot_persistence'})

@dataclass(frozen=True)
class BurnTier:
    name: str
    long_seconds: int
    short_seconds: int
    threshold: float
    action: str
    minimum_events: int = 100

# Latest user implementation correction overrides doc21's earlier fast1h/6h proposal.
BURN_TIERS = (BurnTier('fast',3600,300,14.4,'PAGE'),BurnTier('sustained',21600,1800,6.,'PAGE'),BurnTier('slow',259200,21600,1.,'TICKET'))
BURN_WINDOWS = tuple(sorted({s for tier in BURN_TIERS for s in (tier.long_seconds,tier.short_seconds)}))


def classify(*, specialists=(), governed=False, teams_lookup=False, general=False):
    """Uses server route/topology facts only; UNKNOWN is never defaulted to general."""
    if len(set(specialists) & {'technical_authority_engineer','incident_manager','problem_manager','automated_operations_engineer'}) >= 2:
        return RequestClass.COMPLEX_MULTI_AGENT
    if governed: return RequestClass.GOVERNED_TROUBLESHOOTING
    if teams_lookup: return RequestClass.TEAMS_LOOKUP
    if general: return RequestClass.GENERAL
    return RequestClass.UNKNOWN
