"""Canonical dry-run alert conditions and generated single-condition PromQL."""
from dataclasses import dataclass
from decimal import Decimal
from .slo_contract import DEFINITIONS,BURN_TIERS,SAFETY_KINDS,State,WINDOW_SECONDS

POLICY_STATUS='PROVISIONAL — CALIBRATION REQUIRED'
@dataclass(frozen=True)
class Condition:
    alert_id: str
    severity: str
    action: str
    active: bool
    group: str
    runbook: str
    source_status: str = 'FRESH'


def burn_conditions(result,environment):
    usable=result['state'] in (State.HEALTHY.value,State.BREACHED.value,State.BURNING.value)
    return [Condition(result['slo_id']+'_page','CRITICAL','PAGE',usable and bool(set(result['burn_tiers'])&{'fast','sustained'}),environment+':service-burn',result['runbook']),
        Condition(result['slo_id']+'_ticket','HIGH','TICKET',usable and 'slow' in result['burn_tiers'],environment+':service-burn',result['runbook'])]


def safety_condition(kind,confirmed=False,environment='development'):
    if kind not in SAFETY_KINDS:raise ValueError('Unregistered safety condition')
    return Condition('safety_'+kind,'CRITICAL','PAGE',confirmed,environment+':safety:'+kind,'docs/Telemetry/runbooks/safety_violation.md')


def confirm_safety(runtime,kind):
    """Trusted producer API accepts only bounded condition code, never payload/content."""
    condition=safety_condition(kind,True,runtime.config.otel_environment)
    from .slo_metrics import record
    record(runtime,'slopanoc.safety.violations',1,kind,'CONFIRMED')
    return condition

class DryRun:
    def __init__(self):self.open={}
    def update(self,conditions,*,fresh=True):
        # Source absence is not recovery. No network notification is ever sent.
        if not fresh:return {'opened':(),'resolved':(),'notifications':(),'source':'UNKNOWN'}
        opened=[];resolved=[]
        for c in conditions:
            if c.active and c.alert_id not in self.open: self.open[c.alert_id]=c;opened.append(c.alert_id)
            elif not c.active and c.alert_id in self.open:del self.open[c.alert_id];resolved.append(c.alert_id)
        groups={}
        for c in self.open.values():
            old=groups.get(c.group)
            if old is None or (c.action=='PAGE' and old.action!='PAGE'):groups[c.group]=c
        return {'opened':tuple(opened),'resolved':tuple(resolved),'notifications':tuple(c.alert_id for c in groups.values()),'source':'FRESH'}


def selector(metric,slo_id,seconds):
    return 'max by (environment, operation) ({__name__="workload.googleapis.com/slopanoc.slo.'+metric+'",operation="'+slo_id+'",window="'+str(seconds)+'",status=~"HEALTHY|BREACHED|BURNING"})'


def promql(slo_id,action):
    d=DEFINITIONS[slo_id]
    if d.objective==1:raise ValueError('No finite burn for 100% objective')
    pairs=[]
    for tier in BURN_TIERS:
        if tier.action!=action:continue
        parts=[]
        for seconds in (tier.long_seconds,tier.short_seconds):
            parts.append('('+selector('bad_fraction',slo_id,seconds)+' / '+str(Decimal(1)-Decimal(str(d.objective)))+' > '+str(tier.threshold)+')')
            parts.append('('+selector('eligible',slo_id,seconds)+' >= '+str(tier.minimum_events)+')')
            parts.append('('+selector('unknown',slo_id,seconds)+' == 0)')
        pairs.append('('+' and on (environment, operation) '.join(parts)+')')
    combined='('+' or on (environment, operation) '.join(pairs)+')'
    return combined+' and on (environment, operation) ('+selector('source_age',slo_id,WINDOW_SECONDS)+' <= '+str(d.freshness_seconds)+')'

SIGNAL_ALERTS={
    'terminal_integrity':('CRITICAL','PAGE','terminal_integrity','missing/late/duplicate canonical terminal evidence'),
    'hard_safety':('CRITICAL','PAGE','safety_violation','any confirmed bounded safety violation'),
    'timeout_stall_spike':('HIGH','TICKET','timeout_stall','timeouts >5%/15m with20turns or sustained stalled work'),
    'model_degradation':('HIGH','TICKET','model_provider','model reliability burn / sustained provider failure'),
    'gateway_degradation':('HIGH','TICKET','power_automate_gateway','Power Automate reliability burn / sustained throttling'),
    'db_acquisition_degradation':('HIGH','TICKET','database_pool','pool exhaustion >=3/5m plus acquisition saturation'),
    'persistence_degradation':('HIGH','TICKET','diagnostic_persistence','failed/rejected/terminal_unpersisted with pending work'),
    'telemetry_export_failure':('HIGH','TICKET','telemetry_exporter','sustained exporter loss/queue pressure5m'),
    'trace_completeness':('HIGH','TICKET','trace_completeness','structural completeness burn'),
    'sse_delivery':('HIGH','TICKET','sse_delivery','eligible receipt burn / server delivery failure'),
    'queue_saturation':('HIGH','TICKET','timeout_stall','>=3confirmed saturation events/5m'),
    'infrastructure_capacity':('HIGH','TICKET','infrastructure_capacity','platform capacity baseline pending'),
    'planning_source_gaps':('MEDIUM','INFORMATIONAL','planning_source_gaps','sustained baseline deviation; safe gap not availability failure'),
    'synthetic_failure':('HIGH','TICKET','synthetic_checks','3consecutive safe synthetic failures'),
}

@dataclass(frozen=True)
class Signals:
    environment: str = 'development'
    available: bool = True
    fresh: bool = True
    turns: int = 0
    timeouts: int = 0
    terminal_integrity_failures: int = 0
    model_calls: int = 0
    model_failures: int = 0
    gateway_calls: int = 0
    gateway_failures: int = 0
    pool_exhaustions: int = 0
    persistence_failures: int = 0
    pending_persistence: int = 0
    exporter_degraded_seconds: int = 0
    trace_calls: int = 0
    trace_incomplete: int = 0
    sse_calls: int = 0
    sse_missing: int = 0
    queue_failures: int = 0
    synthetic_consecutive_failures: int = 0
    safety: frozenset[str] = frozenset()
    def __post_init__(self):
        if not self.safety<=SAFETY_KINDS:raise ValueError('Unregistered safety marker')
        for key,value in self.__dict__.items():
            if key not in ('environment','available','fresh','safety') and (type(value) is not int or value<0):raise ValueError('Invalid signal count')


def signal_conditions(s):
    active={
        'terminal_integrity':s.terminal_integrity_failures>0,
        'hard_safety':bool(s.safety),
        'timeout_stall_spike':s.turns>=20 and s.timeouts/s.turns>.05,
        'model_degradation':s.model_calls>=100 and s.model_failures/s.model_calls>.005,
        'gateway_degradation':s.gateway_calls>=100 and s.gateway_failures/s.gateway_calls>.005,
        'db_acquisition_degradation':s.pool_exhaustions>=3,
        'persistence_degradation':s.persistence_failures>0 and s.pending_persistence>0,
        'telemetry_export_failure':s.exporter_degraded_seconds>=300,
        'trace_completeness':s.trace_calls>=100 and s.trace_incomplete/s.trace_calls>.001,
        'sse_delivery':s.sse_calls>=100 and s.sse_missing/s.sse_calls>.001,
        'queue_saturation':s.queue_failures>=3,
        'synthetic_failure':s.synthetic_consecutive_failures>=3,
    }
    return [Condition(key,sev,action,bool(active.get(key,False) and (key=='hard_safety' or s.available and s.fresh)),s.environment+(':'+key if key in ('hard_safety','terminal_integrity') else ':service-symptoms'),'docs/Telemetry/runbooks/'+book+'.md', 'FRESH' if s.available and s.fresh else 'UNKNOWN') for key,(sev,action,book,_) in SIGNAL_ALERTS.items()]
