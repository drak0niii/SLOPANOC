from datetime import timedelta
import pytest
from backend.tests._m13_unit_economics import *
from backend.tests.test_finops_unit_economics_publication import setup
from backend.tests.test_observability_api import api,authorize
from backend.observability.schemas import Role,ROLE_PERMISSIONS
from backend.observability.authorization import Principal,verified_principal_provider
from backend.observability.finops.unit_denominators import utcnow

PATH='/api/observability/finops/unit-economics'
PARAMS={'start':'2026-01-10','end':'2026-01-11'}

@pytest.mark.asyncio
@pytest.mark.parametrize('role',list(Role))
@pytest.mark.parametrize('path',[PATH,PATH+'/PROVIDER_ATTEMPT'])
async def test_financial_capability_and_environment_enforced(api,role,path):
    app,client,_,_,backend=api;authorize(app,role)
    response=await client.get(path,params=PARAMS)
    assert response.status_code==(200 if role==Role.FINOPS else 403),response.text

@pytest.mark.asyncio
async def test_unverified_identity_no_capability_and_invalid_filters(api):
    app,client,_,_,backend=api
    assert (await client.get(PATH)).status_code==401
    app.dependency_overrides[verified_principal_provider]=lambda:Principal('dev',Role.FINOPS,ROLE_PERMISSIONS[Role.FINOPS],frozenset({'development'}),False)
    assert (await client.get(PATH)).status_code==401
    authorize(app,Role.FINOPS)
    assert (await client.get(PATH,params={'environment':'production'})).status_code==403
    for params in ({'limit':32},{'start':'2025-01-01','end':'2026-01-01'},{'currency':'EURUSD'},{'cursor':'SECRET_SQL'},{'dimension':'SECRET_SQL'}):
        assert (await client.get(PATH,params=params)).status_code in (400,422)
    assert (await client.get(PATH+'/UNKNOWN')).status_code==400

@pytest.mark.asyncio
async def test_cache_projection_no_jobs_privacy_and_stale_source_overlay(api,system,monkeypatch):
    app,client,_,_,backend=api;repo,worker=await setup(system)
    assert await worker.run_period(WINDOW,utcnow())=='PUBLISHED'
    backend.unit_economics_repository=repo;backend.financial_sources=system.sources;authorize(app,Role.FINOPS)
    import backend.observability.finops.unit_economics_service as reads
    monkeypatch.setattr(reads,'now',lambda:NOW)
    async def forbidden(*args,**kwargs):raise AssertionError('GET triggered source work')
    monkeypatch.setattr(worker,'run_period',forbidden);monkeypatch.setattr(system.a,'extract',forbidden)
    response=await client.get(PATH+'/PROVIDER_ATTEMPT',params=PARAMS)
    assert response.status_code==200,response.text
    page=response.json();assert page['analytical_views']=='ALTERNATIVE_NON_ADDITIVE'
    assert page['items'][0]['results'][0]['direct_cost_per_unit'] is not None
    nofx=(await client.get(PATH+'/PROVIDER_ATTEMPT',params=dict(PARAMS,currency='USD'))).json()['items'][0]['results'][0]
    assert nofx['state']=='CURRENCY_UNAVAILABLE' and nofx['direct_cost_per_unit'] is None
    for secret in ('PROJECT_SENTINEL','ACCOUNT_SENTINEL','RESOURCE_SENTINEL','SECRET_SENTINEL','sku-input','service-ai','distribution_controls','payload_hash','classified','scope_rules','source_vector'):
        assert secret not in response.text
    monkeypatch.setattr(reads,'now',lambda:JAN+timedelta(days=10))
    stale=(await client.get(PATH+'/PROVIDER_ATTEMPT',params=PARAMS)).json()['items'][0]['results'][0]
    assert stale['state']=='SOURCE_STALE' and stale['direct_cost_per_unit'] is not None

@pytest.mark.asyncio
async def test_unavailable_units_null_and_cursor_scope_mutation_rejected(api,system):
    app,client,_,_,backend=api;authorize(app,Role.FINOPS)
    r=await client.get(PATH+'/RESOLVED_INCIDENT',params=PARAMS)
    v=r.json()['items'][0]['results'][0];assert v['state']=='DEFINED_NOT_AVAILABLE' and v['direct_cost_per_unit'] is None
    repo,worker=await setup(system);await worker.run_period(WINDOW,utcnow())
    backend.unit_economics_repository=repo;backend.financial_sources=system.sources
    params={'start':'2026-01-09','end':'2026-01-11','limit':1}
    cursor=(await client.get(PATH,params=params)).json()['next_cursor'];assert cursor
    assert (await client.get(PATH,params=dict(params,cursor=cursor))).status_code==200
    await repo.reserve('development',SCOPE,WINDOW.partition)
    assert (await client.get(PATH,params=dict(params,cursor=cursor))).status_code==400
