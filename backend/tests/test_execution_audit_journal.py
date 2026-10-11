"""Actual owners, transaction classes, no content/replay, and durable timeline proofs."""
import asyncio
from datetime import timedelta
from uuid import uuid4
import pytest
import pytest_asyncio
from sqlalchemy import select,func,update
from backend.tests.test_hybrid_phase1_runtime import pipeline
from backend.execution_audit.journal import Journal,event
from backend.execution_audit.runtime import install,release,Activity,correlation
from backend.execution_audit.models import ActionExecutionRecord,AuditTimelineRecord,ToolExecutionRecord
from backend.observability.hybrid.models import AuditRow,OutboxRow
from backend.execution_audit.read import AuditReadRepository
from backend.observability.projection import utcnow

@pytest_asyncio.fixture
async def journal(pipeline):
    db,p,cfg=pipeline
    j=Journal(db.sessions,cfg.environment);runtime=install(j,controlled_egress=True)
    try:yield db,p,j,runtime
    finally:release(runtime)

async def rows(db,model):
    async with db.sessions() as s:return (await s.execute(select(model))).scalars().all()

@pytest.mark.asyncio
async def test_fact_atomic_rollback_and_identity_conflict(journal):
    db,p,j,runtime=journal
    identity=str(uuid4())
    f=event('retrieval.started','development',identity,retrieval_id=identity,consistency_class='C',outcome='STARTED')
    async with db.sessions() as s:
        await s.begin();await j.append(f,s);await s.rollback()
    assert not await rows(db,AuditRow) and not await rows(db,OutboxRow)
    assert await j.append(f);assert not await j.append(f)
    conflicting=f.model_copy(update={'timestamp':utcnow()})
    with pytest.raises(ValueError,match='identity conflict'):await j.append(conflicting)
    assert len(await rows(db,AuditRow))==len(await rows(db,OutboxRow))==1

@pytest.mark.asyncio
@pytest.mark.parametrize('domain',['agent','tool','retrieval'])
@pytest.mark.parametrize('error',[None,RuntimeError('PRIVATE TEXT'),TimeoutError('SECRET'),asyncio.CancelledError()])
async def test_activity_lifecycle_and_reverse_projection(journal,domain,error):
    db,p,j,runtime=journal
    run=str(uuid4());turn=str(uuid4())
    with correlation(session_id='real-session',run_id=run,turn_id=turn):
        activity=Activity(domain,**({'tool_name':'teams_get_messages'} if domain=='tool' else {}))
        await activity.start();await activity.finish(error)
    events=await rows(db,AuditRow);assert len(events)==2
    assert all(e.run_id==run and e.turn_id==turn for e in events)
    from backend.observability.hybrid.store import decode_row
    for e in reversed(events):await p.projector.store.apply(decode_row(e))
    view=(await rows(db,ToolExecutionRecord))[0]
    assert view.started_at and view.terminal_at and view.execution_id==activity.identity
    assert view.outcome==('COMPLETED' if error is None else 'TIMEOUT' if isinstance(error,TimeoutError) else 'CANCELLED' if isinstance(error,asyncio.CancelledError) else 'FAILED')
    assert 'PRIVATE TEXT' not in str([e.safe_payload for e in events])
    assert len(await rows(db,AuditTimelineRecord))==2

@pytest.mark.asyncio
async def test_class_c_capture_failure_preserves_result(journal,monkeypatch):
    db,p,j,runtime=journal
    async def fail(*a,**k):raise RuntimeError('PRIVATE SQL')
    monkeypatch.setattr(j,'append',fail)
    activity=Activity('tool',tool_name='real_registered_tool')
    await activity.start();await activity.finish()
    assert runtime.capture_failures==2
    assert not await rows(db,AuditRow)

@pytest.mark.asyncio
async def test_adk_durable_handoff_reconciles_after_destination_failure(journal,monkeypatch):
    from google.adk.sessions import DatabaseSessionService
    from backend.api.session_service import ApiSessionService,APP_NAME
    from backend.execution_audit.adk_handoff import reconcile,KEY
    from backend.approval.service import create_action_proposal,approve_proposal,PENDING_ACTION_PROPOSAL_STATE_KEY
    db,p,j,runtime=journal
    # Local isolated second ADK store proves public metadata is persisted durably.
    adk=DatabaseSessionService('sqlite+aiosqlite:///:memory:')
    service=ApiSessionService(adk)
    session_id=await service.create_session();session=await service.get_session(session_id)
    proposal=create_action_proposal('teams.sendMessage',{'chatId':'real-target','message':'PRIVATE TEAMS BODY'},session.state)
    original=j.append
    async def fail(*a,**k):raise RuntimeError('injected sink outage')
    monkeypatch.setattr(j,'append',fail)
    await service.persist_state_delta(session,{PENDING_ACTION_PROPOSAL_STATE_KEY:session.state[PENDING_ACTION_PROPOSAL_STATE_KEY]})
    stored=await adk.get_session(app_name=APP_NAME,user_id=session.user_id,session_id=session_id)
    assert stored.state[PENDING_ACTION_PROPOSAL_STATE_KEY]['status']=='pending'
    assert stored.events[-1].content is None and KEY in stored.events[-1].custom_metadata
    assert 'PRIVATE TEAMS BODY' not in str(stored.events[-1].custom_metadata)
    monkeypatch.setattr(j,'append',original)
    assert await reconcile(stored,j)==1
    assert await reconcile(stored,j)==0
    approve_proposal(proposal.proposal_id,stored.state)
    await service.persist_state_delta(stored,{PENDING_ACTION_PROPOSAL_STATE_KEY:stored.state[PENDING_ACTION_PROPOSAL_STATE_KEY]})
    approvals=[e for e in await rows(db,AuditRow) if e.event_type.startswith('approval.')]
    assert {e.event_type for e in approvals}=={'approval.requested','approval.approved'}
    assert all(e.safe_payload['execution']['consistency_class']=='B' for e in approvals)
    await adk.close()

@pytest.mark.asyncio
async def test_adk_provenance_is_reference_only(journal):
    from backend.api.session_service import ApiSessionService
    db,p,j,runtime=journal
    from google.adk.sessions import DatabaseSessionService
    adk=DatabaseSessionService('sqlite+aiosqlite:///:memory:')
    service=ApiSessionService(adk);sid=await service.create_session();session=await service.get_session(sid)
    turn=str(uuid4())
    await service.persist_state_delta(session,{'turn_source_references':{turn:{'knowledge_sources':[{'title':'PRIVATE DOCUMENT TITLE','body':'SECRET BODY'}]}}})
    facts=[e for e in await rows(db,AuditRow) if e.event_type=='evidence.selection.recorded']
    assert len(facts)==1 and facts[0].turn_id==turn
    assert 'SECRET BODY' not in str(facts[0].safe_payload)
    assert facts[0].safe_payload['execution']['selected_count']==1
    await adk.close()

@pytest.mark.asyncio
async def test_timeline_ties_snapshot_late_arrival_and_cursor_scope(journal):
    db,p,j,runtime=journal;run=str(uuid4());now=utcnow();facts=[]
    for _ in range(3):
        identity=str(uuid4());f=event('retrieval.started','development',identity,at=now,run_id=run,
            retrieval_id=identity,consistency_class='C',outcome='STARTED');facts.append(f);await j.append(f)
    await p.tick()
    reader=AuditReadRepository(db.sessions)
    first=await reader.timeline('development',run,2);assert first.next_cursor
    lateid=str(uuid4());late=event('retrieval.started','development',lateid,at=now-timedelta(seconds=1),run_id=run,
        retrieval_id=lateid,consistency_class='C',outcome='STARTED')
    await j.append(late);await p.tick()
    second=await reader.timeline('development',run,2,first.next_cursor)
    assert len(second.events)==1
    combined=[str(e.event.event_id) for e in first.events+second.events]
    assert combined==sorted(str(f.event_id) for f in facts)
    refreshed=await reader.timeline('development',run,100)
    assert refreshed.events[0].event.event_id==late.event_id and refreshed.completeness=='PARTIAL'
    with pytest.raises(ValueError):await reader.timeline('test',run,2,first.next_cursor)

@pytest.mark.asyncio
@pytest.mark.parametrize('forbidden',['prompt','reasoning','documents','teams_body','authorization_header','password','command','arguments','results'])
async def test_all_journal_sinks_reject_content(journal,forbidden):
    db,p,j,runtime=journal
    from backend.observability.hybrid.contracts import decode_event
    import json
    identity=str(uuid4())
    f=event('retrieval.started','development',identity,retrieval_id=identity,consistency_class='C',outcome='STARTED')
    raw=f.model_dump(mode='json');raw['payload_safe']['execution'][forbidden]='SECRET'
    with pytest.raises(ValueError):decode_event(json.dumps(raw).encode(),environment='development')
    assert not await rows(db,AuditRow)

@pytest.mark.asyncio
async def test_read_plane_audit_rbac_and_environment(journal):
    import httpx
    from backend.api.observability_app import create_app
    from backend.observability.read_config import ReadConfiguration
    from backend.observability.service import Service
    from backend.tests.test_observability_read_plane import authorize
    from backend.tests._m7_storage import state,config
    from backend.observability.schemas import Role
    db,p,j,runtime=journal;run=state();await p.run_repository.persist(run);await p.tick()
    app=create_app(config=config(),settings=ReadConfiguration(),isolated_test=True)
    service=Service(p.run_repository,config());service.execution_audit_reads=AuditReadRepository(db.sessions)
    app.state.observability_service=service
    authorize(app,Role.OPERATOR)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app),base_url='http://reader') as client:
        path='/api/observability/runs/'+run.run_id+'/audit'
        response=await client.get(path)
        assert response.status_code==200,response.text
        assert response.json()['completeness']=='PARTIAL'
        authorize(app,Role.FINOPS)
        assert (await client.get(path)).status_code==403
        authorize(app,Role.OPERATOR,('production',))
        assert (await client.get(path)).status_code==403

@pytest.mark.asyncio
async def test_false_atomic_claim_rejected(journal):
    identity=str(uuid4())
    with pytest.raises(ValueError,match='False consistency'):
        event('tool.execution.started','development',identity,tool_execution_id=identity,
            consistency_class='A',outcome='STARTED')

@pytest.mark.asyncio
async def test_case_selection_same_cas_transaction(journal):
    from backend.cases.db import CaseDatabase
    from backend.cases.models import Base
    from backend.cases.service import CaseService
    from backend.cases.progression_store import CaseProgressionStore,ProgressionConflict
    from backend.cases.troubleshooting_progression import TroubleshootingProgression,TroubleshootingStep
    from backend.cases.evidence_identity import EvidenceIdentity
    from backend.observability.hybrid.capture import Capture
    db,p,j,runtime=journal
    async with db.engine.begin() as c:await c.run_sync(Base.metadata.create_all)
    case_db=CaseDatabase(str(db.engine.url))
    try:
        case=(await CaseService(case_db).create_case('eng','fault','PRIVATE CASE BODY')).case_id
        store=CaseProgressionStore(case_db,audit_capture=Capture(),audit_environment='development')
        progression=TroubleshootingProgression(case_id=case)
        assert await store.save(case,progression,None,'real-session')==1
        # Test authoritative exact tuple delta, never split rendered IDs.
        step=TroubleshootingStep(step_id='real-step',fault_id='real-fault',objective='PRIVATE OBJECTIVE',selected_evidence=[EvidenceIdentity(knowledge_id='id:with:colon',version_label='v1',section_id='part:1')])
        progression=progression.model_copy(update={'steps':[step]})
        assert await store.save(case,progression,1,'real-session')==2
        assert await store.save(case,progression,2,'real-session')==3
        with pytest.raises(ProgressionConflict):await store.save(case,progression,1,'real-session')
        selection=[e for e in await rows(db,AuditRow) if e.event_type=='evidence.selection.recorded']
        assert len(selection)==1 and selection[0].aggregate_version==2
        assert selection[0].safe_payload['execution']['consistency_class']=='A'
    finally:await case_db.close()

@pytest.mark.asyncio
async def test_real_adk_agent_tool_retrieval_and_model_reference_journey(journal):
    from google.adk.sessions import DatabaseSessionService
    from google.adk.runners import Runner
    from google.genai import types
    from backend.api.session_service import ApiSessionService,APP_NAME
    from backend.observability.adk_adapter import ObservedAgent
    from backend.tests.test_observability_agent_instrumentation import synthetic
    from backend.knowledge.retrieval.service import KnowledgeRetrievalService
    from backend.knowledge.retrieval.contracts import KnowledgeRetrievalQuery
    from backend.tests._m7_storage import state,change
    from backend.tests.test_finops_runtime_ledger import identity,payload
    from backend.observability.finops.contracts import Payload
    from backend.execution_audit.runtime import agent_id
    db,p,j,runtime=journal
    adk=DatabaseSessionService('sqlite+aiosqlite:///:memory:');sessions=ApiSessionService(adk)
    sid=await sessions.create_session();run=state(session_id=sid);await p.run_repository.persist(run)
    class EmptyKnowledge:
        async def list_all(self):return []
    retrieval=KnowledgeRetrievalService(EmptyKnowledge())
    from google.adk.tools.tool_context import ToolContext
    from google.adk.models.llm_response import LlmResponse
    seen=[]
    async def knowledge_search(tool_context: ToolContext):
        result=await retrieval.retrieve(KnowledgeRetrievalQuery(query_text='PRIVATE QUERY',as_of=utcnow(),limit=1))
        assert not result.items
        # Physical accounting attempt uses its real Identity; only actual active parent
        # handle reference added to usage, never a dashboard-generated relationship.
        attempt=identity();await p.accounting_repository.admit(attempt);await p.accounting_repository.start(attempt)
        usage=Payload.model_validate({**payload(attempt).model_dump(mode='python'),'run_id':run.run_id,
            'turn_id':tool_context.invocation_id,'agent_execution_id':agent_id()})
        await p.accounting_repository.finished(attempt,usage.provider_finished_at)
        await p.accounting_repository.capture(usage);await p.accounting_repository.materialize(attempt.event_id)
        seen.append((str(attempt.attempt_id),agent_id()))
        return {'items': []}
    calls=0
    def invoke_tool(callback_context,llm_request):
        nonlocal calls
        calls+=1
        if calls==1:
            return LlmResponse(content=types.Content(role='model',parts=[types.Part(function_call=types.FunctionCall(name='knowledge_search',args={}))]))
        return synthetic(callback_context,llm_request)
    agent=ObservedAgent(name='team_manager',model='gemini-2.5-flash',tools=[knowledge_search],before_model_callback=invoke_tool)
    runner=Runner(app_name=APP_NAME,agent=agent,session_service=adk)
    try:
        with correlation(session_id=sid,run_id=run.run_id):
            events=[e async for e in runner.run_async(user_id='api-user',session_id=sid,new_message=types.Content(role='user',parts=[types.Part(text='PRIVATE PROMPT')]))]
        actual_turn=events[0].invocation_id
        await p.run_repository.persist(change(run,state_version=2,turn_id=actual_turn,turn_id_origin='execution'))
        facts=await rows(db,AuditRow)
        started=next(e for e in facts if e.event_type=='agent.execution.started')
        usage=next(e for e in facts if e.event_type=='model.usage.captured')
        retrieved=next(e for e in facts if e.event_type=='retrieval.completed')
        assert started.safe_payload['execution']['agent_execution_id']==usage.safe_payload['agent_execution_id']==seen[0][1]
        assert usage.safe_payload['model_attempt_id']==seen[0][0]
        assert retrieved.safe_payload['execution']['agent_execution_id']==seen[0][1]
        tool=next(e for e in facts if e.event_type=='tool.execution.started')
        assert retrieved.safe_payload['tool_execution_id']==tool.safe_payload['tool_execution_id']
        assert tool.safe_payload['execution']['agent_execution_id']==seen[0][1]
        assert started.run_id==retrieved.run_id==usage.run_id==tool.run_id==run.run_id
        assert started.turn_id==retrieved.turn_id==usage.turn_id==actual_turn
        assert all(secret not in str([e.safe_payload for e in facts]) for secret in ('PRIVATE PROMPT','PRIVATE QUERY'))
        await p.tick()
        timeline=await AuditReadRepository(db.sessions).timeline('development',run.run_id)
        assert any(e.event.event_type=='retrieval.completed' for e in timeline.events)
        assert agent_id() is None
    finally:await runner.close()

@pytest.mark.asyncio
async def test_closed_copied_execution_context_does_not_invent_parent(journal):
    from backend.execution_audit.runtime import agent_id
    parent=Activity('agent');await parent.start();gate=asyncio.Event()
    async def late():
        await gate.wait()
        assert agent_id() is None
    with parent.attached():task=asyncio.create_task(late())
    await parent.finish();gate.set();await task


def test_legacy_financial_digest_unchanged_when_execution_reference_absent():
    from backend.tests.test_finops_runtime_ledger import identity,payload
    import json,hashlib
    p=payload(identity());body=p.model_dump(mode='json')
    assert 'agent_execution_id' not in body
    body.pop('observed_at')
    assert p.digest()==hashlib.sha256(json.dumps(body,sort_keys=True,separators=(',',':')).encode()).hexdigest()


@pytest.mark.asyncio
async def test_unresolved_dispatch_read_is_unknown_not_replay(journal):
    db,p,j,runtime=journal;run=str(uuid4());identity=str(uuid4());now=utcnow()
    async with db.sessions() as s,s.begin():
        s.add(ActionExecutionRecord(execution_id=identity,environment='development',run_id=run,
            authorization_id=str(uuid4()),action_type='teams.sendMessage',state='DISPATCHING',
            attempt=1,started_at=now,updated_at=now))
    page=await AuditReadRepository(db.sessions).timeline('development',run)
    assert len(page.actions)==1 and page.actions[0].state=='DISPATCHING'
    assert page.actions[0].outcome=='UNKNOWN' and page.completeness=='PARTIAL'
    assert not await rows(db,AuditRow)  # Read never fabricates acknowledgement or retries action.
