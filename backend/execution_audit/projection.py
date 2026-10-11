"""Derived execution records and timeline in the receipt's projector transaction."""
from sqlalchemy import select
from backend.observability.finops.repository import insert_for
from .models import AuditTimelineRecord,ToolExecutionRecord

async def apply(session,event,digest,now):
    data=dict(environment=event.environment,event_id=str(event.event_id),run_id=str(event.run_id) if event.run_id else None,
        occurred_at=event.timestamp,processed_at=now,payload_hash=digest,event=event.model_dump(mode='json'))
    await session.execute(insert_for(session,AuditTimelineRecord).values(**data).on_conflict_do_nothing(index_elements=['environment','event_id']))
    fact=event.payload_safe.execution
    if fact is None or fact.execution_domain not in ('tool','agent','retrieval'):return
    identity=event.payload_safe.tool_execution_id if fact.execution_domain=='tool' else str(fact.agent_execution_id if fact.execution_domain=='agent' else fact.retrieval_id)
    terminal=not event.event_type.endswith('started')
    data=dict(environment=event.environment,execution_id=identity,domain=fact.execution_domain,
        run_id=str(event.run_id) if event.run_id else None,session_id=event.payload_safe.session_id,
        turn_id=event.turn_id,started_at=None if terminal else event.timestamp,
        terminal_at=event.timestamp if terminal else None,outcome=fact.outcome,
        terminal_event_id=str(event.event_id) if terminal else None)
    await session.execute(insert_for(session,ToolExecutionRecord).values(**data).on_conflict_do_nothing(index_elements=['environment','execution_id']))
    row=await session.get(ToolExecutionRecord,(event.environment,identity),with_for_update=True)
    if not terminal:
        if row.started_at is None:row.started_at=event.timestamp
    elif row.terminal_event_id is None:
        row.terminal_at=event.timestamp;row.outcome=fact.outcome;row.terminal_event_id=str(event.event_id)
