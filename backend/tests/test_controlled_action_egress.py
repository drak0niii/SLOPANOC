"""Strict pre-egress durability, business compatibility, and no external replay."""
import asyncio
from uuid import uuid4
import pytest
from sqlalchemy import select
from backend.tests.test_execution_audit_journal import journal,rows
from backend.tests.test_hybrid_phase1_runtime import pipeline
from backend.tests.test_teams_execute_write import _approved_send_message_session,_FakeToolContext,_install_gateway
from backend.tests._fakes import FakeResponse
from backend.gateway import power_automate_client as gateway
from backend.tools.teams.execute_write import teams_send_message
from backend.execution_audit.models import ActionExecutionRecord
from backend.observability.hybrid.models import AuditRow
from backend.execution_audit.runtime import correlation
from backend.approval.service import load_active_proposal

async def invoke(state):
    with correlation(session_id='actual-session',run_id=str(uuid4()),turn_id=str(uuid4())):
        return await asyncio.to_thread(teams_send_message,'c1','Hello team',_FakeToolContext(state))

@pytest.mark.asyncio
async def test_success_same_payload_policy_and_single_send(journal,monkeypatch):
    db,p,j,runtime=journal;state=_approved_send_message_session()
    spy=_install_gateway(monkeypatch,FakeResponse(200,{}))
    result=await invoke(state)
    assert result=={'status':'executed','chatId':'c1'} and len(spy.calls)==1
    assert spy.calls[0]['message']=='Hello team'
    assert load_active_proposal(state).status.value=='consumed'
    row=(await rows(db,ActionExecutionRecord))[0]
    assert row.state=='COMPLETED' and row.downstream_request_id==spy.calls[0]['requestId']
    facts=await rows(db,AuditRow)
    assert {e.event_type for e in facts if not e.event_type.startswith('tool.')}=={
        'authorization.decided','action.authorized','action.dispatch_attempted','action.acknowledged','action.completed'}
    assert len([e for e in facts if e.event_type=='tool.execution.started'])==1
    assert 'Hello team' not in str([e.safe_payload for e in facts])
    # Audit transport replay never invokes gateway/business work.
    await p.tick();await p.tick()
    assert len(spy.calls)==1

@pytest.mark.asyncio
async def test_denied_decision_never_dispatches_or_creates_intent(journal,monkeypatch):
    db,p,j,runtime=journal
    spy=_install_gateway(monkeypatch,FakeResponse(200,{}))
    result=await invoke({})
    assert result['error']['errorCode']=='authorization_error' and not spy.calls
    assert not await rows(db,ActionExecutionRecord)
    receipt=next(e for e in await rows(db,AuditRow) if e.event_type=='authorization.decided')
    assert receipt.safe_payload['execution']['decision']=='DENY'

@pytest.mark.asyncio
async def test_pre_egress_storage_failure_blocks_send(journal,monkeypatch):
    db,p,j,runtime=journal;spy=_install_gateway(monkeypatch,FakeResponse(200,{}))
    async def fail(*args,**kwargs):raise RuntimeError('SECRET SQL')
    monkeypatch.setattr(j,'append',fail)
    state=_approved_send_message_session();result=await invoke(state)
    assert result['error']['errorCode']=='connector_unavailable' and not spy.calls
    assert load_active_proposal(state).status.value=='approved'
    assert not await rows(db,ActionExecutionRecord)

@pytest.mark.asyncio
@pytest.mark.parametrize('exception',[gateway.requests.exceptions.Timeout('PRIVATE URL'),gateway.requests.exceptions.ConnectionError('PRIVATE URL')])
async def test_remote_ambiguity_unknown_not_failed_or_replayed(journal,monkeypatch,exception):
    db,p,j,runtime=journal;calls=[]
    def lost(*args,**kwargs):calls.append(1);raise exception
    monkeypatch.setattr(gateway.requests,'post',lost)
    state=_approved_send_message_session();result=await invoke(state)
    assert result['error']['errorCode']=='run_failure' and len(calls)==1
    row=(await rows(db,ActionExecutionRecord))[0]
    assert row.state=='UNKNOWN'
    facts=await rows(db,AuditRow)
    assert any(e.event_type=='action.outcome_unknown' for e in facts)
    assert not any(e.event_type=='action.failed' for e in facts)
    assert load_active_proposal(state).status.value=='approved'
    await p.tick();await p.tick();assert len(calls)==1

@pytest.mark.asyncio
async def test_ack_persistence_failure_preserves_success_and_never_resends(journal,monkeypatch):
    db,p,j,runtime=journal;spy=_install_gateway(monkeypatch,FakeResponse(200,{}));original=j.append
    async def fail_ack(fact,session=None):
        if fact.event_type=='action.acknowledged':raise RuntimeError('ack store unavailable')
        return await original(fact,session)
    monkeypatch.setattr(j,'append',fail_ack)
    state=_approved_send_message_session();result=await invoke(state)
    assert result['status']=='executed' and len(spy.calls)==1
    assert load_active_proposal(state).status.value=='consumed'
    assert (await rows(db,ActionExecutionRecord))[0].state=='UNKNOWN'
    assert runtime.capture_failures>=1
    await p.tick();assert len(spy.calls)==1

@pytest.mark.asyncio
async def test_naked_gateway_write_blocked_in_controlled_mode(journal,monkeypatch):
    spy=_install_gateway(monkeypatch,FakeResponse(200,{}))
    from backend.gateway.safe_error import SafeErrorException
    with pytest.raises(SafeErrorException):await asyncio.to_thread(gateway.PowerAutomateClient().send_message,'c1','Hello team')
    assert not spy.calls

@pytest.mark.asyncio
async def test_definite_pre_network_failure_is_failed(journal,monkeypatch):
    db,p,j,runtime=journal
    from backend.gateway.safe_error import internal_error
    def fail_before(*args):raise internal_error('Connector unavailable.')
    monkeypatch.setattr(gateway.PowerAutomateClient,'send_message',fail_before)
    result=await invoke(_approved_send_message_session())
    assert 'error' in result
    assert (await rows(db,ActionExecutionRecord))[0].state=='FAILED'
    assert any(e.event_type=='action.failed' for e in await rows(db,AuditRow))
