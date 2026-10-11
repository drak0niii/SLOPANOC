"""Honest audit boundary: unsupported business lifecycles cannot be invented."""
from uuid import uuid4
import pytest
from backend.observability.hybrid.capture import make_event, RUNTIME_TYPES
from backend.observability.hybrid.contracts import decode_event
from backend.observability.projection import utcnow

@pytest.mark.parametrize('kind',[
    'session.created','session.resumed','session.closed','agent.started','agent.completed',
    'agent.failed','retrieval.started','retrieval.completed','evidence.selected',
    'tool.started','tool.completed','tool.failed','approval.requested','approval.approved',
    'approval.rejected','approval.expired','command.authorization_decided',
    'action.dispatch_attempted','action.dispatch_acknowledged','action.completed','action.failed'])
def test_missing_owner_events_are_not_admitted(kind):
    with pytest.raises(ValueError,match='No canonical event owner'):
        make_event(kind,'development','run',str(uuid4()),1,utcnow())


def test_reference_payload_does_not_invent_execution_correlation():
    run=str(uuid4())
    event=make_event('run.accepted','development','run',run,1,utcnow(),run_id=run,status='RUNNING',
                     session_id='existing-session',turn_id='existing-turn',trace_id='a'*32)
    result=decode_event(event.model_dump_json().encode(),environment='development')
    assert result.payload_safe.session_id=='existing-session'
    for field in ('tool_execution_id','approval_id','action_id','model_attempt_id'):
        assert getattr(result.payload_safe,field) is None
        assert field not in result.payload_safe.model_dump(mode='json')
    from backend.observability.hybrid.capture import LEGACY_TYPES
    assert len(LEGACY_TYPES)==9
    assert LEGACY_TYPES <= RUNTIME_TYPES

@pytest.mark.parametrize('field',[
    'prompt','reasoning','documents','model_response','teams_body','password','token',
    'tool_arguments','tool_results','command_body'])
def test_forbidden_content_is_rejected_at_event_sink(field):
    run=str(uuid4())
    event=make_event('run.accepted','development','run',run,1,utcnow(),run_id=run,status='RUNNING')
    data=event.model_dump(mode='json');data['payload_safe'][field]='forbidden fixture body'
    import json
    with pytest.raises(ValueError):decode_event(json.dumps(data).encode(),environment='development')

# Reuse the isolated real-owner pipeline fixture, never a runtime database URL.
from backend.tests.test_hybrid_phase1_runtime import pipeline

@pytest.mark.asyncio
async def test_actual_committed_owner_correlation_and_explicit_gaps(pipeline):
    from sqlalchemy import select
    from backend.tests._m7_storage import state
    from backend.tests.test_finops_runtime_ledger import identity,payload
    from backend.observability.hybrid.models import AuditRow
    from backend.observability.finops.models import LedgerRow
    db,p,cfg=pipeline
    run=state(turn_id=str(uuid4()),turn_id_origin="execution",trace_id="a"*32)
    await p.run_repository.persist(run)
    attempt=identity()
    await p.accounting_repository.admit(attempt)
    await p.accounting_repository.start(attempt)
    from backend.observability.finops.contracts import Payload
    usage=Payload.model_validate({**payload(attempt).model_dump(mode='python'),'run_id':run.run_id,'turn_id':run.turn_id,'trace_id':run.trace_id})
    await p.accounting_repository.finished(attempt,usage.provider_finished_at)
    assert await p.accounting_repository.capture(usage)=='ACCEPTED'
    assert await p.accounting_repository.materialize(attempt.event_id)=='PERSISTED'
    async with db.sessions() as s:
        events=(await s.execute(select(AuditRow))).scalars().all()
        accepted=next(e for e in events if e.event_type=='run.accepted')
        captured=next(e for e in events if e.event_type=='model.usage.captured')
        started=next(e for e in events if e.event_type=='model.attempt.started')
        ledger=await s.get(LedgerRow,attempt.event_id)
        assert accepted.safe_payload['session_id']==run.session_id
        assert accepted.run_id==captured.run_id==ledger.metadata_safe["run_id"]==run.run_id
        assert accepted.turn_id==captured.turn_id==ledger.metadata_safe["turn_id"]==run.turn_id
        assert captured.safe_payload['model_attempt_id']==str(attempt.attempt_id)
        assert captured.safe_payload['agent']==attempt.agent.value
        assert ledger.input_tokens==100
        # Identity at STARTED has no run/turn/trace; later correlation is not backfilled.
        assert started.run_id is None and started.turn_id is None and started.trace_id is None
        for e in events:
            assert not {'tool_execution_id','approval_id','action_id'} & e.safe_payload.keys()
    await p.tick()
