"""Owned canonical backend hard-exit driver; usable only with M16 egress guard."""
import asyncio
import json
import os
from pathlib import Path
import sys
import httpx
from sqlalchemy import select
from google.adk.sessions import DatabaseSessionService
from backend.api.chat_service import ChatService
from backend.api.session_service import ApiSessionService
from backend.tests._api_fakes import FakeRunner
from backend.tests.test_observability_model_provider import fake_model,payload,request
from backend.observability.config import ObservabilityConfig
from backend.observability.database import Database
from backend.observability.models import Base
from backend.observability.finops.models import FinOpsBase
from backend.observability.finops.repository import Repository as AccountingRepository
from backend.observability.finops.usage_ledger import Ledger,install
from backend.observability.repository import Repository
from backend.observability.persistence import Coordinator,install_coordinator
from backend.observability.runtime import Runtime
from backend.observability.model_adapter import instrument_model
from backend.observability.tool_instrumentation import tool_scope


def receipt(path,domain):
    with path.open('a') as stream:
        stream.write(domain+'\n');stream.flush();os.fsync(stream.fileno())

async def run(root,phase,collector=None):
    assert os.environ.get('SLOPANOC_M16_LOCAL_TEST')=='1'
    db=Database('sqlite+aiosqlite:///'+str(root/'state.db'),isolated_test=True)
    async with db.engine.begin() as connection:
        for metadata in (Base.metadata,FinOpsBase.metadata):await connection.run_sync(metadata.create_all)
    cfg=ObservabilityConfig(observability_enabled=True,otel_enabled=True,otel_exporter_mode='local',projection_enabled=True,
        projection_write_spacing_seconds=.01,projection_checkpoint_seconds=.05)
    repo=Repository(db.sessions,cfg);accounting=AccountingRepository(db.sessions)
    ledger=Ledger(accounting,cfg);install(ledger)
    coordinator=Coordinator(repo,cfg);install_coordinator(coordinator);coordinator.start()
    if collector:
        from pydantic import SecretStr
        cfg=cfg.model_copy(update={'otel_exporter_mode':'otlp','otel_exporter_otlp_endpoint':SecretStr(collector),'otel_batch_interval_seconds':.01})
    runtime=Runtime(cfg)
    sessions=ApiSessionService(DatabaseSessionService('sqlite+aiosqlite:///'+str(root/'session.db')))
    sid=await sessions.create_session()
    (root/'session_id').write_text(sid)
    for method,target in (('admit','admission'),('start','started')):
        original=getattr(accounting,method)
        async def call(*args,_original=original,_target=target,**kwargs):
            result=await _original(*args,**kwargs)
            if phase==_target:os._exit(73)
            return result
        setattr(accounting,method,call)
    persist=repo.persist
    async def persisted(value):
        result=await persist(value)
        if value.terminal_at is not None and phase=='terminal_persisted':os._exit(73)
        return result
    repo.persist=persisted
    if phase=='accepted':
        import backend.api.chat_service as chat
        begin=chat.begin_turn
        def accepted(*args,**kwargs):
            turn=begin(*args,**kwargs);receipt(root/'dispatch','accepted');os._exit(73)
        chat.begin_turn=accepted
    if phase=='cleanup':
        from backend.observability.watchdog import Watchdog
        original=Watchdog.stop
        async def stop(self):receipt(root/'dispatch','cleanup');os._exit(73)
        Watchdog.stop=stop
    if phase=='pre_delivery':
        from backend.observability.delivery import DeliveryBuffer
        original=DeliveryBuffer.put
        async def put(self,event):
            if getattr(getattr(event,'type',None),'value',None)=='message.completed':os._exit(73)
            return await original(self,event)
        DeliveryBuffer.put=put
    def provider(req):
        receipt(root/'dispatch','provider')
        return httpx.Response(200,json=payload())
    async with fake_model(provider) as (base,_):
        model=instrument_model(base,'team_manager','orchestration')
        async def execute(*args):
            receipt(root/'dispatch','agent')
            _=[value async for value in model.generate_content_async(request())]
            if phase=='provider_completed':os._exit(73)
            with tool_scope('get_current_time_context'):
                receipt(root/'dispatch','tool')
            if phase=='tool_completed':os._exit(73)
            if phase=='hold':
                assert runtime.flush(2)
                assert await coordinator.flush(2)
                import json
                from backend.tests._m16_harness import resources
                snapshot=resources()
                snapshot.update(db_checked_out=db.engine.pool.checkedout(),
                    session_db_checked_out=sessions.adk_session_service.db_engine.pool.checkedout(),
                    trace_queue=runtime.health.snapshot()['trace']['queue_size'],
                    log_queue=runtime.health.snapshot()['log']['queue_size'])
                (root/'ready').write_text(json.dumps(snapshot))
                while True:await asyncio.sleep(.05)
        service=ChatService(sessions,runner=FakeRunner(sessions,side_effect=execute),observability_runtime=runtime)
        _=[event async for event in service.execute_turn_events(sid,'synthetic ordinary request')]
        await coordinator.flush(2)
    raise AssertionError('Requested hard-exit boundary was not reached')

if __name__=='__main__':asyncio.run(run(Path(sys.argv[1]),sys.argv[2],sys.argv[3] if len(sys.argv)>3 else None))
