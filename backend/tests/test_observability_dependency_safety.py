import pytest
from types import SimpleNamespace
from backend.observability import dependency_instrumentation as di, dependency_metrics as dm
from backend.observability.dependency_storage import storage_call, secret_read
from backend.tests._m5_dependencies import environment, capture

@pytest.mark.parametrize('failure',['start','begin','update','finish','metric','sanitize','snapshot','log'])
@pytest.mark.parametrize('operation',['upload','delete','secret','http','db','knowledge'])
def test_failure_injection_never_replays_business_calls(failure,operation,monkeypatch):
    calls=[]
    def broken(*args,**kwargs):raise RuntimeError('M5_PRIVATE_TELEMETRY')
    def business(*args,**kwargs):calls.append(1);return b'private'
    with environment() as (r,t):
        if failure=='start':monkeypatch.setattr(r.tracer,'start_span',broken)
        elif failure in {'begin','update','finish'}:monkeypatch.setattr(di.Dependency,failure,broken)
        elif failure=='metric':monkeypatch.setattr(dm,'record',broken)
        elif failure=='sanitize':monkeypatch.setattr('backend.observability.redaction.safe_span_attributes',broken)
        elif failure=='snapshot':monkeypatch.setattr(t,'add_dependency',broken)
        elif failure=='log':monkeypatch.setattr('backend.observability.logging.emit',broken)
        if operation in {'upload','delete'}:result=storage_call('chat_attachments',operation,business,byte_count=0)
        elif operation=='secret':result=secret_read(business)
        else:
            with di.dependency_scope('case_db' if operation=='db' else 'knowledge' if operation=='knowledge' else 'power_automate_gateway',
                    'SELECT' if operation=='db' else 'dense' if operation=='knowledge' else 'teams.sendMessage',
                    'db.client' if operation=='db' else 'knowledge.retrieval' if operation=='knowledge' else 'http.client'):
                result=business()
        assert calls==[1] and result==b'private'
        assert t.snapshot().dependencies==() and di.active_dependency(t) is None

@pytest.mark.parametrize('failure',['start','sanitize','metric','finish','logging'])
def test_original_business_exception_identity_preserved(failure,monkeypatch):
    exc=ValueError('M5_PRIVATE_BUSINESS');calls=[]
    def business():calls.append(1);raise exc
    def broken(*args,**kwargs):raise RuntimeError('M5_PRIVATE_TELEMETRY')
    with environment() as (r,t):
        if failure=='start':monkeypatch.setattr(r.tracer,'start_span',broken)
        elif failure=='sanitize':monkeypatch.setattr('backend.observability.redaction.safe_span_attributes',broken)
        elif failure=='metric':monkeypatch.setattr(dm,'record',broken)
        elif failure=='logging':monkeypatch.setattr('backend.observability.logging.emit_dependency_failure',broken)
        else:monkeypatch.setattr(di.Dependency,'finish',broken)
        with pytest.raises(ValueError) as caught:storage_call('chat_attachments','delete',business)
        assert caught.value is exc and calls==[1]
        assert 'M5_PRIVATE' not in capture(r)

@pytest.mark.asyncio
@pytest.mark.parametrize('failure',['start','sanitize','metric','snapshot','finish'])
@pytest.mark.parametrize('operation',['gateway','upload','delete','secret','query','commit','knowledge'])
async def test_actual_adapters_never_replay_side_effects(failure,operation,monkeypatch):
    from sqlalchemy import select,event
    from sqlalchemy.ext.asyncio import create_async_engine,async_sessionmaker
    from backend.observability.dependency_database import pool_kwargs,observe_engine,observe_factory
    from backend.gateway.power_automate_client import PowerAutomateClient
    from backend.config.settings import Settings,_cached_secret_value
    from backend.tests.test_observability_dependency_storage import Blob,storage
    from backend.knowledge.retrieval.service import KnowledgeRetrievalService
    from backend.observability.knowledge_instrumentation import knowledge_stage
    from backend.tests.test_observability_dependency_knowledge import Repo,query
    engine=None
    with environment() as (r,t):
        def broken(*args,**kwargs):raise ValueError('M5_PRIVATE_FAILURE')
        if failure=='start':monkeypatch.setattr(r.tracer,'start_span',broken)
        elif failure=='sanitize':monkeypatch.setattr('backend.observability.redaction.safe_span_attributes',broken)
        elif failure=='metric':monkeypatch.setattr(dm,'record',broken)
        elif failure=='snapshot':monkeypatch.setattr(t,'add_dependency',broken)
        elif failure=='finish':monkeypatch.setattr(di.Dependency,'finish',broken)
        if operation=='gateway':
            calls=[]
            def post(*args,**kwargs):calls.append(1);return SimpleNamespace(status_code=200,json=lambda:{},headers={})
            monkeypatch.setattr('backend.gateway.power_automate_client.requests.post',post)
            PowerAutomateClient(Settings(env={'SLOPANOC_POWER_AUTOMATE_GATEWAY_URL':'https://fake.invalid'})).send_message('private','private')
            assert calls==[1]
        elif operation in {'upload','delete'}:
            blob=Blob();obj=storage('chat',blob)
            if operation=='upload':obj.put_bytes('private',b'private','image/png')
            else:obj.delete('private')
            assert blob.calls==[operation]
        elif operation=='secret':
            calls=[]
            def read(**kwargs):calls.append(1);return SimpleNamespace(payload=SimpleNamespace(data=b'private'))
            monkeypatch.setattr('google.cloud.secretmanager.SecretManagerServiceClient',lambda:SimpleNamespace(access_secret_version=read))
            _cached_secret_value.cache_clear()
            try:assert _cached_secret_value('private')=='private' and calls==[1]
            finally:_cached_secret_value.cache_clear()
        elif operation in {'query','commit'}:
            url='sqlite+aiosqlite:///:memory:'
            engine=create_async_engine(url,**pool_kwargs(url,{},'case_db'));observe_engine(engine,'case_db')
            factory=async_sessionmaker(engine);observe_factory(factory)
            calls=[];commits=[]
            event.listen(engine.sync_engine,'before_cursor_execute',lambda *args:calls.append(1))
            event.listen(engine.sync_engine,'commit',lambda *args:commits.append(1))
            try:
                async with factory() as session:
                    await session.execute(select(1))
                    if operation=='commit':await session.commit()
                assert calls==[1] and len(commits)==(1 if operation=='commit' else 0)
            finally:await engine.dispose()
        else:
            repo=Repo();result=await KnowledgeRetrievalService(repo,observer=knowledge_stage).retrieve(query())
            assert repo.calls==1 and len(result.items)==1
        assert t.snapshot().dependencies==()
