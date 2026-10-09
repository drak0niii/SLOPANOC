"""Read-only operational populations. Snapshot persistence is independent of billing."""
from datetime import datetime, timezone, timedelta
from sqlalchemy import select, text
from ..slo_rollups import Receipt, SREReceiptRow, SRESourceRow, contributions
from ..slo_contract import VERSION, RequestClass, SETTLEMENT_GRACE_SECONDS
from .models import AttemptRow, LedgerRow, SourceRow
from .reconciliation_inputs import runtime_input
from .billing_contracts import digest, instant, SourceError
from .unit_economics_contracts import Snapshot, Policy, Population, OperationalContext, divide

def utcnow():
    return datetime.now(timezone.utc)

async def begin_read(session):
    if session.bind.dialect.name=='postgresql':
        await session.connection(execution_options={'isolation_level':'REPEATABLE READ'})
    else:
        await session.execute(text('BEGIN'))

def turn_counts(receipts, now):
    """Reuse M9's canonical success/terminal interpretation; no new outcome rule."""
    counts={'ACCEPTED_TURN':0,'AVAILABLE_TURN':0,'TIMELY_TERMINAL_TURN':0,
            **{c.value+'_TURN':0 for c in RequestClass}}
    witness=[]
    for r in receipts:
        counts['ACCEPTED_TURN']+=1
        c=contributions(r,now)
        counts['AVAILABLE_TURN']+=c.get('availability',{}).get('good',0)
        counts['TIMELY_TERMINAL_TURN']+=c.get('terminal_completion',{}).get('good',0)
        counts[r.request_class.value+'_TURN']+=1
        # Identity/version witness only; no subject/receipt copy is persisted.
        witness.append((r.run_id,r.version,digest(c),r.request_class.value))
    return counts,digest(sorted(witness))

def turn_contexts(receipts,now):
    """Acceptance-cohort SLO context; operational availability is not M19 quality."""
    from decimal import Decimal,localcontext
    from .reconciliation_contracts import ratio
    groups={}
    with localcontext() as ctx:
        ctx.prec=100
        for r in receipts:
            for key in ('ACCEPTED_TURN','AVAILABLE_TURN','TIMELY_TERMINAL_TURN',r.request_class.value+'_TURN'):
                g=groups.setdefault(key,dict(n=0,good=0,known=0,timely=0,latency=Decimal(0),observed=0))
                g['n']+=1;c=contributions(r,now);a=c.get('availability',{})
                g['good']+=a.get('good',0);g['known']+=a.get('good',0)+a.get('bad',0)
                g['timely']+=c.get('terminal_completion',{}).get('good',0)
                if r.terminal_at and r.duration_ms is not None:
                    g['latency']+=Decimal(str(r.duration_ms));g['observed']+=1
        return {key:OperationalContext(availability_ratio=ratio(g['good'],g['known']),
            timely_terminal_ratio=ratio(g['timely'],g['n']),mean_latency_ms=divide(g['latency'],g['observed']),
            latency_observed=g['observed'],eligible_turns=g['n']) for key,g in groups.items()}

class TurnProvider:
    def __init__(self,sessions,environment,turn_timeout_seconds,policy=None):
        self.sessions,self.environment,self.timeout=sessions,environment,turn_timeout_seconds
        self.policy=policy or Policy()
    async def snapshot(self,window,now=None):
        now=now or utcnow()
        async with self.sessions() as s:
            await begin_read(s)
            source=await s.get(SRESourceRow,self.environment)
            # Indexed due range bounds the read. Accepted time decides the day.
            rows=(await s.scalars(select(SREReceiptRow).where(SREReceiptRow.environment==self.environment,
                SREReceiptRow.due_at>=window.start,
                SREReceiptRow.due_at<window.end+timedelta(seconds=self.timeout+SETTLEMENT_GRACE_SECONDS))
                .order_by(SREReceiptRow.run_id).limit(self.policy.max_rows+1))).all()
            if len(rows)>self.policy.max_rows:
                raise SourceError('BATCH_LIMIT')
            receipts=[Receipt.model_validate(r.payload) for r in rows]
            receipts=[r for r in receipts if window.start<=r.accepted_at<window.end]
            counts,witness=turn_counts(receipts,now)
            retained=now<window.start+timedelta(days=2)
            settled=now>=window.end+timedelta(seconds=self.timeout+SETTLEMENT_GRACE_SECONDS)
            fresh=bool(source and 0<=(now-instant(source.updated_at)).total_seconds()<=300)
            complete=bool(source and source.capture_complete and instant(source.coverage_start)<=window.start and retained and settled)
            if not retained:
                counts={k:None for k in counts}
            quality={'retained':retained,'settled':settled,'complete':complete,'fresh':fresh,
                'coverage_start':source.coverage_start.isoformat() if source else None}
            contexts=turn_contexts(receipts,now) if complete else {}
            return Snapshot(source='M9',environment=self.environment,period=window.partition,
                source_digest=digest([witness,quality,VERSION,{k:v.model_dump() for k,v in contexts.items()}]),watermark=window.end if settled else min(now,window.end),
                captured_at=now,complete=complete,freshness='FRESH' if fresh else 'STALE' if source else 'UNAVAILABLE',
                counts=counts,business_population_proven=self.policy.business_population_proven,
                window_aligned=all(r.terminal_at is None or r.terminal_at<window.end for r in receipts),
                operational_contexts=contexts,
                coverage_start=source.coverage_start if source else None)

def runtime_counts(attempts, bases, window):
    """Usage start owns the day; database capture time never moves the denominator."""
    groups={};operations=set();aligned=True
    for a in attempts:
        if a.started_at is None or a.state=='NOT_STARTED':
            continue
        i=a.identity;row=bases.get(a.attempt_id)
        region=row.metadata_safe.get('region') if row else None
        key=(i['provider'],i['model'],region or 'UNKNOWN',i['operation_type'],i['workload'])
        g=groups.setdefault(key,dict(attempts=0,operations=set(),complete=True))
        g['attempts']+=1;g['operations'].add(i['logical_call_id']);operations.add(i['logical_call_id'])
        g['complete'] &= a.state=='USAGE_CAPTURED' and row is not None
        if row is not None and not window.start<=instant(row.observed_at)<window.end:
            aligned=False
    return tuple(Population(provider=k[0],model=k[1],region=k[2],operation_type=k[3],workload=k[4],
        attempts=v['attempts'],operations=len(v['operations']),complete=v['complete']) for k,v in sorted(groups.items())),len(operations),aligned

class ModelProvider:
    def __init__(self,sessions,environment,policy=None):
        self.sessions,self.environment=sessions,environment;self.policy=policy or Policy()
    async def snapshot(self,window,m12_runtime=None,now=None):
        now=now or utcnow()
        async with self.sessions() as s:
            await begin_read(s)
            # Read start-day attempts and both start/observed-day BASE populations.
            attempts=(await s.scalars(select(AttemptRow).where(AttemptRow.environment==self.environment,
                AttemptRow.started_at>=window.start,AttemptRow.started_at<window.end)
                .order_by(AttemptRow.attempt_id).limit(self.policy.max_rows+1))).all()
            aids=[a.attempt_id for a in attempts]
            # Subquery avoids 100k SQL bind parameters.
            selected=select(AttemptRow.attempt_id).where(AttemptRow.environment==self.environment,
                AttemptRow.started_at>=window.start,AttemptRow.started_at<window.end)
            base_ids=select(LedgerRow.event_id).where(LedgerRow.environment==self.environment,
                LedgerRow.record_kind=='BASE',((LedgerRow.attempt_id.in_(selected))|
                ((LedgerRow.observed_at>=window.start)&(LedgerRow.observed_at<window.end))))
            rows=(await s.scalars(select(LedgerRow).where(LedgerRow.environment==self.environment,
                (LedgerRow.event_id.in_(base_ids))|(LedgerRow.original_event_id.in_(base_ids)))
                .order_by(LedgerRow.event_id).limit(self.policy.max_rows+1))).all()
            if max(len(attempts),len(rows))>self.policy.max_rows:
                raise SourceError('BATCH_LIMIT')
            bases={r.attempt_id:r for r in rows if r.record_kind=='BASE'}
            populations,logical,aligned=runtime_counts(attempts,bases,window)
            observed={r.event_id for r in bases.values() if window.start<=instant(r.observed_at)<window.end}
            observed_rows=[r for r in rows if r.event_id in observed or r.original_event_id in observed]
            from .reconciliation_contracts import Policy as ReconciliationPolicy
            runtime=runtime_input(observed_rows,attempts,self.environment,window,ReconciliationPolicy())
            runtime_digest=None
            if m12_runtime:
                # Freeze against the exact accounting evidence M12 used; source
                # health timestamps may evolve, immutable witnesses may not.
                witness=digest([runtime['ledger_digest'],m12_runtime['quality']])
                if witness==m12_runtime['ledger_digest'] and runtime['buckets']==m12_runtime['buckets']:
                    runtime_digest=witness
                else:
                    aligned=False
            # Captures whose usage belongs to another day cannot be priced here.
            if set(bases)-set(aids):
                aligned=False
            source=await s.get(SourceRow,self.environment)
            complete=bool(source and source.coverage_start<=window.start and all(p.complete for p in populations))
            fresh=bool(source and 0<=(now-instant(source.updated_at)).total_seconds()<=300)
            if len(populations)>self.policy.max_buckets:
                raise SourceError('BATCH_LIMIT')
            counts={'PROVIDER_ATTEMPT':sum(p.attempts for p in populations),'MODEL_OPERATION':logical}
            return Snapshot(source='M10',environment=self.environment,period=window.partition,
                source_digest=digest([runtime['ledger_digest'],counts,[p.model_dump() for p in populations],aligned,complete]),
                runtime_digest=runtime_digest,watermark=window.end,captured_at=now,complete=complete,
                freshness='FRESH' if fresh else 'STALE' if source else 'UNAVAILABLE',counts=counts,
                buckets=populations,window_aligned=aligned,coverage_start=source.coverage_start if source else None)
