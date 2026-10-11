"""Append immutable audit reference + outbox within caller-owned SQL transaction."""
from uuid import UUID, uuid5
from sqlalchemy import select
from .contracts import AuditEvent, AuditPayload, decode_event
from .models import AuditRow, OutboxRow
from ..finops.repository import insert_for
from ..projection import utcnow

NAMESPACE=UUID('88f66a83-0847-502f-8cae-f4b4efc18424')
LEGACY_TYPES=frozenset({'governance.progression.recorded','turn.bound','run.accepted','run.terminal','model.attempt.started',
    'model.attempt.completed','model.usage.captured','accounting.debt.created','accounting.debt.resolved'})

from backend.execution_audit.contracts import JOURNAL_EVENTS
RUNTIME_TYPES=LEGACY_TYPES | frozenset(JOURNAL_EVENTS)

def make_event(kind,environment,aggregate,identity,version,at,*,source_version=1,
    status=None,agent=None,run_id=None,turn_id=None,trace_id=None,session_id=None,model_attempt_id=None,agent_execution_id=None):
    if kind not in LEGACY_TYPES: raise ValueError('No canonical event owner')
    return AuditEvent(event_id=uuid5(NAMESPACE,f'v1:{environment}:{kind}:{aggregate}:{identity}:{version}'),
        environment=environment,event_type=kind,timestamp=at,aggregate_type=aggregate,
        aggregate_id=identity,aggregate_version=version,producer='anoc' if aggregate in ('run','governance') else 'accounting',
        run_id=run_id,turn_id=turn_id,trace_id=trace_id,payload_safe=AuditPayload(
            source_record_id=identity,source_record_version=source_version,status=status,agent=agent,
            session_id=session_id,model_attempt_id=model_attempt_id,agent_execution_id=agent_execution_id))

def canonical_digest(event):
    import json
    from hashlib import sha256
    return sha256(json.dumps(event.model_dump(mode='json'),sort_keys=True,
        separators=(',', ':')).encode()).hexdigest()

class Capture:
    async def append(self,session,event):
        if not session.in_transaction(): raise ValueError('Caller transaction required')
        event=decode_event(event.model_dump_json(warnings='none').encode(),environment=event.environment)
        if not isinstance(event,AuditEvent) or event.event_type not in RUNTIME_TYPES:
            raise ValueError('Canonical audit required')
        # Already sink-validated above: hash the validated canonical representation once.
        digest=canonical_digest(event);now=utcnow()
        values=dict(event_id=str(event.event_id),event_schema_version=1,event_class='AUDIT',
            event_type=event.event_type,environment=event.environment,aggregate_type=event.aggregate_type,
            aggregate_id=event.aggregate_id,aggregate_version=event.aggregate_version,
            run_id=str(event.run_id) if event.run_id else None,turn_id=event.turn_id,trace_id=event.trace_id,
            occurred_at=event.timestamp,producer=event.producer,
            safe_payload=event.payload_safe.model_dump(mode='json'),payload_hash=digest,created_at=now)
        result=await session.execute(insert_for(session,AuditRow).values(**values)
            .on_conflict_do_nothing(index_elements=['event_id']).returning(AuditRow.event_id))
        inserted=result.scalar_one_or_none() is not None
        if not inserted:
            saved=await session.scalar(select(AuditRow.payload_hash).where(AuditRow.event_id==str(event.event_id)))
            if saved!=digest: raise ValueError('Audit identity conflict')
        delivery_values=dict(values)
        if event.payload_safe.execution is not None:delivery_values["safe_payload"]={}
        await session.execute(insert_for(session,OutboxRow).values(**delivery_values,publication_state='PENDING',
            attempt_count=0,available_at=now).on_conflict_do_nothing(index_elements=['event_id']))
        return inserted
