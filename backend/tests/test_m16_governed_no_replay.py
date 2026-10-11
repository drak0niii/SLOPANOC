"""Canonical local turn integrates real command authority and approved write gate.

Only model HTTP and gateway POST are fake. Command egress is presentation;
operational equipment execution is not enabled in the existing architecture.
The real approval-gated Teams write is the simulated side-effecting command.
"""
import asyncio
from types import SimpleNamespace
import httpx
import pytest
from opentelemetry.exporter.otlp.proto.common.trace_encoder import encode_spans
from backend.tests.test_finops_runtime_ledger import accounting
from backend.tests.test_observability_model_provider import fake_model,payload,request
from backend.tests.test_single_invocation_command_authority import _authorized
from backend.tests._api_fakes import FakeRunner
from backend.tests._fakes import FakeResponse
from backend.tests._m16_harness import Dispatches
from backend.tests._m7_storage import close_chat_storage
from backend.api.chat_service import ChatService
from backend.api.session_service import ApiSessionService
from backend.api.command_egress import CommandAuthorization,normalize_command,enforce_command_egress
from backend.approval.service import create_action_proposal,approve_proposal
from backend.approval.policy_gate import authorize_write
from backend.tools.teams.execute_write import teams_send_message
from backend.gateway import power_automate_client as gateway
from backend.observability.config import ObservabilityConfig
from backend.observability.runtime import Runtime
from backend.observability.model_adapter import instrument_model
from backend.observability.tool_instrumentation import tool_scope
from backend.observability.turn_trace import current_turn
from backend.observability.finops.usage_ledger import install
from backend.observability.archive import FilesystemArchive,ArchiveError
from backend.observability.delivery import DeliveryBuffer,Backpressure

@pytest.mark.asyncio
@pytest.mark.parametrize('boundary',['admission','governance','provider','tool_selection','approval','command_preparation','egress'])
@pytest.mark.parametrize('fault',['telemetry','archive','accounting','delivery','recovery','retention','diagnostics'])
async def test_governed_approved_write_exactly_once(accounting,tmp_path,monkeypatch,boundary,fault):
    db,repo,ledger=accounting;install(ledger);calls=Dispatches();fired=False
    runtime=Runtime(ObservabilityConfig(observability_enabled=True,otel_enabled=True,otel_exporter_mode='local',otel_batch_interval_seconds=.01))
    archive=FilesystemArchive(tmp_path/'archive','development');materialize=repo.materialize
    def fail(*args,**kwargs):raise OSError('M16_PRIVATE_FAULT')
    async def failed_materialize(*args,**kwargs):raise OSError('M16_PRIVATE_FAULT')
    auxiliary=[];fault_observed=[];retention_listener=None
    if fault in ('retention','diagnostics'):
        from backend.observability.models import Base
        from backend.observability.repository import Repository as OperationalRepository
        from backend.observability.retention_worker import RetentionWorker
        from backend.observability.service import Service
        from backend.tests._m7_storage import state
        from backend.observability.projection import utcnow
        from backend.observability.stages import RunStatus
        from datetime import timedelta
        from sqlalchemy import event
        async with db.engine.begin() as connection:await connection.run_sync(Base.metadata.create_all)
        ops=OperationalRepository(db.sessions,runtime.config)
        old=utcnow()-timedelta(days=100)
        observed=state(status=RunStatus.FAILED,started_at=old,terminal_at=old+timedelta(seconds=1))
        await ops.persist(observed)
        retention=RetentionWorker(db.sessions,'development',batch=7)
        diagnostics=Service(ops,runtime.config);get=ops.get
        async def ancillary():
            try:
                if fault=='retention':await retention.run('exceptional_summary',utcnow(),dry_run=False)
                else:await diagnostics.query('get',observed.run_id,('development',))
            except Exception:fault_observed.append(fault)
            else:raise AssertionError('required auxiliary fault not observed')
    def inject(phase):
        nonlocal fired,retention_listener
        if phase!=boundary or fired:return
        fired=True
        if fault=='telemetry':monkeypatch.setattr(runtime.exporters['trace'],'export',fail)
        if fault=='archive':monkeypatch.setattr(archive,'submit',fail)
        if fault in ('accounting','recovery'):monkeypatch.setattr(repo,'materialize',failed_materialize)
        if fault=='retention':
            def interrupted(conn,cursor,statement,parameters,context,many):
                if statement.lower().startswith('delete'):fail()
            retention_listener=interrupted
            event.listen(db.engine.sync_engine,'after_cursor_execute',interrupted)
            auxiliary.append(asyncio.create_task(ancillary()))
        if fault=='diagnostics':
            monkeypatch.setattr(ops,'get',failed_materialize)
            auxiliary.append(asyncio.create_task(ancillary()))
        if fault=='delivery':
            put=DeliveryBuffer.put
            async def broken(self,event):
                if getattr(getattr(event,'type',None),'value',None)=='message.completed':raise Backpressure()
                return await put(self,event)
            monkeypatch.setattr(DeliveryBuffer,'put',broken)
    admitted=repo.admit
    async def admit(*args,**kwargs):
        result=await admitted(*args,**kwargs);inject('admission');return result
    monkeypatch.setattr(repo,'admit',admit)
    def post(*args,**kwargs):
        calls.hit('gateway');calls.hit('command');return FakeResponse(200,{'id':'synthetic-chat'})
    monkeypatch.setattr(gateway.requests,'post',post)
    def model_post(req):calls.hit('provider');return httpx.Response(200,json=payload())
    sessions=ApiSessionService();sid=await sessions.create_session()
    async with fake_model(model_post) as (base,_):
        model=instrument_model(base,'team_manager','orchestration')
        async def execute(service,session,text):
            calls.hit('agent')
            _=[v async for v in model.generate_content_async(request())];inject('provider')
            assert _authorized('st ru')==['st ru'];inject('governance')
            state=dict(session.state);context=SimpleNamespace(state=state);inject('tool_selection')
            proposal=create_action_proposal('teams.sendMessage',{'chatId':'synthetic-chat','message':'synthetic approved notice'},state)
            assert approve_proposal(proposal.proposal_id,state).success;inject('approval')
            turn=current_turn();command='st ru'
            authorization=CommandAuthorization(command,normalize_command(command),turn.run_id,None,'KID-CMD:v1:sec-0000',None)
            result=enforce_command_egress('Run `st ru`.',authorizations=[authorization],producer='technical_authority_engineer',run_id=turn.run_id)
            assert command in result.text;inject('command_preparation')
            with tool_scope('teams_send_message'):
                calls.hit('tool')
                assert teams_send_message('synthetic-chat','synthetic approved notice',context)['status']=='executed'
            inject('egress')
            assert not authorize_write('teams.sendMessage',{'chatId':'synthetic-chat','message':'synthetic approved notice'},state).authorized
            await service.persist_state_delta(session,state)
        chat=ChatService(sessions,runner=FakeRunner(sessions,side_effect=execute,respond=lambda _: 'Approved action completed.'),observability_runtime=runtime)
        run=chat._run_turn_events
        async def counted_turn(*args,**kwargs):
            calls.hit('turn')
            async for item in run(*args,**kwargs):yield item
        monkeypatch.setattr(chat,'_run_turn_events',counted_turn)
        try:
            _=[event async for event in chat.execute_turn_events(sid,'synthetic approved workflow')]
            await asyncio.gather(*tuple(chat._background_turns),return_exceptions=True)
            await asyncio.gather(*auxiliary)
            assert fired
            if fault in ('retention','diagnostics'):
                assert fault_observed==[fault]
                if retention_listener is not None:
                    event.remove(db.engine.sync_engine,'after_cursor_execute',retention_listener);retention_listener=None
                monkeypatch.setattr(ops,'get',get)
                assert await diagnostics.query('get',observed.run_id,('development',)) is not None
                if fault=='retention':assert (await retention.run('exceptional_summary',utcnow(),dry_run=False)).deleted==1
            runtime.flush(2)
            exported=runtime.exporters['trace'].snapshot()
            try:archive.submit(encode_spans(exported).SerializeToString())
            except (OSError,ArchiveError):assert fault in ('archive','telemetry')
            monkeypatch.setattr(repo,'materialize',materialize)
            await ledger.recover();assert await ledger.recover()==0
            assert (await repo.health('development'))['debt']==0
            calls.expect(provider=1,agent=1,tool=1,gateway=1,command=1,turn=1)
            async with asyncio.timeout(1),sessions.lock_for(sid,'default'):pass
            assert not chat._background_turns and not chat._run_tasks
            print('M16_GOVERNED boundary='+boundary+' fault='+fault+' physical_command=1 physical_provider=1 physical_turn=1 recovery_business_dispatches=0')
        finally:
            if retention_listener is not None:event.remove(db.engine.sync_engine,'after_cursor_execute',retention_listener)
            await close_chat_storage(chat);runtime.close()
