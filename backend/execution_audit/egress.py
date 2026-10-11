"""Opt-in controlled final Teams egress. Policy is ALWAYS authorize_write's decision.

No retries, no reconstruction of action payload from audit, no worker dispatch loop.
"""
from contextvars import ContextVar
from dataclasses import dataclass
from uuid import uuid4
from sqlalchemy import select
from backend.gateway.safe_error import SafeError,SafeErrorException
from backend.observability.projection import utcnow
from .models import ActionExecutionRecord
from .journal import event
from .runtime import current,references,agent_id

_active=ContextVar('controlled_action_egress',default=None)

def unavailable():
    return SafeErrorException(SafeError(error_code='connector_unavailable',user_message='Action audit storage is unavailable.'))

@dataclass
class Handle:
    runtime: object
    execution_id: str
    authorization_id: str
    operation: str
    refs: dict
    action_id: str | None
    request_id: str
    entered: bool=False
    used: bool=False
    acknowledged: bool=False

async def _decision(handle,authorization):
    runtime=handle.runtime;now=utcnow()
    async with runtime.journal.sessions() as s,s.begin():
        fact=event('authorization.decided',runtime.journal.environment,handle.authorization_id,
            at=now,consistency_class='D',authorization_id=handle.authorization_id,
            action_id=handle.action_id,approval_id=handle.action_id,
            decision='ALLOW' if authorization.authorized else 'DENY',outcome='AUTHORIZED' if authorization.authorized else 'DENIED',
            reason_code=authorization.reason.value if authorization.reason else None,policy_version='teams-write-v1',**handle.refs)
        await runtime.journal.append(fact,s)
        if authorization.authorized:
            s.add(ActionExecutionRecord(execution_id=handle.execution_id,environment=runtime.journal.environment,
                action_id=handle.action_id,authorization_id=handle.authorization_id,approval_id=handle.action_id,
                session_id=handle.refs.get('session_id'),run_id=handle.refs.get('run_id'),turn_id=handle.refs.get('turn_id'),
                action_type=handle.operation,safe_target_reference=handle.action_id,state='AUTHORIZED',attempt=1,
                started_at=now,updated_at=now,downstream_request_id=None))
            await runtime.journal.append(_fact(handle,'action.authorized','AUTHORIZED',1,now),s)

def _fact(handle,kind,outcome,version,at):
    return event(kind,handle.runtime.journal.environment,handle.execution_id,version,at=at,
        consistency_class='D',authorization_id=handle.authorization_id,attempt_id=handle.execution_id,
        action_id=handle.action_id,approval_id=handle.action_id,safe_target_reference=handle.action_id,
        outcome=outcome,failure_code='GATEWAY_OUTCOME_UNKNOWN' if outcome=='UNKNOWN' else None,**handle.refs)

async def _transition(handle,kind,state,version,expected):
    now=utcnow()
    async with handle.runtime.journal.sessions() as s,s.begin():
        row=(await s.execute(select(ActionExecutionRecord).where(ActionExecutionRecord.execution_id==handle.execution_id,
            ActionExecutionRecord.environment==handle.runtime.journal.environment).with_for_update())).scalar_one()
        if row.state not in expected:raise ValueError('Action transition conflict')
        row.state=state;row.updated_at=now
        if state=='DISPATCHING':row.downstream_request_id=handle.request_id
        await handle.runtime.journal.append(_fact(handle,kind,state,version,now),s)

def decision(operation,authorization):
    """Called ONLY after existing policy gate. Denied decisions cannot create action intent."""
    runtime=current()
    if runtime is None or not runtime.controlled_egress:return None
    if operation not in ('teams.createChat','teams.sendMessage'):raise unavailable()
    proposal=authorization.proposal
    refs=references()
    if agent_id():refs['agent_execution_id']=agent_id()
    handle=Handle(runtime,str(uuid4()),str(uuid4()),operation,refs,
                  proposal.proposal_id if proposal else None,str(uuid4()))
    try:runtime.sync(_decision(handle,authorization))
    except Exception:raise unavailable() from None
    return handle if authorization.authorized else None

def dispatch(handle,call):
    if handle is None:return call() # Existing disabled behavior, exactly one call.
    try:handle.runtime.sync(_transition(handle,'action.dispatch_attempted','DISPATCHING',2,{'AUTHORIZED'}))
    except Exception:raise unavailable() from None
    token=_active.set(handle)
    try:
        result=call()
    except BaseException:
        kind,state=('action.outcome_unknown','UNKNOWN') if handle.entered else ('action.failed','FAILED')
        try:handle.runtime.sync(_transition(handle,kind,state,3,{'DISPATCHING'}))
        except Exception:handle.runtime.capture_failures+=1
        raise # Preserve business failure; never call again.
    finally:_active.reset(token)
    try:
        handle.runtime.sync(_transition(handle,'action.acknowledged','ACKNOWLEDGED',3,{'DISPATCHING'}))
        handle.acknowledged=True
    except Exception:
        # Side effect already happened. Audit outage must NOT suggest a resend.
        handle.runtime.capture_failures+=1
        try:handle.runtime.sync(_transition(handle,'action.outcome_unknown','UNKNOWN',3,{'DISPATCHING'}))
        except Exception:handle.runtime.capture_failures+=1
    return result

def completed(handle):
    if handle is None or not handle.acknowledged:return
    try:handle.runtime.sync(_transition(handle,'action.completed','COMPLETED',4,{'ACKNOWLEDGED'}))
    except Exception:handle.runtime.capture_failures+=1

def gateway_guard(operation):
    runtime=current()
    if runtime is None or not runtime.controlled_egress or operation not in ('teams.createChat','teams.sendMessage'):return
    handle=_active.get()
    if handle is None or handle.operation!=operation or handle.used:
        raise SafeErrorException(SafeError(error_code='authorization_error',user_message='Controlled action egress is required.'))
    handle.used=True

def gateway_request_id(operation):
    handle=_active.get()
    return handle.request_id if handle and handle.operation==operation else str(uuid4())

def gateway_entered(operation):
    handle=_active.get()
    if handle and handle.operation==operation:handle.entered=True
