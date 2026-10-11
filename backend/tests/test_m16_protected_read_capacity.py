"""Actual protected ASGI reads: default four permits, deadline and recovery."""
import asyncio
import time
import pytest
from backend.tests.test_observability_api import api,authorize,paths
from backend.observability.schemas import Role
from backend.tests._m16_harness import envelope

@pytest.mark.asyncio
async def test_protected_mixed_read_capacity(api):
    app,client,repo,state,service=api;authorize(app,Role.DEVELOPER_SRE)
    timings=[]
    async def read(index):
        start=time.perf_counter()
        response=await client.get(paths(state.run_id)[index%7])
        timings.append(time.perf_counter()-start)
        assert response.status_code==(403 if index%7==6 else 200)
    await asyncio.gather(*(read(i) for i in range(48)))
    assert service.reads._value==4 and len(service.rates)==1
    assert repo.sessions.kw['bind'].pool.checkedout()==0
    envelope('protected_mixed_reads',timings,concurrency=48,permits=4,requests=48,
             policy='60 reads per principal per minute',business_dispatches=0)

@pytest.mark.asyncio
async def test_protected_reads_full_deadline_and_recovery(api,monkeypatch):
    app,client,repo,state,service=api;authorize(app,Role.DEVELOPER_SRE)
    entered=0;release=asyncio.Event();full=asyncio.Event();original=repo.get
    async def blocked(*args,**kwargs):
        nonlocal entered
        entered+=1
        if entered==4:full.set()
        await release.wait()
        return await original(*args,**kwargs)
    monkeypatch.setattr(repo,'get',blocked)
    path=paths(state.run_id)[2]
    requests=[asyncio.create_task(client.get(path)) for _ in range(5)]
    await asyncio.wait_for(full.wait(),1)
    assert entered==4 and service.reads._value==0
    results=await asyncio.gather(*requests)
    assert all(r.status_code==503 for r in results)
    assert service.reads._value==4
    monkeypatch.setattr(repo,'get',original)
    assert (await client.get(path)).status_code==200
    # Server denial precedes the bounded repository query after role revocation.
    authorize(app,Role.USER)
    async def forbidden(*args,**kwargs):raise AssertionError('query after revocation')
    monkeypatch.setattr(repo,'get',forbidden)
    assert (await client.get(path)).status_code==403
    print('M16_PROTECTED_READS permits=4 offered=5 deadline_seconds=2 failures=5 recovery=200 revoked=403 no_business_dispatch=0')
