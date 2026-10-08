import asyncio
import pytest
from sqlalchemy import text, select, event
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
from sqlalchemy.exc import TimeoutError as PoolTimeout, DBAPIError
from backend.observability.dependency_database import pool_kwargs, observe_engine, observe_factory
from backend.tests._m5_dependencies import environment, spans, capture, points

SECRET='M5_DB_SENTINEL'

@pytest.mark.asyncio
@pytest.mark.parametrize('owner',['session_db','case_db','attachment_db','knowledge_db'])
async def test_engine_exact_query_counts_statement_privacy_transaction(owner):
    engine=create_async_engine('sqlite+aiosqlite:///:memory:', **pool_kwargs('sqlite+aiosqlite:///:memory:',{},owner))
    observe_engine(engine,owner);observe_engine(engine,owner)
    factory=async_sessionmaker(engine,expire_on_commit=False);observe_factory(factory)
    with environment() as (r,t):
        actual=[]
        event.listen(engine.sync_engine,'before_cursor_execute',lambda *args:actual.append(1))
        async with factory() as session:
            await session.execute(text("select 'M5_DB_SENTINEL' where :private=:private"),{'private':SECRET})
            await session.execute(select(1))
            await session.commit()
            await session.execute(select(2));await session.rollback()
        assert len(spans(r,'db.client'))==len(actual)==3
        assert all(s.attributes['slopanoc.dependency']==owner for s in spans(r,'db.client'))
        outcomes=[s.attributes.get('slopanoc.transaction_outcome') for s in spans(r,'db.transaction')]
        assert outcomes==['commit','rollback']
        assert SECRET not in capture(r)
        assert t.snapshot().dependencies==()
    await engine.dispose()

@pytest.mark.asyncio
async def test_real_pool_saturation_wait_exhaustion_and_supported_gauges(tmp_path):
    url='sqlite+aiosqlite:///'+str(tmp_path/'isolated.db')
    engine=create_async_engine(url, **pool_kwargs(url,{'pool_size':1,'max_overflow':0,'pool_timeout':.03},'case_db'))
    observe_engine(engine,'case_db')
    with environment() as (r,t):
        initial_checked_out=points(r,"slopanoc.db.pool.checked_out","case_db")[-1].value
        first=await engine.connect()
        began=asyncio.Event()
        async def second():
            began.set()
            async with engine.connect():pass
        task=asyncio.create_task(second());await began.wait();await asyncio.sleep(.005)
        assert t.snapshot().current_dependency=='case_db'
        with pytest.raises(PoolTimeout):await task
        assert t.snapshot().dependencies==()
        acquired=spans(r,'db.connection')
        assert len(acquired)==2
        assert acquired[-1].attributes['slopanoc.failure_kind']=='pool_timeout'
        assert acquired[-1].attributes['slopanoc.duration_ms']>=20
        assert points(r,'slopanoc.db.pool.exhaustion')[-1].value==1
        assert points(r,'slopanoc.db.pool.checked_out','case_db')[-1].value==initial_checked_out+1
        assert points(r,'slopanoc.db.pool.size','case_db')[-1].value>=1 and engine.pool.size()==1
        assert points(r,'slopanoc.db.connection_acquire_duration')
        assert not points(r,'slopanoc.db.pool.wait')
        await first.close()
        assert points(r,'slopanoc.db.pool.checked_out','case_db')[-1].value==initial_checked_out
    await engine.dispose()

@pytest.mark.asyncio
async def test_pool_release_concurrent_success(tmp_path):
    url='sqlite+aiosqlite:///'+str(tmp_path/'local.db')
    engine=create_async_engine(url,**pool_kwargs(url,{'pool_size':1,'max_overflow':0},'knowledge_db'));observe_engine(engine,'knowledge_db')
    with environment() as (r,t):
        first=await engine.connect()
        async def query():
            async with engine.connect() as conn:await conn.execute(select(1))
        task=asyncio.create_task(query());await asyncio.sleep(.005)
        assert t.snapshot().dependencies
        await first.close();await task
        assert t.snapshot().dependencies==()
        assert len(spans(r,'db.client'))==1 and len(spans(r,'db.connection'))==2
    await engine.dispose()

@pytest.mark.asyncio
async def test_query_failure_connection_failure_no_content():
    from sqlalchemy.exc import OperationalError
    engine=create_async_engine('sqlite+aiosqlite:///:memory:',**pool_kwargs('sqlite+aiosqlite:///:memory:',{},'knowledge_db'));observe_engine(engine,'knowledge_db')
    with environment() as (r,t):
        async with engine.connect() as conn:
            with pytest.raises(OperationalError):await conn.execute(text('SELECT * FROM M5_DB_SENTINEL'))
        record=spans(r,'db.client')[0]
        assert record.attributes['slopanoc.error_code']=='DATABASE_QUERY_ERROR'
        assert SECRET not in capture(r) and t.snapshot().dependencies==()
    await engine.dispose()

@pytest.mark.asyncio
async def test_actual_four_owners_and_adk_factory(tmp_path):
    from backend.api.session_service import create_session_service_backend
    from backend.config.settings import Settings
    from backend.cases.db import CaseDatabase
    from backend.attachments.repository import AttachmentRepository
    from backend.knowledge.repository.sqlalchemy import SqlAlchemyKnowledgeRepository
    url='sqlite+aiosqlite:///:memory:'
    adk=create_session_service_backend(Settings(env={'SLOPANOC_DATABASE_URL':url,'SLOPANOC_SESSION_BACKEND':'database'}))
    case=CaseDatabase(url);attachment=AttachmentRepository(url);knowledge=SqlAlchemyKnowledgeRepository(url)
    try:
        with environment() as (r,t):
            await adk.create_session(app_name='m5',user_id='fake',session_id='local')
            await adk.get_session(app_name='m5',user_id='fake',session_id='local')
            for engine in [case._engine,attachment._engine,knowledge._engine]:
                async with engine.connect() as conn:await conn.execute(select(1))
            assert {s.attributes['slopanoc.dependency'] for s in spans(r,'db.client')}=={'session_db','case_db','attachment_db','knowledge_db'}
            assert t.snapshot().dependencies==()
    finally:
        await adk.close();await case.close();await attachment.close();await knowledge.close()

@pytest.mark.asyncio
async def test_database_existing_retry_once_is_not_telemetry_retry(monkeypatch):
    from backend.knowledge.repository.sqlalchemy import SqlAlchemyKnowledgeRepository
    repo=SqlAlchemyKnowledgeRepository('sqlite+aiosqlite:///:memory:');repo._schema_ready=True
    calls=[]
    class Session:
        async def __aenter__(self):return self
        async def __aexit__(self,*args):pass
        async def get(self,*args):
            calls.append(1)
            if len(calls)==1:raise DBAPIError('M5_DB_SENTINEL',{'private':SECRET},Exception(SECRET),False)
            return None
    monkeypatch.setattr(repo,'_session_factory',lambda:Session())
    with environment() as (r,t):
        assert await repo.get('private','v1') is None
        assert calls==[1,1]
        assert points(r,'slopanoc.dependency.retries')[-1].value==1
        assert SECRET not in capture(r)
    await repo.close()

@pytest.mark.asyncio
async def test_pool_recreation_keeps_single_listeners_and_connection_counts():
    url='sqlite+aiosqlite:///:memory:'
    engine=create_async_engine(url,**pool_kwargs(url,{},'attachment_db'));observe_engine(engine,'attachment_db')
    with environment() as (r,t):
        for _ in range(3):
            async with engine.connect() as conn:await conn.execute(select(1))
            assert points(r,'slopanoc.db.pool.connections','attachment_db')[-1].value>=1
            await engine.dispose()
        assert len(spans(r,'db.client'))==3 and len(spans(r,'db.connection'))==3
        assert points(r,'slopanoc.db.pool.connections','attachment_db')[-1].value==0

@pytest.mark.asyncio
async def test_real_commit_failure_is_not_replayed_and_marks_outcome():
    url='sqlite+aiosqlite:///:memory:'
    engine=create_async_engine(url,**pool_kwargs(url,{},'case_db'));observe_engine(engine,'case_db')
    factory=async_sessionmaker(engine);observe_factory(factory)
    calls=[]
    def fail(conn):calls.append(1);raise RuntimeError(SECRET)
    event.listen(engine.sync_engine,'commit',fail)
    try:
        with environment() as (r,t):
            async with factory() as session:
                await session.execute(select(1))
                with pytest.raises(RuntimeError):await session.commit()
            commits=[s for s in spans(r,'db.transaction') if s.attributes['slopanoc.dependency_operation']=='COMMIT']
            assert len(commits)==len(calls)==1
            assert commits[0].attributes['slopanoc.transaction_outcome']=='failure'
            assert commits[0].attributes['slopanoc.error_code']=='DATABASE_PERSISTENCE_ERROR'
            assert SECRET not in capture(r)
    finally:await engine.dispose()

@pytest.mark.asyncio
async def test_cancelled_pool_wait_cleans_live_context(tmp_path):
    url='sqlite+aiosqlite:///'+str(tmp_path/'cancel.db')
    engine=create_async_engine(url,**pool_kwargs(url,{'pool_size':1,'max_overflow':0},'case_db'));observe_engine(engine,'case_db')
    try:
        with environment() as (r,t):
            first=await engine.connect()
            async def acquire():
                async with engine.connect():pass
            task=asyncio.create_task(acquire());await asyncio.sleep(.005)
            assert t.snapshot().dependencies
            task.cancel()
            with pytest.raises(asyncio.CancelledError):await task
            await first.close()
            assert t.snapshot().dependencies==()
            assert spans(r,'db.connection')[-1].attributes['slopanoc.status']=='CANCELLED'
    finally:await engine.dispose()

@pytest.mark.asyncio
async def test_actual_connection_failure_classified_without_dsn():
    calls=[]
    async def creator():calls.append(1);raise OSError('M5_DB_SENTINEL')
    url='sqlite+aiosqlite:///:memory:'
    engine=create_async_engine(url,async_creator=creator,**pool_kwargs(url,{},'case_db'));observe_engine(engine,'case_db')
    try:
        with environment() as (r,t):
            with pytest.raises(OSError):await engine.connect()
            assert calls==[1]
            scopes=spans(r,'db.connection')
            assert len(scopes)==1 and scopes[0].attributes['slopanoc.error_code']=='DATABASE_CONNECTION_ERROR'
            assert SECRET not in capture(r) and not t.snapshot().dependencies
    finally:await engine.dispose()
