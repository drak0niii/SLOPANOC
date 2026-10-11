"""Actual SQL/worker/read-plane proof; every database is disposable local SQLite."""
import asyncio
import importlib.util
import json
from datetime import timedelta
from pathlib import Path
from uuid import UUID, uuid4
import httpx
import pytest
import pytest_asyncio
from sqlalchemy import select, func, event as sql_event
from sqlalchemy.ext.asyncio import create_async_engine
from alembic.migration import MigrationContext
from alembic.operations import Operations
from backend.observability.database import Database
from backend.observability.models import Base, RunRow
from backend.observability.finops.models import FinOpsBase, AttemptRow, LedgerRow
from backend.observability.hybrid.models import (HybridBase, AuditRow, OutboxRow, ReceiptRow,
    FailureRow, RunProjectionRow, AgentProjectionRow)
from backend.observability.hybrid.capture import Capture, make_event
from backend.observability.hybrid.config import HybridConfig
from backend.observability.hybrid.local import compose
from backend.observability.hybrid.read import ReadRepository, ActivityView
from backend.observability.hybrid.store import OutboxStore, ProjectorStore
from backend.observability.hybrid.workers import Publisher, Projector
from backend.observability.hybrid.transport import FakeEventTransport
from backend.observability.projection import utcnow
from backend.observability.stages import RunStatus, Stage
from backend.tests._m7_storage import state, change, config, close_chat_storage
from backend.tests.test_finops_runtime_ledger import identity, payload
from backend.tests.test_observability_read_plane import authorize

@pytest_asyncio.fixture
async def pipeline(tmp_path):
    db=Database('sqlite+aiosqlite:///'+str(tmp_path/'hybrid.sqlite'),isolated_test=True)
    async with db.engine.begin() as c:
        for base in (Base,FinOpsBase,HybridBase):await c.run_sync(base.metadata.create_all)
    cfg=HybridConfig(environment='development',capture_enabled=True)
    p=compose(db,config(),cfg,isolated_local=True)
    yield db,p,cfg
    await p.close();await db.close()

async def count(db,model):
    async with db.sessions() as s:return (await s.execute(select(func.count()).select_from(model))).scalar_one()

async def rows(db,model):
    async with db.sessions() as s:return (await s.execute(select(model))).scalars().all()

def terminal(run):
    now=utcnow()
    return change(run,state_version=run.state_version+1,status=RunStatus.COMPLETED,
        current_stage=Stage.TURN_COMPLETED,terminal_at=now,observed_at=now,
        heartbeat_at=now,last_progress_at=now,stage_started_at=now)

@pytest.mark.asyncio
async def test_owner_transaction_rollbacks_run_and_accounting(pipeline):
    db,p,cfg=pipeline
    class FailAfterAppend(Capture):
        async def append(self,s,e):
            await super().append(s,e)
            raise RuntimeError('injected after outbox insert')
    p.run_repository.audit_capture=FailAfterAppend()
    with pytest.raises(RuntimeError):await p.run_repository.persist(state())
    assert await count(db,RunRow)==await count(db,AuditRow)==await count(db,OutboxRow)==0
    i=identity();await p.accounting_repository.admit(i)
    p.accounting_repository.audit_capture=FailAfterAppend()
    with pytest.raises(RuntimeError):await p.accounting_repository.start(i)
    assert (await rows(db,AttemptRow))[0].state=='ADMITTED'
    assert await count(db,AuditRow)==await count(db,OutboxRow)==0

@pytest.mark.asyncio
async def test_capture_off_and_explicit_composition(pipeline):
    db,p,cfg=pipeline
    from backend.observability.repository import Repository
    await Repository(db.sessions,config()).persist(state())
    assert await count(db,OutboxRow)==0
    for kwargs in ({},{'isolated_local':True}):
        with pytest.raises(ValueError):compose(db,config(),HybridConfig(),**kwargs)
    assert p.publisher.task is None

@pytest.mark.asyncio
async def test_commit_restart_before_publish_no_business_replay(pipeline):
    db,p,cfg=pipeline;run=state()
    await p.run_repository.persist(run)
    assert await count(db,OutboxRow)==1
    # New pool + worker instances, same file; no business function is called again.
    replacement=Database(str(db.engine.url),isolated_test=True)
    try:
        q=compose(replacement,config(),cfg,isolated_local=True)
        assert await q.tick()==(1,1)
        assert await count(db,RunRow)==1
        assert (await rows(db,OutboxRow))[0].publication_state=='PUBLISHED'
        assert (await ReadRepository(replacement.sessions).activity('development')).runs[0].run_id==UUID(run.run_id)
        await q.close()
    finally:await replacement.close()

@pytest.mark.asyncio
async def test_publish_crash_before_ack_expired_lease_duplicate(pipeline):
    db,p,cfg=pipeline;await p.run_repository.persist(state())
    now=utcnow()
    def crash():raise RuntimeError('process crash seam')
    with pytest.raises(RuntimeError):await p.publisher.tick(now,after_publish=crash)
    assert (await rows(db,OutboxRow))[0].publication_state=='LEASED'
    await p.projector.tick(p.transport,now)
    assert await p.publisher.tick(now+timedelta(seconds=cfg.publisher_lease_seconds+1))==1
    await p.projector.tick(p.transport,now+timedelta(seconds=31))
    assert await count(db,ReceiptRow)==await count(db,RunProjectionRow)==1
    assert (await rows(db,ReceiptRow))[0].duplicate_count==1
    assert (await rows(db,OutboxRow))[0].attempt_count==2

@pytest.mark.asyncio
async def test_lease_tokens_backoff_dead_and_bounded_batch(pipeline):
    db,p,cfg=pipeline
    await p.run_repository.persist(state())
    cfg=cfg.model_copy(update={'publisher_batch_size':1,'publisher_max_attempts':2})
    store=OutboxStore(db.sessions,cfg);now=utcnow();claims=await store.claim(now)
    assert len(claims)==1
    e,token,_=claims[0]
    assert not await store.settle(e.event_id,'stale',success=True,now=now)
    assert await store.settle(e.event_id,token,success=False,now=now)
    row=[r for r in await rows(db,OutboxRow) if r.event_id==str(e.event_id)][0]
    assert row.publication_state=='RETRYING' and row.available_at==now+timedelta(seconds=1)
    assert all(c[0].event_id!=e.event_id for c in await store.claim(now))
    later=now+timedelta(seconds=2)
    claims=await store.claim(later)
    assert claims[0][0].event_id==e.event_id
    assert await store.settle(e.event_id,claims[0][1],success=False,now=later)
    assert [r for r in await rows(db,OutboxRow) if r.event_id==str(e.event_id)][0].publication_state=='DEAD'
    assert not await store.settle(e.event_id,token,success=True,now=later)
    for _ in range(2):await p.run_repository.persist(state())
    assert len(await store.claim(later))==1
    view=await ReadRepository(db.sessions).activity('development')
    assert view.debt.dead==1 and view.debt.leased==1 and view.debt.pending==1

@pytest.mark.asyncio
async def test_transport_pressure_retry_backoff_and_dead_bound(pipeline):
    db,p,cfg=pipeline;clock=[0.0]
    transport=FakeEventTransport(1,2,clock=lambda:clock[0],environment='development')
    wire=make_event('model.attempt.started','development','provider_attempt',str(uuid4()),1,utcnow(),agent='team_manager').model_dump_json().encode()
    await transport.publish(wire)
    with pytest.raises(asyncio.QueueFull):await transport.publish(wire)
    item=await transport.receive();await transport.nack(item)
    assert await transport.receive() is None
    clock[0]=1
    item=await transport.receive();await transport.nack(item)
    assert transport.dead==1 and transport.queue.empty()
    await p.run_repository.persist(state());await transport.publish(wire)
    assert await Publisher(OutboxStore(db.sessions,cfg),transport).tick()==0
    assert (await rows(db,OutboxRow))[0].publication_state=='RETRYING'

@pytest.mark.asyncio
@pytest.mark.parametrize('deliveries',[1,2,100])
async def test_actual_projector_duplicates_have_identical_business_projection(pipeline,deliveries):
    db,p,cfg=pipeline;run=state();await p.run_repository.persist(run)
    event,_,_= (await p.publisher.store.claim())[0]
    wire=event.model_dump_json().encode()
    assert await p.projector.consume(wire)=='PROCESSED'
    for _ in range(deliveries-1):assert await p.projector.consume(wire)=='DUPLICATE'
    projection=(await rows(db,RunProjectionRow))[0]
    assert projection.run_id==run.run_id and projection.aggregate_version==1 and projection.status=='RUNNING'
    assert await count(db,ReceiptRow)==1 and (await rows(db,ReceiptRow))[0].duplicate_count==deliveries-1
    assert projection.latest_event_occurred_at==event.timestamp

@pytest.mark.asyncio
async def test_conflict_out_of_order_and_terminal_never_regresses(pipeline):
    db,p,cfg=pipeline;run=state(trace_id='a'*32,turn_id=str(uuid4()),turn_id_origin='execution')
    await p.run_repository.persist(run);await p.run_repository.persist(terminal(run))
    claims=await p.publisher.store.claim();events={e.event_type:e for e,_,_ in claims}
    for kind in ('run.terminal','run.accepted'):
        assert await p.projector.consume(events[kind].model_dump_json().encode())=='PROCESSED'
    changed=events['run.terminal'].model_copy(update={'timestamp':utcnow()})
    assert await p.projector.consume(changed.model_dump_json().encode())=='CONFLICT'
    row=(await rows(db,RunProjectionRow))[0]
    assert row.status=='COMPLETED' and row.aggregate_version==2 and row.turn_id==run.turn_id and row.trace_id=='a'*32
    assert (await rows(db,FailureRow))[0].failure_code=='CONFLICT'

@pytest.mark.asyncio
async def test_projector_receipt_rolls_back_and_retry_resolves(pipeline):
    db,p,cfg=pipeline;await p.run_repository.persist(state());e,_,_=(await p.publisher.store.claim())[0]
    def fail(conn,cursor,statement,parameters,context,executemany):
        if statement.startswith('INSERT INTO observability_run_projection'):raise RuntimeError('FORBIDDEN_SENTINEL')
    sql_event.listen(db.engine.sync_engine,'before_cursor_execute',fail)
    try:assert await p.projector.consume(e.model_dump_json().encode())=='RETRY'
    finally:sql_event.remove(db.engine.sync_engine,'before_cursor_execute',fail)
    assert await count(db,ReceiptRow)==await count(db,RunProjectionRow)==0
    assert (await rows(db,FailureRow))[0].processing_state=='RETRY'
    assert await p.projector.consume(e.model_dump_json().encode())=='PROCESSED'
    assert (await rows(db,FailureRow))[0].processing_state=='RESOLVED'
    assert 'FORBIDDEN_SENTINEL' not in json.dumps([r.__dict__ for r in await rows(db,FailureRow)],default=str)

@pytest.mark.asyncio
async def test_accounting_catalogue_refs_and_no_financial_projection(pipeline):
    db,p,cfg=pipeline;repo=p.accounting_repository;i=identity()
    await repo.admit(i);await repo.start(i);await repo.finished(i,utcnow());await repo.failed_capture(i)
    usage=payload(i).model_copy(update={'run_id':str(uuid4()),'turn_id':'turn-1','trace_id':'b'*32})
    await repo.capture(usage);assert await repo.materialize(i.event_id)=='PERSISTED'
    assert await repo.materialize(i.event_id)=='DUPLICATE'
    assert {r.event_type for r in await rows(db,AuditRow)}=={'model.attempt.started','model.attempt.completed',
        'model.usage.captured','accounting.debt.created','accounting.debt.resolved'}
    await p.tick();agent=(await rows(db,AgentProjectionRow))[0]
    assert (agent.model_attempts_started,agent.model_attempts_completed,agent.usage_records_captured)==(1,1,1)
    assert await count(db,LedgerRow)==1
    assert not any('token' in c.name or 'cost' in c.name for c in AgentProjectionRow.__table__.columns)

@pytest.mark.asyncio
@pytest.mark.parametrize('field',['prompt','response','teams_body','authorization','sql','chain_of_thought','command','attachment'])
async def test_privacy_every_actual_sink(pipeline,field):
    db,p,cfg=pipeline;await p.run_repository.persist(state());e,_,_=(await p.publisher.store.claim())[0]
    body=e.model_dump(mode='json');body['payload_safe'][field]='FORBIDDEN_SENTINEL'
    assert await p.projector.consume(json.dumps(body).encode())=='INVALID'
    async with db.sessions() as s,s.begin():
        from backend.observability.hybrid.contracts import AuditEvent
        import warnings
        with warnings.catch_warnings(record=True) as captured,pytest.raises(ValueError):
            await Capture().append(s,AuditEvent.model_construct(**body))
        assert not captured
    assert await count(db,RunProjectionRow)==await count(db,ReceiptRow)==0
    for model in (AuditRow,OutboxRow,FailureRow,RunProjectionRow,AgentProjectionRow):
        serial=[{c.name:getattr(r,c.name) for c in model.__table__.columns} for r in await rows(db,model)]
        assert 'FORBIDDEN_SENTINEL' not in json.dumps(serial,default=str)
    with pytest.raises(ValueError):await p.transport.publish(json.dumps(body).encode())
    assert p.transport.queue.empty()

@pytest.mark.asyncio
async def test_wrong_environment_unknown_type_and_safe_projector_ingress(pipeline):
    db,p,cfg=pipeline
    from backend.observability.hybrid.projector_app import create_app
    app=create_app(projector=p.projector,isolated_local=True)
    e=make_event('model.attempt.started','test','provider_attempt',str(uuid4()),1,utcnow(),agent='team_manager')
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://local') as client:
        assert (await client.post('/events',content=e.model_dump_json())).status_code==400
        assert (await client.post('/events',content=b'x'*16385)).status_code==413
        assert 'x'*20 not in json.dumps([r.__dict__ for r in await rows(db,FailureRow)],default=str)
    with pytest.raises(ValueError):create_app(projector=p.projector)

async def reader_app(tmp_path,db):
    from backend.api.observability_app import create_app
    from backend.observability.read_config import ReadConfiguration
    cfg=config();policy=tmp_path/'reader-policy.json';policy.write_text(cfg.model_dump_json())
    app=create_app(config=cfg,settings=ReadConfiguration(database_url=str(db.engine.url),
        producer_policy_file=policy),isolated_test=True)
    authorize(app)
    return app

@pytest.mark.asyncio
async def test_local_end_to_end_correlations_get_only_rbac_and_freshness(pipeline,tmp_path,monkeypatch):
    db,p,cfg=pipeline;run=state(trace_id='c'*32,turn_id=str(uuid4()),turn_id_origin='execution')
    await p.run_repository.persist(run);await p.run_repository.persist(terminal(run));await p.tick()
    app=await reader_app(tmp_path,db)
    async def forbidden(*args,**kwargs):raise AssertionError('Reader started work')
    monkeypatch.setattr(Publisher,'tick',forbidden);monkeypatch.setattr(Projector,'consume',forbidden)
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://local') as client:
            response=await client.get('/api/observability/hybrid/activity')
            assert response.status_code==200,response.text
            dto=ActivityView.model_validate(response.json());row=dto.runs[0]
            assert str(row.run_id)==run.run_id and row.turn_id==run.turn_id and row.trace_id=='c'*32
            assert row.status==RunStatus.COMPLETED and row.projection_lag>=0
            assert row.latest_event_processed_at>=row.latest_event_occurred_at
            assert dto.completed_in_page==1 and dto.processing.processed==3
            assert (await client.post('/api/observability/hybrid/activity')).status_code==405
            assert (await client.get('/api/observability/hybrid/activity?environment=production')).status_code==403
            from backend.observability.schemas import Role
            authorize(app,Role.USER)
            assert (await client.get('/api/observability/hybrid/activity')).status_code==403

@pytest.mark.asyncio
async def test_actual_harmless_chatservice_turn_full_local_pipeline(pipeline,tmp_path):
    from backend.api.chat_service import ChatService
    from backend.api.session_service import ApiSessionService
    from backend.tests._api_fakes import FakeRunner
    from backend.observability.runtime import Runtime
    from backend.observability.persistence import Coordinator
    db,p,cfg=pipeline;c=Coordinator(p.run_repository,config());c.start()
    runtime=Runtime(config());runtime.projection=c
    sessions=ApiSessionService();sid=await sessions.create_session()
    service=ChatService(sessions,runner=FakeRunner(sessions),observability_runtime=runtime)
    try:
        response=await service.run_turn(sid,'FORBIDDEN_SENTINEL harmless echo')
        assert response.message.content=='echo: FORBIDDEN_SENTINEL harmless echo'
        await asyncio.gather(*tuple(service._background_turns),return_exceptions=True)
        assert await c.flush(1)
        assert {r.event_type for r in await rows(db,AuditRow)}=={'run.accepted','run.terminal','turn.bound'}
        await p.tick()
        app=await reader_app(tmp_path,db)
        async with app.router.lifespan_context(app):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://local') as client:
                dto=ActivityView.model_validate((await client.get('/api/observability/hybrid/activity')).json())
                assert len(dto.runs)==1 and dto.runs[0].status==RunStatus.COMPLETED
                assert str(dto.runs[0].run_id)==(await rows(db,RunRow))[0].run_id
        for model in (AuditRow,OutboxRow,ReceiptRow,FailureRow,RunProjectionRow):
            assert 'FORBIDDEN_SENTINEL' not in json.dumps([{c.name:getattr(r,c.name) for c in model.__table__.columns}
                for r in await rows(db,model)],default=str)
    finally:
        await c.close();runtime.close();await close_chat_storage(service)

@pytest.mark.asyncio
async def test_additive_migration_only_in_disposable_sqlite(tmp_path):
    path=Path(__file__).parents[2]/'alembic/versions/d26a1b7c9e40_hybrid_audit_outbox_projections.py'
    spec=importlib.util.spec_from_file_location('local_hybrid_migration',path);module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    assert module.down_revision=='c16f4a8b2d90'
    engine=create_async_engine('sqlite+aiosqlite:///'+str(tmp_path/'migration.sqlite'))
    def run(conn,fn):
        with Operations.context(MigrationContext.configure(conn)):fn()
    try:
        async with engine.begin() as conn:
            from sqlalchemy import text
            await conn.execute(text('CREATE TABLE unrelated (id INTEGER PRIMARY KEY)'))
            await conn.execute(text('INSERT INTO unrelated VALUES (17)'))
            await conn.run_sync(run,module.upgrade)
            from sqlalchemy import inspect
            names=await conn.run_sync(lambda c:inspect(c).get_table_names())
            legacy={name for name in HybridBase.metadata.tables if not name.startswith('execution_')}
            assert set(names)==legacy|{'unrelated'}
            def parity(connection):
                inspector=inspect(connection)
                for table in HybridBase.metadata.sorted_tables:
                    if table.name.startswith("execution_"):continue
                    columns=inspector.get_columns(table.name)
                    assert set(table.columns.keys())=={c['name'] for c in columns}
                    assert {c.name:c.nullable for c in table.columns}=={c['name']:c['nullable'] for c in columns}
                    assert {c.name for c in table.primary_key}==set(inspector.get_pk_constraint(table.name)['constrained_columns'])
                    assert {i.name for i in table.indexes}=={i['name'] for i in inspector.get_indexes(table.name)}
                    from sqlalchemy import CheckConstraint
                    assert len([c for c in table.constraints if isinstance(c,CheckConstraint)])==len(inspector.get_check_constraints(table.name))
                    assert len(table.foreign_key_constraints)==len(inspector.get_foreign_keys(table.name))
            await conn.run_sync(parity)
            await conn.run_sync(run,module.downgrade)
            assert await conn.run_sync(lambda c:inspect(c).get_table_names())==['unrelated']
            assert (await conn.execute(text('SELECT id FROM unrelated'))).scalar_one()==17
    finally:await engine.dispose()

def test_shared_frontend_fixture_is_exact_python_dto():
    data=json.loads((Path(__file__).parent/'fixtures/hybrid_activity.json').read_text())
    assert ActivityView.model_validate(data).model_dump(mode='json')['runs'][0]['projection_lag']==1

@pytest.mark.asyncio
@pytest.mark.parametrize('window',['commit','publish'])
async def test_actual_abrupt_child_process_crash_recovery(pipeline,tmp_path,window):
    import subprocess
    import sys
    db,p,cfg=pipeline;spool=tmp_path/'test-safe-event.json'
    result=await asyncio.to_thread(subprocess.run,[sys.executable,'-m','backend.tests._hybrid_phase1_child',
        db.engine.url.database,window,str(spool)],capture_output=True,text=True,timeout=15)
    assert result.returncode==23,result.stderr
    assert await count(db,RunRow)==await count(db,OutboxRow)==1
    now=utcnow()+timedelta(seconds=31)
    if window=='publish':assert await p.projector.consume(spool.read_bytes())=='PROCESSED'
    assert await p.tick(now)==(1,1)
    assert await count(db,ReceiptRow)==await count(db,RunProjectionRow)==1
    assert (await rows(db,ReceiptRow))[0].duplicate_count==(1 if window=='publish' else 0)
    assert await count(db,RunRow)==1

@pytest.mark.asyncio
async def test_first_snapshot_terminal_does_not_invent_accepted_state(pipeline):
    db,p,cfg=pipeline
    await p.run_repository.persist(state(status=RunStatus.COMPLETED))
    assert [r.event_type for r in await rows(db,AuditRow)]==['run.terminal']
    await p.tick()
    assert (await rows(db,RunProjectionRow))[0].status=='COMPLETED'

@pytest.mark.asyncio
async def test_projector_capacity_shutdown_failure_store_outage_and_duplicate_resolution(pipeline,monkeypatch):
    db,p,cfg=pipeline;await p.run_repository.persist(state());e,_,_=(await p.publisher.store.claim())[0]
    wire=e.model_dump_json().encode()
    assert await p.projector.consume(wire)=='PROCESSED'
    await p.projector.store.failure('PROCESSING_FAILED',str(e.event_id))
    assert await p.projector.consume(wire)=='DUPLICATE'
    assert (await rows(db,FailureRow))[0].processing_state=='RESOLVED'
    blocker=asyncio.Event()
    async def blocked(*args):await blocker.wait()
    q=Projector(ProjectorStore(db.sessions,cfg.model_copy(update={'projector_max_inflight':1,'shutdown_seconds':1})))
    monkeypatch.setattr(q.store,'apply',blocked)
    task=asyncio.create_task(q.consume(wire));await asyncio.sleep(.01)
    assert await q.consume(wire)=='RETRY' and len(q.active)==1
    await q.close();assert task.cancelled() and not q.active
    assert await q.consume(wire)=='RETRY'
    async def outage(*args,**kwargs):raise RuntimeError('FORBIDDEN_DB_CREDENTIAL')
    monkeypatch.setattr(p.projector.store,'failure',outage)
    assert await p.projector.consume(b'malformed')=='RETRY'

@pytest.mark.asyncio
async def test_debt_missing_observation_and_exhausted_inbox_are_canonical(pipeline):
    db,p,cfg=pipeline;repo=p.accounting_repository;now=utcnow()
    i=identity(deadline_at=now-timedelta(seconds=1))
    await repo.admit(i);await repo.start(i);await repo.checkpoint('development',now)
    assert (await rows(db,AttemptRow))[0].state=='OUTCOME_UNKNOWN'
    await repo.capture(payload(i));await repo.retry_failed(i.event_id,1)
    await repo.materialize(i.event_id)
    events=await rows(db,AuditRow)
    assert len([r for r in events if r.event_type=='accounting.debt.created'])==2
    assert len([r for r in events if r.event_type=='accounting.debt.resolved'])==1
    assert (await rows(db,AttemptRow))[0].state=='USAGE_CAPTURED'

@pytest.mark.asyncio
async def test_read_snapshots_are_bounded_not_historical_totals(pipeline):
    db,p,cfg=pipeline
    for _ in range(2):await p.run_repository.persist(state())
    await p.tick()
    dto=await ReadRepository(db.sessions).activity('development',1)
    assert dto.runs_truncated and len(dto.runs)==1
    assert dto.counts_scope=='returned_page_and_bounded_processing_snapshot'
    with pytest.raises(ValueError):await ReadRepository(db.sessions).activity('development',101)

@pytest.mark.asyncio
async def test_ledger_materialization_outbox_and_business_state_rollback_together(pipeline):
    db,p,cfg=pipeline;repo=p.accounting_repository;i=identity()
    await repo.admit(i);await repo.start(i);await repo.capture(payload(i))
    class FailUsage(Capture):
        async def append(self,s,e):
            await super().append(s,e)
            if e.event_type=='model.usage.captured':raise RuntimeError('injected usage transaction failure')
    repo.audit_capture=FailUsage()
    with pytest.raises(RuntimeError):await repo.materialize(i.event_id)
    assert await count(db,LedgerRow)==0
    assert await count(db,OutboxRow)==await count(db,AuditRow)==1
    assert (await rows(db,AttemptRow))[0].state=='FINAL_CAPTURE_PENDING'
    repo.audit_capture=Capture();assert await repo.materialize(i.event_id)=='PERSISTED'
    assert await count(db,LedgerRow)==1 and await count(db,OutboxRow)==2

@pytest.mark.asyncio
async def test_publisher_rejects_integrity_error_without_transport_admission(pipeline):
    from sqlalchemy import update
    db,p,cfg=pipeline;await p.run_repository.persist(state())
    async with db.sessions() as s,s.begin():
        await s.execute(update(OutboxRow).values(payload_hash='0'*64))
    assert await p.publisher.tick()==0 and p.transport.queue.empty()
    row=(await rows(db,OutboxRow))[0]
    assert row.publication_state=='DEAD' and row.last_failure_code=='INVALID'
