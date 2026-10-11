"""Short SQL transactions for delivery and approved observability projections."""
from datetime import timedelta
from hashlib import sha256
from uuid import UUID, uuid4
from sqlalchemy import select, update, or_, and_, case
from ..finops.repository import insert_for
from ..projection import utcnow
from .contracts import AuditEvent, decode_event, event_digest
from .capture import RUNTIME_TYPES
from .models import AuditRow, OutboxRow, ReceiptRow, FailureRow, RunProjectionRow, AgentProjectionRow


def decode_row(row):
    return AuditEvent(event_id=row.event_id,event_type=row.event_type,environment=row.environment,
        timestamp=row.occurred_at,aggregate_type=row.aggregate_type,aggregate_id=row.aggregate_id,
        aggregate_version=row.aggregate_version,producer=row.producer,run_id=row.run_id,
        turn_id=row.turn_id,trace_id=row.trace_id,payload_safe=row.safe_payload)

class OutboxStore:
    def __init__(self,sessions,config): self.sessions,self.config=sessions,config
    async def claim(self,now=None):
        now=now or utcnow();cfg=self.config;claims=[]
        async with self.sessions() as s,s.begin():
            due=or_(and_(OutboxRow.publication_state.in_(('PENDING','RETRYING')),OutboxRow.available_at<=now),
                and_(OutboxRow.publication_state=='LEASED',OutboxRow.lease_expires_at<=now))
            rows=(await s.execute(select(OutboxRow).where(OutboxRow.environment==cfg.environment,due)
                .order_by(OutboxRow.available_at,OutboxRow.event_id).limit(cfg.publisher_batch_size)
                .with_for_update(skip_locked=True))).scalars().all()
            for row in rows:
                if row.attempt_count>=cfg.publisher_max_attempts:
                    await s.execute(update(OutboxRow).where(OutboxRow.event_id==row.event_id,due)
                        .values(publication_state='DEAD',lease_owner=None,lease_expires_at=None,last_failure_code='LEASE_EXHAUSTED'))
                    continue
                token=str(uuid4());attempt=row.attempt_count+1
                changed=await s.execute(update(OutboxRow).where(OutboxRow.event_id==row.event_id,due,
                    OutboxRow.attempt_count==row.attempt_count).values(publication_state='LEASED',
                    attempt_count=attempt,lease_owner=token,lease_expires_at=now+timedelta(seconds=cfg.publisher_lease_seconds)))
                if changed.rowcount:
                    try:
                        journal=(await s.execute(select(AuditRow).where(AuditRow.event_id==row.event_id))).scalar_one()
                        if journal.payload_hash!=row.payload_hash:raise ValueError("Outbox reference hash conflict")
                        event=decode_row(journal)
                    except (ValueError,TypeError):
                        row.publication_state='DEAD';row.lease_owner=row.lease_expires_at=None;row.last_failure_code='INVALID'
                        continue
                    claims.append((event,token,row.payload_hash))
        return claims
    async def settle(self,event_id,token,*,success,now=None,code='TRANSPORT_FAILED'):
        now=now or utcnow();cfg=self.config
        async with self.sessions() as s,s.begin():
            row=(await s.execute(select(OutboxRow).where(OutboxRow.environment==cfg.environment,
                OutboxRow.event_id==str(event_id),OutboxRow.publication_state=='LEASED',
                OutboxRow.lease_owner==token,OutboxRow.lease_expires_at>now).with_for_update())).scalar_one_or_none()
            if row is None: return False
            row.lease_owner=row.lease_expires_at=None
            if success:
                row.publication_state='PUBLISHED';row.published_at=now;row.last_failure_code=None
            else:
                dead=row.attempt_count>=cfg.publisher_max_attempts or code=='INVALID'
                row.publication_state='DEAD' if dead else 'RETRYING'
                row.last_failure_code=code if code in ('TRANSPORT_FAILED','INVALID') else 'TRANSPORT_FAILED'
                delay=min(cfg.retry_max_seconds,cfg.retry_initial_seconds*2**min(row.attempt_count-1,16))
                row.available_at=now+timedelta(seconds=delay)
            return True

class ProjectorStore:
    def __init__(self,sessions,config): self.sessions,self.config=sessions,config
    async def failure(self,code,key='invalid',now=None):
        now=now or utcnow()
        if code not in ('INVALID','CONFLICT','PROCESSING_FAILED'): code='PROCESSING_FAILED'
        try:key=str(UUID(key)) if key!='invalid' else key
        except (ValueError,TypeError,AttributeError):key='invalid'
        key=sha256((code+key).encode()).hexdigest()
        async with self.sessions() as s,s.begin():
            stmt=insert_for(s,FailureRow).values(environment=self.config.environment,failure_key=key,
                failure_code=code,processing_state='DEAD' if code!='PROCESSING_FAILED' else 'RETRY',attempts=1,last_at=now)
            await s.execute(stmt.on_conflict_do_update(index_elements=['environment','failure_key'],set_={
                'attempts':FailureRow.attempts+1,'last_at':now,'processing_state':
                'DEAD' if code!='PROCESSING_FAILED' else 'RETRY'}))
            row=await s.get(FailureRow,(self.config.environment,key),with_for_update=True)
            if row.attempts>=self.config.publisher_max_attempts:row.processing_state='DEAD'
            return row.processing_state
    async def apply(self,event,now=None):
        now=now or utcnow();cfg=self.config
        event=decode_event(event.model_dump_json(warnings='none').encode(),environment=cfg.environment)
        if not isinstance(event,AuditEvent) or event.event_type not in RUNTIME_TYPES:raise ValueError('Canonical audit required')
        digest=event_digest(event)
        async with self.sessions() as s,s.begin():
            stmt=insert_for(s,ReceiptRow).values(environment=cfg.environment,event_id=str(event.event_id),
                payload_hash=digest,occurred_at=event.timestamp,processed_at=now,duplicate_count=0)
            inserted=await s.execute(stmt.on_conflict_do_nothing(index_elements=['environment','event_id']))
            receipt=await s.get(ReceiptRow,(cfg.environment,str(event.event_id)),with_for_update=True)
            if not inserted.rowcount:
                if receipt.payload_hash!=digest: return 'CONFLICT'
                receipt.duplicate_count+=1
                await self.resolve_failure(s,event,now)
                return 'DUPLICATE'
            from backend.execution_audit.projection import apply as project_execution
            await project_execution(s,event,digest,now)
            lag=max(0,(now-event.timestamp).total_seconds())
            if event.event_type in ('run.accepted','run.terminal'):
                data=dict(environment=cfg.environment,run_id=str(event.run_id),aggregate_version=event.aggregate_version,
                    status=event.payload_safe.status.value,turn_id=event.turn_id,trace_id=event.trace_id,
                    latest_event_occurred_at=event.timestamp,latest_event_processed_at=now,projection_lag=lag)
                stmt=insert_for(s,RunProjectionRow).values(**data)
                await s.execute(stmt.on_conflict_do_update(index_elements=['environment','run_id'],set_=data,
                    where=and_(RunProjectionRow.aggregate_version<event.aggregate_version,
                        RunProjectionRow.status=='RUNNING')))
            elif event.payload_safe.agent is not None and event.event_type.startswith('model.'):
                agent=event.payload_safe.agent.value
                data=dict(environment=cfg.environment,agent=agent,model_attempts_started=0,
                    model_attempts_completed=0,usage_records_captured=0,latest_event_occurred_at=event.timestamp,
                    latest_event_processed_at=now,projection_lag=lag)
                field={'model.attempt.started':'model_attempts_started','model.attempt.completed':'model_attempts_completed',
                    'model.usage.captured':'usage_records_captured'}[event.event_type];data[field]=1
                stmt=insert_for(s,AgentProjectionRow).values(**data)
                await s.execute(stmt.on_conflict_do_update(index_elements=['environment','agent'],set_={
                    field:getattr(AgentProjectionRow,field)+1,'latest_event_processed_at':now,
                    'latest_event_occurred_at':case((AgentProjectionRow.latest_event_occurred_at<event.timestamp,event.timestamp),else_=AgentProjectionRow.latest_event_occurred_at),
                    'projection_lag':case((AgentProjectionRow.latest_event_occurred_at<event.timestamp,lag),else_=AgentProjectionRow.projection_lag)},
                    # Counts are additive after receipt dedup; timestamp watermark fixed below.
                ))
            await self.resolve_failure(s,event,now)
            return 'PROCESSED'

    async def resolve_failure(self,s,event,now):
        failure_key=sha256(('PROCESSING_FAILED'+str(event.event_id)).encode()).hexdigest()
        await s.execute(update(FailureRow).where(FailureRow.environment==self.config.environment,
            FailureRow.failure_key==failure_key).values(processing_state='RESOLVED',last_at=now))
