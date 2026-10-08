"""M9 producers exercise real M2 ownership with explicitly local doubles."""
import asyncio
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import Mock
import pytest
from sqlalchemy import select
from backend.api.chat_service import ChatService
from backend.api.session_service import ApiSessionService
from backend.api.streaming_events import StreamEventType
from backend.observability.runtime import Runtime
from backend.observability.slo_sources import Writer, subject_digest
from backend.observability.slo_rollups import Rollups, SREReceiptRow, Receipt
from backend.tests._api_fakes import FakeRunner
from backend.tests._m7_storage import storage, close_chat_storage
from backend.tests.test_observability_runtime import config


@pytest.mark.asyncio
@pytest.mark.parametrize('disconnect', [False, True])
async def test_actual_chat_receipt_is_unsampled_and_cannot_own_completion(tmp_path, disconnect):
    db,_=await storage(tmp_path/'chat.db')
    runtime=Runtime(config())
    rollups=Rollups(db.sessions)
    writer=Writer(rollups,runtime.config,runtime)
    runtime.sre=writer
    sessions=ApiSessionService();sid=await sessions.create_session()
    chat=ChatService(sessions,runner=FakeRunner(sessions),observability_runtime=runtime)
    try:
        events=chat.execute_turn_events(sid,'synthetic general request',sse_expected=True)
        first=await anext(events)
        if disconnect:
            await events.aclose()
            await asyncio.gather(*tuple(chat._background_turns))
        else:
            remaining=[e async for e in events]
            assert any(e and e.type==StreamEventType.MESSAGE_COMPLETED for e in remaining)
        await writer.drain()
        async with db.sessions() as session:
            row=await session.get(SREReceiptRow,first.run_id)
            receipt=Receipt.model_validate(row.payload)
        assert receipt.terminal_status=='COMPLETED' and receipt.valid_outcome
        assert receipt.terminal_count==1 and receipt.trace_complete
        assert receipt.subject_digest==subject_digest('api-user')
        before=receipt.terminal_status
        if not disconnect:
            assert await rollups.receive(receipt.run_id,receipt.subject_digest,('development',),receipt.emitted_at+timedelta(seconds=1))
            assert await rollups.receive(receipt.run_id,receipt.subject_digest,('development',),receipt.emitted_at+timedelta(seconds=2))
        assert receipt.terminal_status==before
        assert not chat._background_turns
    finally:
        await writer.close();runtime.close();await db.close();await close_chat_storage(chat)


@pytest.mark.asyncio
async def test_whole_knowledge_population_does_not_count_stages(monkeypatch):
    from backend.knowledge.retrieval.service import KnowledgeRetrievalService
    from backend.tests.test_observability_dependency_knowledge import query
    from backend.observability.slo_sources import knowledge_operation
    events=[];writer=SimpleNamespace(publish=events.append,lost=False)
    monkeypatch.setattr('backend.observability.agent_instrumentation.runtime_for_execution',lambda:SimpleNamespace(sre=writer))
    class Repository:
        async def list_all(self):return []
    service=KnowledgeRetrievalService(Repository(),operation_observer=knowledge_operation)
    await service.retrieve(query())
    assert len(events)==1 and events[0][0]=='dependency_knowledge' and events[0][2].good==1
    async def fail(_query):raise RuntimeError('SYNTHETIC_FAILURE')
    monkeypatch.setattr(service,'_retrieve',fail)
    with pytest.raises(RuntimeError):await service.retrieve(query())
    assert len(events)==2 and events[1][2].bad==1

