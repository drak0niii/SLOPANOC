from datetime import timedelta
import pytest
from backend.tests._m12_reconciliation import *
from backend.tests.test_observability_api import api,authorize
from backend.observability.schemas import Role,ROLE_PERMISSIONS
from backend.observability.authorization import Principal,verified_principal_provider

PATHS=['/api/observability/finops/reconciliation','/api/observability/finops/reconciliation/2026-01-10','/api/observability/finops/allocation']

@pytest.mark.asyncio
@pytest.mark.parametrize('rows,status,currencies',[
    ([charge(quantity='1100')],'VARIANCE_REVIEW',['EUR']),
    ([charge(sku={'id':'unknown'})],'PARTIALLY_RECONCILED',['EUR']),
    ([charge(),charge(currency='USD')],'VARIANCE_REVIEW',['EUR','USD']),
])
async def test_safe_api_partial_variance_and_native_currency_states(api,system,monkeypatch,rows,status,currencies):
    app,client,_,_,backend=api;system.a.rows=tuple(rows);await seed(system)
    backend.financial_sources=system.sources;backend.reconciliation_repository=system.repo;authorize(app,Role.FINOPS)
    import backend.observability.finops.reconciliation_service as reads
    monkeypatch.setattr(reads,'now',lambda:NOW)
    for path in PATHS:
        response=await client.get(path);assert response.status_code==200,response.text
        item=response.json()['items'][0]
        assert item['status']==status and item['display_state']!='Reconciled'
        assert [a['currency'] for a in item['amounts']]==currencies
        assert all(a['controls']=='PASS' for a in item['amounts'])

@pytest.mark.asyncio
@pytest.mark.parametrize('role',list(Role))
@pytest.mark.parametrize('path',PATHS)
async def test_financial_capability_only(api,role,path):
    app,client,_,_,backend=api;authorize(app,role)
    r=await client.get(path)
    assert r.status_code==(200 if role==Role.FINOPS else 403),r.text

@pytest.mark.asyncio
async def test_unverified_identity_scope_and_validation(api):
    app,client,_,_,backend=api
    for p in PATHS:assert (await client.get(p)).status_code==401
    app.dependency_overrides[verified_principal_provider]=lambda:Principal('dev',Role.FINOPS,ROLE_PERMISSIONS[Role.FINOPS],frozenset({'production'}),False)
    for p in PATHS:assert (await client.get(p)).status_code==401
    authorize(app,Role.FINOPS)
    for p in PATHS:assert (await client.get(p,params={'environment':'production'})).status_code==403
    assert (await client.get(PATHS[1])).json()['items'][0]['display_state']=='Source unavailable'
    for params in (dict(limit=32),dict(start='2025-01-01',end='2026-01-01'),dict(cursor='SELECT_TOKEN')):
        assert (await client.get(PATHS[0],params=params)).status_code in (400,422)
    assert (await client.get(PATHS[0]+'/2026-13-99')).status_code==400

@pytest.mark.asyncio
async def test_published_only_safe_projection_source_stale_and_revocation(api,system,monkeypatch):
    app,client,_,_,backend=api;await seed(system);backend.financial_sources=system.sources;backend.reconciliation_repository=system.repo
    authorize(app,Role.FINOPS)
    import backend.observability.finops.reconciliation_service as reads
    monkeypatch.setattr(reads,'now',lambda:NOW)
    response=await client.get(PATHS[1]);assert response.status_code==200,response.text
    v=response.json()['items'][0]
    assert v['display_state']=='Reconciled' and v['amounts'][0]['direct']=='10' and v['quantities'][0]['confidence']=='AGGREGATED'
    assert v['controls']==dict(signed_net='PASS',components='PASS',absolute='PASS',count='PASS')
    for marker in ('ACCOUNT_SENTINEL','PROJECT_SENTINEL','RESOURCE_SENTINEL','SECRET_SENTINEL','sku-input','service-ai','SELECT','payload_hash'):
        assert marker not in response.text
    monkeypatch.setattr(reads,'now',lambda:JAN+timedelta(days=10))
    v=(await client.get(PATHS[1])).json()['items'][0]
    assert v['billing_freshness']=='STALE' and v['display_state']=='Stale source' and v['amounts'][0]['net']=='10'
    authorize(app,Role.ADMIN);assert (await client.get(PATHS[1])).status_code==403

@pytest.mark.asyncio
async def test_no_query_jobs_on_get_and_moving_source_cursor(api,system,monkeypatch):
    app,client,_,_,backend=api;await seed(system);backend.financial_sources=system.sources;backend.reconciliation_repository=system.repo;authorize(app,Role.FINOPS)
    import backend.observability.finops.reconciliation_service as reads
    monkeypatch.setattr(reads,'now',lambda:NOW)
    async def forbidden(*args):raise AssertionError('GET ran source extraction')
    monkeypatch.setattr(system.a,'extract',forbidden)
    response=await client.get(PATHS[0],params={'start':'2026-01-09','end':'2026-01-11','limit':1})
    cursor=response.json()['next_cursor'];assert cursor
    params={'start':'2026-01-09','end':'2026-01-11','limit':1,'cursor':cursor}
    assert (await client.get(PATHS[0],params=params)).status_code==200
    await system.repo.reserve('development',SCOPE,WINDOW.partition)
    assert (await client.get(PATHS[0],params=params)).status_code==400

@pytest.mark.asyncio
async def test_current_accounting_debt_overlay_cannot_remain_green(api,system,monkeypatch):
    from types import SimpleNamespace
    app,client,_,_,backend=api;await seed(system);backend.financial_sources=system.sources;backend.reconciliation_repository=system.repo
    backend.accounting=SimpleNamespace(repository=system.runtime);authorize(app,Role.FINOPS)
    import backend.observability.finops.reconciliation_service as reads
    monkeypatch.setattr(reads,'now',lambda:NOW)
    i=await capture(system.runtime)
    # Add a real durable started obligation after the known-good generation.
    from uuid import uuid4
    i=i.model_copy(update={'attempt_id':uuid4(),'logical_call_id':uuid4(),'provider_request_id':uuid4()})
    await system.runtime.admit(i,NOW);await system.runtime.start(i,NOW)
    value=(await client.get(PATHS[1])).json()['items'][0]
    assert value['display_state']=='Partial' and value['current_accounting_debt']==1
    assert value['status']=='RECONCILED' # immutable historical assessment stays intact
