import asyncio
from datetime import datetime, timezone
import pytest
import httpx
from backend.knowledge.retrieval.service import KnowledgeRetrievalService
from backend.knowledge.retrieval.contracts import KnowledgeRetrievalQuery
from backend.knowledge.domain.applicability import ApplicabilityContext
from backend.observability.knowledge_instrumentation import knowledge_stage
from backend.observability.tool_instrumentation import tool_scope
from backend.observability.model_context import observation_sink
from backend.tests.knowledge.test_tools_service import _governed_object
from backend.tests.test_observability_model_provider import fake_model
from backend.tests._m5_dependencies import environment, spans, capture, points

SECRET='M5_KNOWLEDGE_SENTINEL'
class Repo:
    def __init__(self,empty=False):self.calls=0;self.empty=empty
    async def list_all(self):self.calls+=1;return [] if self.empty else [_governed_object(content='router '+SECRET)]

def query():
    return KnowledgeRetrievalQuery(query_text='router',as_of=datetime(2026,6,1,tzinfo=timezone.utc),applicability_context=ApplicabilityContext(),limit=5)

@pytest.mark.asyncio
@pytest.mark.parametrize('mode',['lexical','hybrid','failure','timeout','empty'])
async def test_stages_preserve_retrieval_and_fallback(mode):
    repo=Repo(mode=='empty')
    class Dense:
        model_name='fake'
        async def similarities(self,*args):
            if mode=='failure':raise ValueError(SECRET)
            if mode=='timeout':await asyncio.sleep(10)
            return [.9]
    with environment() as (r,t):
        service=KnowledgeRetrievalService(repo,dense_provider=None if mode in {'lexical','empty'} else Dense(),dense_timeout_seconds=.01,observer=knowledge_stage)
        with tool_scope('knowledge_search'):
            result=await service.retrieve(query())
        records=spans(r,'knowledge.retrieval')
        assert {s.attributes['slopanoc.dependency_operation'] for s in records}=={'metadata','applicability','sparse','dense','fusion'}
        assert len(records)==5 and repo.calls==1
        assert len(result.items)==(0 if mode=='empty' else 1)
        assert all(s.kind.name=='INTERNAL' for s in records)
        assert all(s.parent.span_id==spans(r,'slopanoc.tool')[0].context.span_id for s in records)
        assert not points(r,'slopanoc.dependency.requests')
        if mode in {'timeout','failure'}:
            dense=next(s for s in records if s.attributes['slopanoc.dependency_operation']=='dense')
            assert dense.attributes['slopanoc.error_code']==('KNOWLEDGE_TIMEOUT' if mode=='timeout' else 'KNOWLEDGE_PROVIDER_ERROR')
            assert result.mode.value=='lexical'
            assert points(r,'slopanoc.knowledge.fallback')
        assert SECRET not in capture(r)

@pytest.mark.asyncio
@pytest.mark.parametrize('break_at',['create','enter','exit','count','dense_result'])
async def test_observer_failure_never_changes_evidence(break_at):
    class Broken:
        def __enter__(self):
            if break_at=='enter':raise ValueError(SECRET)
            return self
        def __exit__(self,*args):
            if break_at=='exit':raise ValueError(SECRET)
        def count(self,*args):
            if break_at=='count':raise ValueError(SECRET)
        def dense_result(self,*args):
            if break_at=='dense_result':raise ValueError(SECRET)
    def observer(op):
        if break_at=='create':raise ValueError(SECRET)
        return Broken()
    base=await KnowledgeRetrievalService(Repo()).retrieve(query())
    observed=await KnowledgeRetrievalService(Repo(),observer=observer).retrieve(query())
    assert observed==base

@pytest.mark.asyncio
async def test_m3_embedding_exact_counts_parented_under_dense():
    from backend.tools.knowledge.dense_similarity import VertexEmbeddingSimilarityProvider
    calls=[];observations=[]
    def handler(req):
        calls.append(1)
        return httpx.Response(200,json={'embeddings':[{'values':[.1,.2]}]})
    async with fake_model(handler) as (base,client):
        provider=VertexEmbeddingSimilarityProvider(dimension=2,client_factory=lambda:client)
        with environment() as (r,t), observation_sink(observations.append,runtime=r):
            with tool_scope('knowledge_search'):
                service=KnowledgeRetrievalService(Repo(),dense_provider=provider,observer=knowledge_stage)
                await service.retrieve(query());await service.retrieve(query())
            requests=spans(r,'gen_ai.request');models=spans(r,'slopanoc.model.operation')
            assert len(requests)==len(calls)==len(observations)==3
            assert len(models)==3
            dense_ids={s.context.span_id for s in spans(r,'knowledge.retrieval') if s.attributes['slopanoc.dependency_operation']=='dense'}
            assert all(m.parent.span_id in dense_ids for m in models)
            assert not spans(r,'http.client')
            assert SECRET not in capture(r)

@pytest.mark.asyncio
async def test_knowledge_cancellation_stages_close():
    started=asyncio.Event()
    class SlowRepo:
        async def list_all(self):started.set();await asyncio.Event().wait()
    with environment() as (r,t):
        task=asyncio.create_task(KnowledgeRetrievalService(SlowRepo(),observer=knowledge_stage).retrieve(query()))
        await started.wait();task.cancel()
        with pytest.raises(asyncio.CancelledError):await task
        assert spans(r,'knowledge.retrieval')[0].attributes['slopanoc.status']=='CANCELLED'

@pytest.mark.asyncio
async def test_actual_repository_query_is_child_of_metadata_stage():
    from backend.knowledge.repository.sqlalchemy import SqlAlchemyKnowledgeRepository
    repo=SqlAlchemyKnowledgeRepository('sqlite+aiosqlite:///:memory:')
    try:
        await repo.add(_governed_object(content='router '+SECRET))
        with environment() as (r,t):
            with tool_scope('knowledge_search'):
                result=await KnowledgeRetrievalService(repo,observer=knowledge_stage).retrieve(query())
            assert len(result.items)==1
            metadata=next(s for s in spans(r,'knowledge.retrieval') if s.attributes['slopanoc.dependency_operation']=='metadata')
            queries=spans(r,'db.client')
            assert len(queries)==1 and queries[0].parent.span_id==metadata.context.span_id
            assert queries[0].attributes['slopanoc.dependency']=='knowledge_db'
            assert SECRET not in capture(r) and not t.snapshot().dependencies
    finally:await repo.close()
