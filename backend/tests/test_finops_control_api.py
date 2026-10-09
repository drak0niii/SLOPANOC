import pytest
from types import SimpleNamespace
from backend.tests._m14_controls import *
from backend.tests.test_observability_api import api,authorize
from backend.observability.schemas import Role,ROLE_PERMISSIONS
from backend.observability.authorization import Principal,verified_principal_provider
from backend.observability.finops.billing_service import FinancialSources
from backend.observability.finops.billing_sources import BillingSourceAdapter
from backend.observability.finops.control_repository import calculate
PATHS=['/api/observability/finops/'+p for p in ('budgets','forecasts','anomalies')]

@pytest.mark.asyncio
@pytest.mark.parametrize('role',list(Role))
@pytest.mark.parametrize('path',PATHS)
async def test_capability_server_enforced(api,role,path):
    app,client,_,_,backend=api;authorize(app,role)
    r=await client.get(path);assert r.status_code==(200 if role==Role.FINOPS else 403)

@pytest.mark.asyncio
async def test_empty_unverified_environment_bounds_and_no_mutation_routes(api):
    app,client,_,_,backend=api
    for p in PATHS:assert (await client.get(p)).status_code==401
    app.dependency_overrides[verified_principal_provider]=lambda:Principal('dev',Role.FINOPS,ROLE_PERMISSIONS[Role.FINOPS],frozenset({'production'}),False)
    for p in PATHS:assert (await client.get(p)).status_code==401
    authorize(app,Role.FINOPS)
    for p in PATHS:
        assert (await client.get(p,params={'environment':'production'})).status_code==403
        assert (await client.post(p,json={})).status_code==405
        assert (await client.get(p,params={'month':'2026-13'})).status_code==400
        assert (await client.get(p,params={'limit':101})).status_code==400
    assert (await client.get(PATHS[0])).json()['state']=='NOT_CONFIGURED'
    assert (await client.get(PATHS[1])).json()['state']=='UNEVALUATED'

@pytest.mark.asyncio
async def test_cache_privacy_cursor_and_stale_revocation(api,controls,monkeypatch):
    app,client,_,_,backend=api;authorize(app,Role.FINOPS)
    for i in range(1,8):await financial(controls,date(2026,10,i))
    adapter=BillingSourceAdapter(scope='approved-synthetic-scope');assert adapter.scope==BINDING
    backend.financial_sources=FinancialSources(adapters=[adapter]);backend.control_repository=controls.repo
    reg=Registry(budgets=(budget('4000'),));inp=await controls.adapter.load('development',BINDING,'2026-10','EUR',ISSUE)
    for kind in ('BUDGET','FORECAST','ANOMALY'):
        k,r=await controls.repo.reserve('development',BINDING,'2026-10',kind)
        assert await controls.repo.publish(k,r,inp,reg,POLICY,kind,calculate(inp,reg,POLICY,kind))=='PUBLISHED'
    import backend.observability.finops.control_service as service
    monkeypatch.setattr(service,'now',lambda:ISSUE)
    async def forbidden(*a,**kw):raise AssertionError('GET recomputed')
    monkeypatch.setattr(controls.repo,'publish',forbidden);monkeypatch.setattr(adapter,'extract',forbidden)
    for p in PATHS:
        res=await client.get(p);assert res.status_code==200,res.text
        assert BINDING not in res.text and 'source_vector' not in res.text and 'SELECT' not in res.text
    f=(await client.get(PATHS[1])).json()['items'][0]
    assert f['confidence']=='LOW' and f['range_type']=='SCENARIO_RANGE'
    assert (await client.get(PATHS[1]+'/'+f['generation'])).status_code==200
    assert (await client.get(PATHS[0]+'/budget-total')).status_code==200
    assert (await client.get(PATHS[1],params={'cursor':'SECRET'})).status_code==400
    monkeypatch.setattr(service,'now',lambda:ISSUE+timedelta(days=10))
    assert (await client.get(PATHS[1])).json()['state']=='SOURCE_STALE'
    authorize(app,Role.ADMIN);assert (await client.get(PATHS[1])).status_code==403
