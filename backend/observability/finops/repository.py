"""Short accounting transactions. This module never calls a provider or business tool."""
from datetime import datetime, timezone, timedelta
from hashlib import sha256
import json
from sqlalchemy import select, update, func
from .contracts import Identity, Payload, Adjustment, QUANTITIES
from .models import AttemptRow, InboxRow, LedgerRow, BucketRow, SourceRow


def utcnow(): return datetime.now(timezone.utc)
def minute(at): return at.astimezone(timezone.utc).replace(second=0, microsecond=0)

def insert_for(session, table):
    if session.bind.dialect.name == 'postgresql':
        from sqlalchemy.dialects.postgresql import insert
    else:
        from sqlalchemy.dialects.sqlite import insert
    return insert(table)

class Repository:
    def __init__(self, sessions): self.sessions = sessions

    async def source(self, s, env, now):
        stmt = insert_for(s, SourceRow).values(environment=env, coverage_start=minute(now), updated_at=now, admission_failures=0)
        await s.execute(stmt.on_conflict_do_nothing(index_elements=['environment']))

    async def bucket(self, s, identity, at, delta):
        keys = {k: getattr(identity, k) for k in ('environment','provider','model','agent','workload','operation','operation_type')}
        keys = {k: v.value if hasattr(v,'value') else v for k,v in keys.items()}
        keys['at'] = minute(at)
        counts = ('good','bad','quantity_known','quantity_partial', *QUANTITIES, *(q+'_known' for q in QUANTITIES))
        stmt = insert_for(s, BucketRow).values(**keys, **dict.fromkeys(counts,0))
        await s.execute(stmt.on_conflict_do_nothing(index_elements=list(keys)))
        await s.execute(update(BucketRow).where(*(getattr(BucketRow,k)==v for k,v in keys.items()))
            .values(**{k:getattr(BucketRow,k)+v for k,v in delta.items()}))

    async def admit(self, identity, now=None):
        identity = Identity.model_validate(identity.model_dump())
        now = now or utcnow()
        async with self.sessions() as s, s.begin():
            await self.source(s, identity.environment, now)
            stmt = insert_for(s, AttemptRow).values(environment=identity.environment,
                attempt_id=str(identity.attempt_id), identity=identity.model_dump(mode='json'),
                state='ADMITTED', admitted_at=now, deadline_at=identity.deadline_at)
            await s.execute(stmt.on_conflict_do_nothing(index_elements=['environment','attempt_id']))
            row = await s.get(AttemptRow,(identity.environment,str(identity.attempt_id)),with_for_update=True)
            if row.identity != identity.model_dump(mode='json') or row.state != 'ADMITTED':
                raise ValueError('Attempt admission conflict')
        return identity

    async def start(self, identity, now=None):
        now = now or utcnow()
        async with self.sessions() as s, s.begin():
            row = await s.get(AttemptRow,(identity.environment,str(identity.attempt_id)),with_for_update=True)
            if row is None or row.state != 'ADMITTED': raise ValueError('Attempt cannot start')
            row.state='STARTED';row.started_at=now
            await self.bucket(s,identity,now,{'bad':1})
            await s.execute(update(SourceRow).where(SourceRow.environment==identity.environment).values(updated_at=now,last_observation=now))

    async def not_started(self, identity):
        async with self.sessions() as s, s.begin():
            await s.execute(update(AttemptRow).where(AttemptRow.environment==identity.environment,
                AttemptRow.attempt_id==str(identity.attempt_id),AttemptRow.state=='ADMITTED').values(state='NOT_STARTED'))

    async def finished(self, identity, at):
        async with self.sessions() as s, s.begin():
            await s.execute(update(AttemptRow).where(AttemptRow.environment==identity.environment,
                AttemptRow.attempt_id==str(identity.attempt_id),AttemptRow.state=='STARTED')
                .values(state='PROVIDER_FINISHED',provider_finished_at=at))

    async def failed_capture(self, identity):
        async with self.sessions() as s, s.begin():
            await s.execute(update(AttemptRow).where(AttemptRow.environment==identity.environment,
                AttemptRow.attempt_id==str(identity.attempt_id),AttemptRow.state.in_(('STARTED','PROVIDER_FINISHED')))
                .values(state='FINAL_CAPTURE_FAILED',gap_reason='CAPTURE_FAILED'))

    async def capture(self, payload):
        payload = Payload.model_validate(payload.model_dump())
        i=payload.identity;event_id=i.event_id;now=utcnow()
        async with self.sessions() as s, s.begin():
            row=await s.get(AttemptRow,(i.environment,str(i.attempt_id)),with_for_update=True)
            if row is None or row.started_at is None: raise ValueError('Unstarted capture')
            if row.identity != i.model_dump(mode='json'): raise ValueError('Observation identity conflict')
            stmt=insert_for(s,InboxRow).values(event_id=event_id,environment=i.environment,attempt_id=str(i.attempt_id),
                payload=payload.model_dump(mode='json'),payload_hash=payload.digest(),delivered=False,
                attempts=0,next_attempt_at=now,created_at=now,exhausted=False)
            result=await s.execute(stmt.on_conflict_do_nothing(index_elements=['event_id']))
            inbox=await s.get(InboxRow,event_id)
            if inbox.payload_hash != payload.digest():
                row.state='CONFLICT';row.gap_reason='IDENTITY_CONFLICT'
                return 'CONFLICT'
            if row.state!='CONFLICT' and row.state!='USAGE_CAPTURED':
                row.state='FINAL_CAPTURE_PENDING';row.captured_at=now;row.provider_finished_at=payload.provider_finished_at
            await s.execute(update(SourceRow).where(SourceRow.environment==i.environment).values(last_observation=payload.observed_at,updated_at=now))
            return 'ACCEPTED' if result.rowcount else 'DUPLICATE'

    async def materialize(self, event_id):
        now=utcnow()
        async with self.sessions() as s, s.begin():
            # Consistent attempt->inbox lock ordering across capture/materialize.
            # Short transaction locks are worker leases; a crash releases them.
            header=(await s.execute(select(InboxRow.environment,InboxRow.attempt_id)
                .where(InboxRow.event_id==event_id))).one_or_none()
            if header is None:return 'BUSY'
            row=(await s.execute(select(AttemptRow).where(AttemptRow.environment==header.environment,
                AttemptRow.attempt_id==header.attempt_id).with_for_update(skip_locked=True))).scalar_one_or_none()
            if row is None:return 'BUSY'
            inbox=(await s.execute(select(InboxRow).where(InboxRow.event_id==event_id)
                .with_for_update(skip_locked=True))).scalar_one_or_none()
            if inbox is None:return 'BUSY'
            if inbox.delivered:return 'DUPLICATE'
            payload=Payload.model_validate(inbox.payload);i=payload.identity
            if inbox.payload_hash!=payload.digest():
                row.state='CONFLICT';row.gap_reason='INBOX_INTEGRITY';return 'CONFLICT'
            if row.state=='CONFLICT': return 'CONFLICT'
            safe=payload.model_dump(mode='json');safe.pop('usage')
            safe['usage_source']=payload.usage.source;safe['usage_availability']=payload.usage.availability
            stmt=insert_for(s,LedgerRow).values(event_id=event_id,environment=i.environment,
                attempt_id=str(i.attempt_id),payload_hash=payload.digest(),record_kind='BASE',metadata_safe=safe,
                observed_at=row.started_at,created_at=now,**{q:getattr(payload.usage,q) for q in QUANTITIES})
            result=await s.execute(stmt.on_conflict_do_nothing(index_elements=['event_id']))
            ledger=await s.get(LedgerRow,event_id)
            if ledger.payload_hash!=payload.digest():
                row.state='CONFLICT';row.gap_reason='IDENTITY_CONFLICT';return 'CONFLICT'
            if result.rowcount:
                delta={'good':1,'bad':-1,'quantity_known':int(payload.usage.availability=='KNOWN'),
                    'quantity_partial':int(payload.usage.availability=='PARTIAL')}
                for q in QUANTITIES:
                    value=getattr(payload.usage,q)
                    if value is not None: delta[q]=value;delta[q+'_known']=1
                await self.bucket(s,i,row.started_at,delta)
            inbox.delivered=True;inbox.delivered_at=now
            row.state='USAGE_CAPTURED';row.gap_reason=None
            await s.execute(update(SourceRow).where(SourceRow.environment==i.environment)
                .values(last_persistence=now,updated_at=now))
            return 'PERSISTED' if result.rowcount else 'DUPLICATE'

    async def pending(self, limit, now=None):
        now=now or utcnow()
        async with self.sessions() as s:
            return list((await s.execute(select(InboxRow.event_id).where(InboxRow.delivered.is_(False),
                InboxRow.exhausted.is_(False),InboxRow.next_attempt_at<=now)
                .order_by(InboxRow.next_attempt_at,InboxRow.event_id).limit(limit))).scalars())

    async def retry_failed(self, event_id, maximum):
        async with self.sessions() as s, s.begin():
            row=await s.get(InboxRow,event_id,with_for_update=True)
            if row is None or row.delivered:return
            row.attempts+=1;row.exhausted=row.attempts>=maximum
            row.next_attempt_at=utcnow()+timedelta(seconds=min(60,2**min(row.attempts,6)))

    async def checkpoint(self, env, now=None, limit=100):
        now=now or utcnow()
        async with self.sessions() as s, s.begin():
            await self.source(s,env,now)
            rows=(await s.execute(select(AttemptRow).where(AttemptRow.environment==env,
                AttemptRow.deadline_at<now,AttemptRow.state.in_(('ADMITTED','STARTED','PROVIDER_FINISHED')))
                .order_by(AttemptRow.deadline_at).limit(limit).with_for_update(skip_locked=True))).scalars()
            for row in rows:
                # STARTED is required before dispatch, so expired ADMITTED never dispatched.
                row.state='NOT_STARTED' if row.started_at is None else 'OUTCOME_UNKNOWN'
                row.gap_reason=None if row.started_at is None else 'FINAL_OBSERVATION_MISSING'
            await s.execute(update(SourceRow).where(SourceRow.environment==env).values(updated_at=now))

    async def adjust(self, value):
        value=Adjustment.model_validate(value.model_dump());now=utcnow()
        digest=sha256(json.dumps(value.model_dump(mode='json'),sort_keys=True).encode()).hexdigest()
        async with self.sessions() as s, s.begin():
            original=await s.get(LedgerRow,str(value.original_event_id),with_for_update=True)
            if original is None or original.environment!=value.environment or original.record_kind!='BASE':
                raise ValueError('Correction reference invalid')
            identity=Identity.model_validate(original.metadata_safe['identity'])
            previous=(await s.execute(select(LedgerRow).where(
                LedgerRow.original_event_id==original.event_id))).scalars().all()
            known={q for q in QUANTITIES if getattr(original,q) is not None
                or any(getattr(a,q) is not None for a in previous)}
            supplied={q for q,v in value.quantities.items() if v is not None}
            existing=await s.get(LedgerRow,str(value.correction_id))
            if existing is None and value.reason=='PROVIDER_LATE_USAGE' and (
                supplied & known or any(v<0 for v in value.quantities.values() if v is not None)):
                raise ValueError('Late usage must supplement unknown quantities')
            stmt=insert_for(s,LedgerRow).values(event_id=str(value.correction_id),environment=value.environment,
                attempt_id=original.attempt_id,payload_hash=digest,record_kind='ADJUSTMENT',
                original_event_id=original.event_id,correction_reason=value.reason,correction_process=value.process,
                metadata_safe={'ledger_schema_version':1,'quantity_semantics_version':1,'idempotency_key_version':1},
                observed_at=value.at,created_at=now,**{q:value.quantities.get(q) for q in QUANTITIES})
            result=await s.execute(stmt.on_conflict_do_nothing(index_elements=['event_id']))
            row=await s.get(LedgerRow,str(value.correction_id))
            if row.payload_hash!=digest:
                attempt=await s.get(AttemptRow,(value.environment,original.attempt_id),with_for_update=True)
                attempt.state='CONFLICT';attempt.gap_reason='CORRECTION_CONFLICT';return 'CONFLICT'
            if result.rowcount:
                # Referenced quantity contributions never inflate BASE population.
                delta={q:v for q,v in value.quantities.items() if v is not None}
                for q in supplied-known:delta[q+'_known']=1
                required={'input_tokens'} if identity.operation_type=='EMBEDDING' else {'input_tokens','output_tokens','total_tokens'}
                availability=original.metadata_safe.get('usage_availability','UNKNOWN')
                def coverage(fields):
                    if availability=='KNOWN' or required<=fields:return (1,0)
                    if availability=='PARTIAL' or fields:return (0,1)
                    return (0,0)
                before=coverage(known);after=coverage(known|supplied)
                delta['quantity_known']=after[0]-before[0]
                delta['quantity_partial']=after[1]-before[1]
                await self.bucket(s,identity,original.observed_at,delta)
            return 'PERSISTED' if result.rowcount else 'DUPLICATE'

    async def health(self, env, now=None):
        now=now or utcnow()
        async with self.sessions() as s:
            source=await s.get(SourceRow,env)
            pending=(await s.execute(select(func.count(),func.min(InboxRow.created_at)).where(
                InboxRow.environment==env,InboxRow.delivered.is_(False)))).one()
            debt=(await s.execute(select(func.count(),func.min(AttemptRow.started_at)).where(
                AttemptRow.environment==env,AttemptRow.started_at.is_not(None),AttemptRow.state!='USAGE_CAPTURED'))).one()
            conflicts=(await s.execute(select(func.count()).select_from(AttemptRow).where(AttemptRow.environment==env,AttemptRow.state=='CONFLICT'))).scalar_one()
            exhausted=(await s.execute(select(func.count()).select_from(InboxRow).where(InboxRow.environment==env,InboxRow.exhausted.is_(True),InboxRow.delivered.is_(False)))).scalar_one()
            return {'pending':pending[0],'oldest_pending_at':pending[1],'debt':debt[0],
                'oldest_debt_at':debt[1],'conflicts':conflicts,'exhausted':exhausted,
                'updated_at':source.updated_at if source else None,
                'coverage_start':source.coverage_start if source else None,
                'last_observation':source.last_observation if source else None,
                'last_persistence':source.last_persistence if source else None}

    async def buckets(self, env, start, end):
        # Compact minute counts for M9; aggregate across safe dimensions in SQL.
        async with self.sessions() as s:
            return (await s.execute(select(BucketRow.at,func.sum(BucketRow.good),func.sum(BucketRow.bad))
                .where(BucketRow.environment==env,BucketRow.at>=start,BucketRow.at<end)
                .group_by(BucketRow.at).order_by(BucketRow.at))).all()

    async def usage(self, env, start, end, group='model', limit=100, offset=0):
        if group not in ('model','provider','agent','workload','operation','operation_type'):raise ValueError('Invalid grouping')
        dim=getattr(BucketRow,group)
        columns=[dim.label('dimension'),func.sum(BucketRow.good+BucketRow.bad).label('attempts'),func.sum(BucketRow.good).label('captured_attempts'),func.sum(BucketRow.quantity_known).label('quantity_known'),
            func.sum(BucketRow.quantity_partial).label('quantity_partial')]
        columns += [func.sum(getattr(BucketRow,q)).label(q) for q in QUANTITIES]
        columns += [func.sum(getattr(BucketRow,q+'_known')).label(q+'_known') for q in QUANTITIES]
        async with self.sessions() as s:
            rows=(await s.execute(select(*columns).where(BucketRow.environment==env,BucketRow.at>=start,BucketRow.at<end)
                .group_by(dim).order_by(dim).offset(offset).limit(limit+1))).mappings().all()
        items=[]
        for row in rows[:limit]:
            items.append({'dimension':row['dimension'],'attempts':row['attempts'],
                'captured_attempts':row['captured_attempts'],'quantity_known':row['quantity_known'],'quantity_partial':row['quantity_partial'],
                'quantity_coverage':row['quantity_known']/row['captured_attempts'] if row['captured_attempts'] else None,
                'quantities':{q:{'observed':row[q] if row[q+'_known'] else None,'known':row[q+'_known'],
                    'unknown':row['captured_attempts']-row[q+'_known']} for q in QUANTITIES}})
        return items,len(rows)>limit
