"""Actual model transport under concurrent durability faults; no provider bypass."""
import asyncio
import time
from types import SimpleNamespace
import httpx
import pytest
from sqlalchemy import select, func
from backend.tests.test_finops_runtime_ledger import accounting, identity, payload as accounting_payload, started
from backend.tests.test_observability_model_provider import fake_model, payload, request
from backend.observability.model_adapter import instrument_model
from backend.observability.finops.usage_ledger import install, AdmissionFailed
from backend.observability.finops.models import AttemptRow, LedgerRow
from backend.tests._m16_harness import Dispatches, envelope

@pytest.mark.asyncio
@pytest.mark.parametrize('phase',['admit','start'])
@pytest.mark.parametrize('concurrency',[4,8,12])
async def test_failed_durable_admission_under_concurrency(accounting,monkeypatch,phase,concurrency):
    db,repo,ledger=accounting; install(ledger); calls=Dispatches()
    async def failed(*args):
        await asyncio.sleep(.005)
        raise OSError('PRIVATE_SECRET_REASONING_SQL')
    monkeypatch.setattr(repo,phase,failed)
    def handler(req):
        calls.hit('provider')
        return httpx.Response(200,json=payload())
    async with fake_model(handler) as (base,client):
        model=instrument_model(base,'team_manager','orchestration')
        async def invoke():
            with pytest.raises(AdmissionFailed):
                _=[v async for v in model.generate_content_async(request())]
        await asyncio.gather(*(invoke() for _ in range(concurrency)))
    calls.expect()
    async with db.sessions() as s:
        assert await s.scalar(select(func.count()).select_from(LedgerRow))==0
        assert all(row.started_at is None for row in (await s.scalars(select(AttemptRow))).all())

@pytest.mark.asyncio
@pytest.mark.parametrize('concurrency',[4,8,12])
async def test_post_provider_capture_failure_never_replays(accounting,monkeypatch,concurrency):
    db,repo,ledger=accounting; install(ledger); calls=Dispatches()
    async def failed(*args):raise OSError('PRIVATE_SECRET_REASONING_SQL')
    monkeypatch.setattr(repo,'capture',failed)
    def handler(req):
        calls.hit('provider'); return httpx.Response(200,json=payload())
    async with fake_model(handler) as (base,client):
        model=instrument_model(base,'team_manager','orchestration')
        async def invoke():return [v async for v in model.generate_content_async(request())]
        results=await asyncio.gather(*(invoke() for _ in range(concurrency)))
    assert all(results)
    calls.expect(provider=concurrency)
    assert (await repo.health('development'))['debt']==concurrency
    assert await ledger.recover()==0
    calls.expect(provider=concurrency)

@pytest.mark.asyncio
async def test_admission_latency_and_concurrent_ledger_integrity(accounting):
    db,repo,ledger=accounting
    for concurrency in (4,8,12):
        values=[]
        async def invoke():
            attempt=SimpleNamespace(identity=identity())
            began=time.monotonic()
            await started(repo,attempt.identity)
            values.append(time.monotonic()-began)
            await repo.capture(accounting_payload(attempt.identity))
            await repo.materialize(attempt.identity.event_id)
        begin=time.monotonic()
        await asyncio.gather(*(invoke() for _ in range(concurrency)))
        envelope('accounting_sqlite',values,concurrency=concurrency,elapsed_seconds=time.monotonic()-begin,
                 actual_pool=str(type(db.engine.pool).__name__))
    async with db.sessions() as s:
        rows=(await s.scalars(select(LedgerRow))).all()
        assert len(rows)==24 and len({row.event_id for row in rows})==24
        assert sum(row.input_tokens for row in rows)==2400
    assert (await repo.health('development'))['debt']==0

@pytest.mark.asyncio
@pytest.mark.parametrize('phase',['admission','started','result','inbox'])
async def test_hard_crash_accounting_boundary_never_replays(accounting,tmp_path,phase):
    import subprocess,sys
    from backend.observability.database import Database
    from backend.observability.finops.repository import Repository
    from backend.observability.finops.usage_ledger import Ledger
    from backend.observability.config import ObservabilityConfig
    from backend.tests.test_finops_runtime_ledger import NOW
    db,_,_=accounting;url=str(db.engine.url);await db.close();counter=tmp_path/'physical-dispatch-count'
    code='''
import asyncio,os,sys
from datetime import timedelta
from pathlib import Path
from backend.observability.database import Database
from backend.observability.finops.repository import Repository
from backend.tests.test_finops_runtime_ledger import identity,payload,NOW
async def run():
 db=Database(sys.argv[1],isolated_test=True);repo=Repository(db.sessions)
 i=identity(deadline_at=NOW-timedelta(seconds=1));phase=sys.argv[2]
 await repo.admit(i)
 if phase=='admission':os._exit(73)
 await repo.start(i,i.started_at)
 if phase=='started':os._exit(73)
 with open(sys.argv[3],'w') as f:f.write('1');f.flush();os.fsync(f.fileno())
 if phase=='result':os._exit(73)
 await repo.capture(payload(i));os._exit(73)
asyncio.run(run())
'''
    result=await asyncio.to_thread(subprocess.run,[sys.executable,'-c',code,url,phase,str(counter)],capture_output=True,timeout=10)
    assert result.returncode==73,result.stderr.decode()
    replacement=Database(url,isolated_test=True)
    try:
        repo=Repository(replacement.sessions);worker=Ledger(repo,ObservabilityConfig())
        assert await worker.recover()==(1 if phase=='inbox' else 0)
        await repo.checkpoint('development',NOW)
        assert await worker.recover()==0
        async with replacement.sessions() as s:
            attempts=(await s.scalars(select(AttemptRow))).all();rows=(await s.scalars(select(LedgerRow))).all()
        assert len(attempts)==1 and len(rows)==(1 if phase=='inbox' else 0)
        if phase in ('started','result'):assert attempts[0].state=='OUTCOME_UNKNOWN'
        if phase=='admission':assert attempts[0].state=='NOT_STARTED'
        assert (counter.read_text() if counter.exists() else '0')==('1' if phase in ('result','inbox') else '0')
    finally:await replacement.close()
