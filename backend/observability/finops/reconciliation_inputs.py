"""Read-only source snapshots and bounded aggregate projections, never raw source copies."""
from datetime import datetime, timezone
from decimal import Decimal, localcontext
from hashlib import sha256
from sqlalchemy import select, func
from .models import LedgerRow, AttemptRow, InboxRow, SourceRow
from .contracts import QUANTITIES
from .billing_contracts import digest, decimal, money, instant, SourceError
from .billing_normalization import detailed, normalize
from .reconciliation_contracts import Policy, controls, bounded


def billing_input(extraction,manifest,mapping,scope,environment,policy=None):
    policy=policy or Policy()
    projection=normalize(extraction,mapping)
    if projection.fingerprint!=manifest.fingerprint or projection.rows!=manifest.row_count or list(projection.controls)!=manifest.controls:
        raise SourceError('INPUT_DETAIL_UNAVAILABLE')
    if len(extraction.rows)>policy.max_rows:raise SourceError('BATCH_LIMIT')
    buckets={};components={}
    with localcontext() as ctx:
        ctx.prec=100
        for raw in extraction.rows:
            row=detailed(raw,mapping)
            dims={k:row[k] for k in ('service','sku','project','region','usage_start','usage_end','invoice_month','currency','cost_type','usage_unit','consumption_model')}
            # Invoice/time intervals retained, then matching aggregates these rows
            # without counting the same runtime population per invoice.
            key=digest(dims)
            b=buckets.setdefault(key,dict(id=key,**dims,quantity=Decimal(0),unknown=0,rows=0))
            b['rows']+=1
            if row['usage_amount'] is None:b['unknown']+=1
            else:b['quantity']+=decimal(row['usage_amount'])
            kind={'regular':'gross','adjustment':'adjustment','tax':'tax','rounding_error':'rounding'}[row['cost_type']]
            parts=[(kind,row['cost'],'NONE')]+[('credit',c['amount'],c['type']) for c in row['credits']]
            for kind,value,credit_type in parts:
                amount=decimal(value);sign='NEGATIVE' if amount<0 else 'NONNEGATIVE'
                cid=digest([key,kind,sign,credit_type])
                c=components.setdefault(cid,dict(id=cid,bucket=key,currency=row['currency'],kind=kind,
                    credit_type=credit_type,amount=Decimal(0),absolute=Decimal(0),count=0))
                c['amount']+=amount;c['absolute']+=abs(amount);c['count']+=1
            if len(buckets)>policy.max_buckets or len(components)>policy.max_buckets:raise SourceError('BATCH_LIMIT')
        bs=[dict(v,quantity=money(v['quantity']) if v['rows']>v['unknown'] else None) for _,v in sorted(buckets.items())]
        cs=[dict(v,amount=money(v['amount']),absolute=money(v['absolute'])) for _,v in sorted(components.items())]
    body=dict(kind='BILLING',environment=environment,scope=scope,period=extraction.window.partition,
              source_generation=manifest.generation,fingerprint=manifest.fingerprint,buckets=bs,components=cs,
              controls=controls(cs),export_time=manifest.export_time.isoformat() if manifest.export_time else None,
              detail_available=True)
    return bounded(body,policy)


class RuntimeProvider:
    def __init__(self,sessions,policy=None):self.sessions=sessions;self.policy=policy or Policy()
    async def snapshot(self,environment,window):
        async with self.sessions() as s:
            # Set isolation before first read; nothing here writes the M10 store.
            if s.bind.dialect.name=='postgresql':
                await s.connection(execution_options={'isolation_level':'REPEATABLE READ'})
            else:await s.connection()
            # SQLite driver otherwise starts no transaction on SELECT. Explicit
            # BEGIN gives concurrent reads a single consistent snapshot.
            if s.bind.dialect.name=='sqlite':
                from sqlalchemy import text
                await s.execute(text('BEGIN'))
            bases=select(LedgerRow.event_id).where(LedgerRow.environment==environment,
                LedgerRow.record_kind=='BASE',LedgerRow.observed_at>=window.start,LedgerRow.observed_at<window.end)
            rows=(await s.scalars(select(LedgerRow).where(LedgerRow.environment==environment,
                ((LedgerRow.event_id.in_(bases)) | (LedgerRow.original_event_id.in_(bases))))
                .order_by(LedgerRow.event_id).limit(self.policy.max_rows+1))).all()
            if len(rows)>self.policy.max_rows:raise SourceError('BATCH_LIMIT')
            attempts=(await s.scalars(select(AttemptRow).where(AttemptRow.environment==environment,
                AttemptRow.started_at>=window.start,AttemptRow.started_at<window.end)
                .order_by(AttemptRow.attempt_id).limit(self.policy.max_rows+1))).all()
            if len(attempts)>self.policy.max_rows:raise SourceError('BATCH_LIMIT')
            source=await s.get(SourceRow,environment)
            global_debt=await s.scalar(select(func.count()).select_from(AttemptRow).where(
                AttemptRow.environment==environment,AttemptRow.started_at.is_not(None),AttemptRow.state!='USAGE_CAPTURED'))
            pending=await s.scalar(select(func.count()).select_from(InboxRow).where(
                InboxRow.environment==environment,InboxRow.delivered.is_(False)))
            body=runtime_input(rows,attempts,environment,window,self.policy)
            body['quality'].update(global_debt=global_debt,pending=pending,
                available=source is not None,coverage_start=source.coverage_start.isoformat() if source else None,
                last_persistence=source.last_persistence.isoformat() if source and source.last_persistence else None)
            body['quality']['freshness']='UNAVAILABLE' if source is None else 'STALE' if (datetime.now(timezone.utc)-instant(source.updated_at)).total_seconds()>300 else 'FRESH'
            body['quality']['coverage_complete']=bool(source and instant(source.coverage_start)<=window.start)
            # Identity includes immutable event digest AND snapshot debt/source
            # quality, not a timestamp pretending to order all UUID events.
            body['ledger_digest']=digest([body['ledger_digest'],body['quality']])
            return bounded(body,self.policy)


def runtime_input(rows,attempts,environment,window,policy=None):
    policy=policy or Policy();rows=sorted(rows,key=lambda r:r.event_id)
    base={r.event_id:r for r in rows if r.record_kind=='BASE'};adjustments={}
    witness=sha256()
    for r in rows:
        witness.update(digest([r.event_id,r.payload_hash,r.record_kind,r.original_event_id]).encode())
        if r.record_kind=='ADJUSTMENT':adjustments.setdefault(r.original_event_id,[]).append(r)
    groups={}
    for row in base.values():
        i=row.metadata_safe['identity'];region=row.metadata_safe.get('region') or 'UNKNOWN'
        key=(i['provider'],i['model'],region,i['operation_type'])
        g=groups.setdefault(key,dict(provider=key[0],model=key[1],region=key[2],operation_type=key[3],events=0,
             quantities={q:dict(observed=0,known=0,unknown=0) for q in QUANTITIES}))
        g['events']+=1
        for q in QUANTITIES:
            values=[getattr(r,q) for r in (row,*adjustments.get(row.event_id,())) if getattr(r,q) is not None]
            v=g['quantities'][q]
            if values:
                total=sum(values)
                if total<0:raise SourceError('RUNTIME_USAGE_UNKNOWN')
                v['observed']+=total;v['known']+=1
            else:v['unknown']+=1
        if len(groups)>policy.max_buckets:raise SourceError('BATCH_LIMIT')
    persisted_attempts={r.attempt_id for r in base.values() if hasattr(r,'attempt_id')}
    for attempt in attempts:
        if attempt.state=='USAGE_CAPTURED' or attempt.attempt_id in persisted_attempts:continue
        identity=attempt.identity
        # Admission has no authoritative region; never borrow another attempt's.
        key=(identity['provider'],identity['model'],'UNKNOWN',identity['operation_type'])
        g=groups.setdefault(key,dict(provider=key[0],model=key[1],region=key[2],operation_type=key[3],events=0,
            quantities={q:dict(observed=0,known=0,unknown=0) for q in QUANTITIES}))
        g['events']+=1
        for v in g['quantities'].values():v['unknown']+=1
        if len(groups)>policy.max_buckets:raise SourceError('BATCH_LIMIT')
    for g in groups.values():
        for v in g['quantities'].values():v['observed']=str(v['observed']) if v['known'] else None
    debt=sum(a.state!='USAGE_CAPTURED' for a in attempts)
    for a in sorted(attempts,key=lambda a:a.attempt_id):witness.update(digest([a.attempt_id,a.state]).encode())
    return dict(kind='RUNTIME',environment=environment,period=window.partition,ledger_digest=witness.hexdigest(),
        schema_version=1,quantity_semantics_version=1,buckets=[groups[k] for k in sorted(groups)],
        quality=dict(eligible=len(attempts),captured=len(attempts)-debt,debt=debt,global_debt=debt,pending=0,
             completeness=None if not attempts else str(Decimal(len(attempts)-debt)/Decimal(len(attempts))),
             available=True,coverage_complete=True,last_persistence=None,freshness='FRESH'))
