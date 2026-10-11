"""One journal authority: existing immutable critical audit table, plus existing outbox.

No second copy of business truth. Existing v1 envelopes/hashes remain compatible.
"""
from uuid import uuid5
from backend.observability.hybrid.capture import Capture,NAMESPACE
from backend.observability.hybrid.contracts import AuditEvent,AuditPayload
from backend.observability.projection import utcnow
from .contracts import Fact,JOURNAL_EVENTS

def event(kind,environment,source_id,version=1,*,at=None,session_id=None,run_id=None,turn_id=None,trace_id=None,
          tool_execution_id=None,approval_id=None,action_id=None,agent=None,**facts):
    if kind not in JOURNAL_EVENTS:raise ValueError('Unknown journal event')
    fact=Fact(execution_domain=kind.split('.')[0],**facts)
    return AuditEvent(event_id=uuid5(NAMESPACE,f'journal:v1:{environment}:{kind}:{source_id}:{version}'),
        environment=environment,event_type=kind,timestamp=at or utcnow(),aggregate_type='governance',
        aggregate_id=str(source_id),aggregate_version=version,producer='anoc',run_id=run_id,
        turn_id=turn_id,trace_id=trace_id,payload_safe=AuditPayload(source_record_id=str(source_id),source_record_version=version,
            session_id=session_id,tool_execution_id=tool_execution_id,approval_id=approval_id,action_id=action_id,
            agent=agent,execution=fact))

class Journal:
    def __init__(self,sessions,environment):self.sessions,self.environment=sessions,environment
    async def append(self,fact,session=None):
        if fact.environment!=self.environment:raise ValueError('Journal environment mismatch')
        if session is not None:return await Capture().append(session,fact)
        async with self.sessions() as s,s.begin():return await Capture().append(s,fact)
