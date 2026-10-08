from datetime import timedelta
from uuid import uuid4
import pytest
import pytest_asyncio
import httpx
from backend.api.app import create_app
from backend.api.observability_routes import service as service_dependency
from backend.observability.authorization import Principal,verified_principal_provider
from backend.observability.schemas import Role,Permission,ROLE_PERMISSIONS
from backend.observability.service import Service
from backend.tests._m7_storage import storage,state,config
from backend.observability.projection import utcnow


@pytest_asyncio.fixture
async def api(tmp_path):
    db,repo=await storage(tmp_path/'api.sqlite');s=state(trace_id='1'*32,root_span_id='2'*16)
    await repo.persist(s)
    app=create_app();backend=Service(repo,config())
    app.dependency_overrides[service_dependency]=lambda:backend
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://test') as client:
        yield app,client,repo,s,backend
    await db.close()


def paths(run):
    return ['/api/observability/runs','/api/observability/active',f'/api/observability/runs/{run}',
        f'/api/observability/runs/{run}/timeline','/api/observability/sessions/session-1/runs',
        '/api/observability/health','/api/observability/config']


@pytest.mark.asyncio
@pytest.mark.parametrize('role',list(Role))
@pytest.mark.parametrize('endpoint',range(7))
async def test_every_endpoint_enforces_canonical_role_capabilities(api,role,endpoint):
    app,client,repo,s,backend=api
    app.dependency_overrides[verified_principal_provider]=lambda:Principal('trusted',role,ROLE_PERMISSIONS[role],frozenset({'development'}),True)
    response=await client.get(paths(s.run_id)[endpoint])
    permission=Permission.EFFECTIVE_CONFIGURATION if endpoint==6 else Permission.OPERATIONAL_METADATA
    assert response.status_code==(200 if permission in ROLE_PERMISSIONS[role] else 403),response.text
    if response.status_code==200 and endpoint==2:
        assert ('technical' in response.json())==(role==Role.DEVELOPER_SRE)
        assert response.json()['source']=='durable'
    if response.status_code==200 and endpoint==6:
        assert all(item['read_only'] is True for item in response.json())
        assert not any('endpoint' in item['setting'] and item['setting']!='collector_endpoint_configured' for item in response.json())


@pytest.mark.asyncio
@pytest.mark.parametrize('endpoint',range(7))
async def test_production_only_dev_header_is_not_verified_and_query_not_called(api,endpoint,monkeypatch):
    app,client,repo,s,backend=api
    backend.config=config(otel_environment='production')
    async def forbidden(*args,**kwargs):raise AssertionError('DB query before auth')
    monkeypatch.setattr(repo,'get',forbidden)
    for headers in ({},{'X-SLOPANOC-DEV-USER':'admin','X-Role':'Admin','Authorization':'Bearer SENTINEL_CREDENTIAL'}):
        response=await client.get(paths(s.run_id)[endpoint],headers=headers)
        assert response.status_code==401
        assert 'SENTINEL' not in response.text


@pytest.mark.asyncio
@pytest.mark.parametrize('principal',[None,{'verified':True},Principal('admin',Role.ADMIN,ROLE_PERMISSIONS[Role.ADMIN],frozenset({'development'}),False),
    Principal('admin',Role.FINOPS,frozenset({Permission.OPERATIONAL_METADATA}),frozenset({'development'}),True)])
async def test_malformed_unverified_and_elevated_claims_denied(api,principal):
    app,client,repo,s,backend=api
    app.dependency_overrides[verified_principal_provider]=lambda:principal
    for path in paths(s.run_id):assert (await client.get(path)).status_code==401


def authorize(app,role=Role.OPERATOR,environments=frozenset({'development'})):
    app.dependency_overrides[verified_principal_provider]=lambda:Principal('trusted',role,ROLE_PERMISSIONS[role],environments,True)


@pytest.mark.asyncio
async def test_cursor_filters_order_bounds_and_scope(api):
    app,client,repo,s,backend=api;authorize(app)
    await repo.persist(state());await repo.persist(state())
    first=await client.get('/api/observability/runs',params={'limit':1});assert first.status_code==200
    cursor=first.json()['next_cursor'];assert cursor
    second=await client.get('/api/observability/runs',params={'limit':1,'cursor':cursor})
    assert second.status_code==200,second.text
    assert second.json()['items'][0]['run_id']!=first.json()['items'][0]['run_id']
    assert second.json()['as_of']==first.json()['as_of']
    for params in ({'limit':101},{'limit':0},{'cursor':'bad'},{'cursor':'x'*513},
        {'status':'DROP_TABLE_SENTINEL'},{'stage':'SENTINEL_SQL'},{'status':'FAILED','cursor':cursor},
        {'since':'2020-01-01T00:00:00Z','until':'2026-01-01T00:00:00Z'},
        {'since':'2026-01-01T00:00:00'}):
        response=await client.get('/api/observability/runs',params=params)
        assert response.status_code==400,response.text
        assert 'SENTINEL' not in response.text
    filtered=await client.get('/api/observability/runs',params={'status':'RUNNING','stage':s.current_stage.value})
    assert len(filtered.json()['items'])==3
    assert (await client.get('/api/observability/runs',params={'environment':'production'})).status_code==403
    authorize(app,environments=frozenset({'production'}))
    assert (await client.get(f'/api/observability/runs/{s.run_id}')).status_code==404
    assert (await client.get(f'/api/observability/runs/{uuid4()}')).status_code==404
    assert (await client.get('/api/observability/runs/not-a-uuid')).status_code==400


@pytest.mark.asyncio
async def test_safe_db_failure_health_and_default_page(api,monkeypatch):
    app,client,repo,s,backend=api;authorize(app)
    assert (await client.get('/api/observability/runs')).status_code==200
    async def failure(*args,**kwargs):raise RuntimeError('SENTINEL_SQL_DSN_TOKEN')
    monkeypatch.setattr(repo,'get',failure)
    response=await client.get(f'/api/observability/runs/{s.run_id}')
    assert response.status_code==503 and 'SENTINEL' not in response.text
    monkeypatch.setattr(repo,'stale_count',failure)
    health=await client.get('/api/observability/health')
    assert health.status_code==200 and health.json()['persistence']=='unavailable'
    assert (await client.get('/health')).status_code==200


@pytest.mark.asyncio
async def test_server_read_throttle_is_bounded(api):
    app,client,repo,s,backend=api;authorize(app)
    for _ in range(60):assert (await client.get('/api/observability/health')).status_code==200
    assert (await client.get('/api/observability/health')).status_code==429
    assert len(backend.rates)==1


@pytest.mark.asyncio
async def test_health_process_scope_and_stale_elapsed_are_honest(api):
    app,client,repo,s,backend=api
    authorize(app,environments=frozenset({'production'}))
    assert (await client.get('/api/observability/health')).status_code==403
    authorize(app)
    old=utcnow()-timedelta(minutes=5)
    stalled=state(started_at=old,stage_started_at=old,last_progress_at=old,heartbeat_at=old,observed_at=old,elapsed_ms=12)
    await repo.persist(stalled)
    response=await client.get(f'/api/observability/runs/{stalled.run_id}')
    assert response.status_code==200
    assert response.json()['classification']=='STALE' and response.json()['outcome_unknown']
    assert response.json()['status']=='RUNNING' and response.json()['elapsed_ms']==12
