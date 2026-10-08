"""One deterministic event-ratio and paired-burn evaluator, shared by API/dry-run."""
from dataclasses import dataclass
from datetime import datetime, timezone, timedelta
from decimal import Decimal
from .slo_contract import DEFINITIONS, BURN_TIERS, BURN_WINDOWS, State, VERSION


def utc(value):
    if value.tzinfo is None: raise ValueError('SRE timestamps must be aware')
    return value.astimezone(timezone.utc)


def minute(value):
    return utc(value).replace(second=0,microsecond=0)

@dataclass(frozen=True)
class Counts:
    good: int = 0
    bad: int = 0
    unknown: int = 0
    excluded: int = 0
    def __post_init__(self):
        if any(type(v) is not int or v<0 for v in (self.good,self.bad,self.unknown,self.excluded)):
            raise ValueError('Invalid SRE counts')
    @property
    def eligible(self): return self.good+self.bad
    def __add__(self, other):
        return Counts(*(getattr(self,k)+getattr(other,k) for k in ('good','bad','unknown','excluded')))

@dataclass(frozen=True)
class Bucket:
    at: datetime
    counts: Counts

@dataclass(frozen=True)
class Snapshot:
    environment: str
    buckets: tuple[Bucket,...] = ()
    updated_at: datetime | None = None
    coverage_start: datetime | None = None
    available: bool = True
    capture_complete: bool = True
    source: str = 'ISOLATED_FIXTURE'
    # Accepted population watermark accounts for outstanding deadlines/ingest grace.
    watermark: datetime | None = None


def budget(counts, objective):
    if not counts.eligible or objective == 1: return (None,None,None)
    allowed=Decimal(counts.eligible)*(1-Decimal(str(objective)))
    consumed=Decimal(counts.bad)/allowed
    return float(allowed),float(consumed),float(1-consumed)


def burn(counts, objective):
    return budget(counts,objective)[1]


def paired(long, short, tier, usable=True):
    return bool(usable and long.eligible>=tier.minimum_events and short.eligible>=tier.minimum_events
        and not long.unknown and not short.unknown)


def evaluate(slo_id, snapshot, *, now):
    definition=DEFINITIONS[slo_id]; now=utc(now)
    end=minute(snapshot.watermark or now); start=end-timedelta(seconds=definition.window_seconds)
    if end>now: raise ValueError('Future SRE watermark')
    accumulated={s:[0,0,0,0] for s in (*BURN_WINDOWS,definition.window_seconds)}
    for item in snapshot.buckets:
        at=utc(item.at)
        if at>=end: continue
        age=(end-at).total_seconds()
        c=item.counts
        for seconds,values in accumulated.items():
            if 0<age<=seconds:
                values[0]+=c.good;values[1]+=c.bad;values[2]+=c.unknown;values[3]+=c.excluded
    totals={seconds:Counts(*values) for seconds,values in accumulated.items()}
    if definition.source in ('M10_COST_LEDGER','DIRECT_GRAPH_UNAVAILABLE'):
        totals={seconds:Counts() for seconds in totals}
    count=totals[definition.window_seconds]
    updated=utc(snapshot.updated_at) if snapshot.updated_at is not None else None
    source_state='AVAILABLE'; reason='FRESH'
    if definition.source=='M10_COST_LEDGER': state=State.DEFINED;source_state='DATA_SOURCE_AVAILABLE_IN_M10';reason=source_state
    elif definition.source=='DIRECT_GRAPH_UNAVAILABLE' or not snapshot.available or updated is None:
        state=State.UNAVAILABLE;source_state='UNAVAILABLE';reason='SOURCE_UNAVAILABLE'
    elif updated>now or (now-updated).total_seconds()>definition.freshness_seconds:
        state=State.STALE;source_state='STALE';reason='STALE_SOURCE'
    elif not snapshot.capture_complete or count.unknown or count.eligible<definition.minimum_events or snapshot.coverage_start is None or utc(snapshot.coverage_start)>start:
        state=State.INSUFFICIENT;reason='INCOMPLETE_COVERAGE' if not snapshot.capture_complete or count.unknown else 'LOW_VOLUME_OR_PARTIAL_WINDOW'
    else: state=State.HEALTHY
    usable=state in (State.HEALTHY,State.BREACHED,State.BURNING)
    windows=[]
    for seconds in BURN_WINDOWS:
        c=totals[seconds]
        windows.append({'seconds':seconds,'eligible':c.eligible,'bad':c.bad,'unknown':c.unknown,'burn_rate':burn(c,definition.objective)})
    active=[]
    for tier in BURN_TIERS:
        lc,sc=totals[tier.long_seconds],totals[tier.short_seconds]
        lb,sb=burn(lc,definition.objective),burn(sc,definition.objective)
        if paired(lc,sc,tier,usable) and lb is not None and sb is not None and lb>tier.threshold and sb>tier.threshold:
            active.append(tier.name)
    value=count.good/count.eligible if count.eligible else None
    if usable:
        if Decimal(count.good)<Decimal(count.eligible)*Decimal(str(definition.objective)): state=State.BREACHED
        elif active: state=State.BURNING
    allowed,consumed,remaining=budget(count,definition.objective)
    if state==State.DEFINED: value=allowed=consumed=remaining=None
    return {'schema_version':VERSION,'slo_id':definition.slo_id,'name':definition.name,
        'description':definition.description,'objective':definition.objective,'objective_status':definition.objective_status,
        'window_seconds':definition.window_seconds,'window_start':start,'window_end':end,
        'population':definition.population,'threshold_seconds':definition.threshold_seconds,
        'eligible':count.eligible,'good':count.good,'bad':count.bad,'unknown':count.unknown,'excluded':count.excluded,
        'current_value':value,'allowed_bad':allowed,'consumed_fraction':consumed,'remaining_fraction':remaining,
        'burn_tiers':active,'burn_windows':windows,'state':state.value,'reason':reason,'data_source':definition.source,
        'source_status':source_state,'source_kind':snapshot.source,'coverage':count.eligible/(count.eligible+count.unknown) if count.eligible+count.unknown else None,
        'evaluated_at':now,'source_last_updated':updated,'freshness_seconds':definition.freshness_seconds,
        'population_watermark':end,'runbook':'docs/Telemetry/runbooks/'+definition.runbook+'.md'}
