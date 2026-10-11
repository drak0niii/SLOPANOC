"""Bounded canonical chronology; snapshot cursors explicitly exclude later arrivals."""
import base64,json
from typing import Literal
from datetime import datetime
from uuid import UUID
from pydantic import AwareDatetime,Field
from sqlalchemy import select,and_,or_
from backend.observability.schemas import Contract
from backend.observability.hybrid.contracts import AuditEvent,decode_event,Environment
from backend.observability.projection import utcnow
from .models import AuditTimelineRecord,ActionExecutionRecord

class Entry(Contract):
    event: AuditEvent
    processed_at: AwareDatetime

class ActionSummary(Contract):
    execution_id: UUID
    state: Literal['AUTHORIZED','DISPATCHING','ACKNOWLEDGED','COMPLETED','FAILED','UNKNOWN']
    outcome: Literal['AUTHORIZED','UNKNOWN','ACKNOWLEDGED','COMPLETED','FAILED']
    updated_at: AwareDatetime

class AuditPage(Contract):
    schema_version: Literal[1]=1
    run_id: UUID
    environment: Environment
    as_of: AwareDatetime
    events: tuple[Entry,...]
    actions: tuple[ActionSummary,...]=()
    next_cursor: str | None=None
    completeness: Literal['PARTIAL']='PARTIAL'
    consistency_notice: str='Occurred-time order; class B/C/D are not atomic with business state. Later arrivals require a refreshed snapshot. Action summaries are current state, bounded to 100; DISPATCHING has UNKNOWN outcome and must not trigger replay.'

class AuditReadRepository:
    def __init__(self,sessions):self.sessions=sessions
    async def timeline(self,environment,run_id,limit=50,cursor=None):
        if environment not in ('local','development','test','staging','production') or not 1<=limit<=100:raise ValueError('Audit query bounds')
        if str(UUID(run_id))!=run_id:raise ValueError('Run identifier')
        now=utcnow();as_of=now;after=None
        scope=f'{environment}:{run_id}'
        if cursor:
            if len(cursor)>512:raise ValueError('Cursor bounds')
            raw=json.loads(base64.b64decode(cursor,altchars=b'-_',validate=True))
            if set(raw)!= {'scope','as_of','occurred_at','event_id'} or raw['scope']!=scope:raise ValueError('Cursor scope')
            as_of=datetime.fromisoformat(raw['as_of']);when=datetime.fromisoformat(raw['occurred_at'])
            if as_of.tzinfo is None or when.tzinfo is None or as_of>now:raise ValueError('Cursor time')
            identifier=str(UUID(raw['event_id']));after=(when,identifier)
        stmt=select(AuditTimelineRecord).where(AuditTimelineRecord.environment==environment,
            AuditTimelineRecord.run_id==run_id,AuditTimelineRecord.processed_at<=as_of)
        if after:
            stmt=stmt.where(or_(AuditTimelineRecord.occurred_at>after[0],and_(AuditTimelineRecord.occurred_at==after[0],AuditTimelineRecord.event_id>after[1])))
        async with self.sessions() as s:
            rows=(await s.execute(stmt.order_by(AuditTimelineRecord.occurred_at,AuditTimelineRecord.event_id).limit(limit+1))).scalars().all()
            actions=(await s.execute(select(ActionExecutionRecord).where(
                ActionExecutionRecord.environment==environment,ActionExecutionRecord.run_id==run_id
            ).order_by(ActionExecutionRecord.started_at,ActionExecutionRecord.execution_id).limit(100))).scalars().all()
        summaries=tuple(ActionSummary(execution_id=r.execution_id,state=r.state,
            outcome='UNKNOWN' if r.state=='DISPATCHING' else r.state,updated_at=r.updated_at) for r in actions)
        more=len(rows)>limit;rows=rows[:limit]
        entries=tuple(Entry(event=decode_event(json.dumps(r.event).encode(),environment=environment),processed_at=r.processed_at) for r in rows)
        next_cursor=None
        if more:
            row=rows[-1]
            next_cursor=base64.urlsafe_b64encode(json.dumps(dict(scope=scope,as_of=as_of.isoformat(),occurred_at=row.occurred_at.isoformat(),event_id=row.event_id),separators=(',',':')).encode()).decode()
        return AuditPage(run_id=run_id,environment=environment,as_of=as_of,events=entries,actions=summaries,next_cursor=next_cursor)
