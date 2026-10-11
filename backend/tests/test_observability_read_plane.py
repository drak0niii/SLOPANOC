"""Independent reader gates against disposable DBs; no cloud or shared credentials."""
import asyncio
import json
import subprocess
import sys
from unittest.mock import Mock
from uuid import uuid4
import httpx
import pytest
import pytest_asyncio
from sqlalchemy import text
from backend.api.observability_app import create_app, READ_PATHS, SOURCE_CLASSIFICATION
from backend.observability.read_config import ReadConfiguration
from backend.observability.authorization import Principal, verified_principal_provider
from backend.observability.schemas import Role, ROLE_PERMISSIONS
from backend.tests._m7_storage import storage, state, config


def authorize(app,role=Role.DEVELOPER_SRE,environments=('development',)):
    app.dependency_overrides[verified_principal_provider]=lambda:Principal(
        'reader-test',role,ROLE_PERMISSIONS[role],frozenset(environments),True)


def health_fixture(name):
    from pathlib import Path
    return json.loads((Path(__file__).parent/'fixtures/observability_health.json').read_text())[name]


@pytest.mark.asyncio
async def test_actual_reader_health_serialization_matches_frontend_fixture(reader):
    app,client,*_=reader
    response=await client.get('/api/observability/health')
    assert response.status_code==200
    payload=response.json()
    payload['as_of']=health_fixture('reader_healthy')['as_of']
    assert payload==health_fixture('reader_healthy')
    app.state.observability_service.repository=None
    response=await client.get('/api/observability/health')
    payload=response.json()
    payload['as_of']=health_fixture('reader_unavailable')['as_of']
    assert payload==health_fixture('reader_unavailable')
    assert (await client.get('/api/observability/runs')).status_code==503


def test_producer_health_serialization_matches_shared_fixture():
    from backend.observability.service import Service
    from fastapi.testclient import TestClient
    app=create_app(config=config(),settings=ReadConfiguration())
    authorize(app)
    app.state.observability_service=Service(None,config())
    with TestClient(app) as client:
        # The lifecycle owns reader service; substitute only the test's producer view.
        app.state.observability_service=Service(None,config(projection_enabled=False))
        response=client.get('/api/observability/health')
        payload=response.json()
        payload['as_of']=health_fixture('producer_disabled')['as_of']
        assert payload==health_fixture('producer_disabled')


@pytest.mark.asyncio
async def test_healthy_empty_durable_source_returns_canonical_empty_pages(reader):
    app,client,run,prod,db,repo=reader
    from sqlalchemy import delete
    from backend.observability.models import EventRow,RunRow,SummaryRow
    # Fixture writer pool only: the live reader pool remains read-only.
    async with db.engine.begin() as connection:
        for row in (EventRow,RunRow,SummaryRow):
            await connection.execute(delete(row))
    for path in ('runs','active'):
        response=await client.get('/api/observability/'+path)
        assert response.status_code==200
        assert response.json()['items']==[]
    assert (await client.get('/api/observability/health')).json()['persistence']=='healthy'
    authorize(app,Role.FINOPS)
    response=await client.get('/api/observability/finops/runtime-usage')
    assert response.status_code==200 and response.json()['items']==[]


@pytest_asyncio.fixture
async def reader(tmp_path):
    from backend.observability.slo_rollups import SRESourceRow
    from backend.observability.policy_models import RetentionCheckpoint
    from backend.observability.models import Base
    from backend.observability.finops.models import FinOpsBase
    from backend.observability.finops.billing_models import BillingBase
    from backend.observability.finops.reconciliation_models import ReconciliationBase
    from backend.observability.finops.unit_economics_models import UnitEconomicsBase
    from backend.observability.finops.control_models import ControlBase
    db,repo=await storage(tmp_path/'reader.sqlite')
    async with db.engine.begin() as conn:
        for base in (Base,FinOpsBase,BillingBase,ReconciliationBase,UnitEconomicsBase,ControlBase):
            await conn.run_sync(base.metadata.create_all)
    run=state(trace_id='1'*32,root_span_id='2'*16)
    await repo.persist(run)
    prod=state(environment='production');await repo.persist(prod)
    cfg=config(finops_enabled=True)
    policy=tmp_path/'policy.json';policy.write_text(cfg.model_dump_json())
    settings=ReadConfiguration(database_url='sqlite+aiosqlite:///'+str(tmp_path/'reader.sqlite'),
        producer_policy_file=policy,billing_scope='a'*64,pricing_scope='b'*64,source_mode='TEST_FIXTURE',
        allowed_origins=('http://localhost:5173',))
    app=create_app(config=cfg,settings=settings,isolated_test=True)
    authorize(app)
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://reader') as client:
            yield app,client,run,prod,db,repo
    await db.close()


def test_final_route_allowlist_and_clean_import():
    app=create_app(config=config(),settings=ReadConfiguration())
    assert {r.path for r in app.routes}=={'/health','/readiness'}|{'/api/observability'+p for p in READ_PATHS}
    assert all(r.methods=={'GET'} for r in app.routes)
    assert set(SOURCE_CLASSIFICATION)==READ_PATHS
    assert not any('sse-receipts' in r.path or '/api/sessions' in r.path for r in app.routes)
    code="from backend.api.observability_app import create_app; import sys; create_app(); assert 'backend.api.app' not in sys.modules; assert 'google.genai' not in sys.modules; assert 'google.adk' not in sys.modules"
    result=subprocess.run([sys.executable,'-c',code],capture_output=True,text=True)
    assert result.returncode==0,result.stderr


@pytest.mark.asyncio
async def test_read_pool_rejects_writes_health_unknown_and_policy(reader):
    app,client,run,prod,db,repo=reader
    health=(await client.get('/api/observability/health')).json()
    assert health['persistence']=='healthy'
    assert health['read_plane_state']=='healthy' and health['anoc_state']=='unknown'
    assert health['writer_scope']=='process_local_anoc_unavailable'
    for key in ('pending','admitted','failed','retries','accepted','collector_configured'):
        assert health.get(key) is None
    assert (await client.get('/health')).status_code==200
    assert (await client.get('/readiness')).status_code==200
    for statement in (
        'DELETE FROM observability_run_status',
        "UPDATE observability_run_status SET environment='production'",
        "INSERT INTO observability_run_status (run_id) VALUES ('forbidden')",
    ):
        async with app.state.read_database.sessions() as session:
            with pytest.raises(Exception,match='readonly'):
                await session.execute(text(statement))
    assert await repo.get(run.run_id,('development',)) is not None
    authorize(app,Role.ADMIN)
    assert (await client.get('/api/observability/config')).status_code==200
    assert (await client.get('/api/observability/policies')).status_code==200
    app.state.observability_service.producer_policy=None
    assert (await client.get('/api/observability/config')).status_code==503


@pytest.mark.asyncio
async def test_auth_role_ceilings_environment_and_cursors(reader):
    app,client,run,prod,db,repo=reader
    app.dependency_overrides.pop(verified_principal_provider)
    assert (await client.get('/api/observability/runs',headers={'X-Role':'Admin','X-SLOPANOC-DEV-USER':'root'})).status_code==401
    authorize(app,Role.FINOPS)
    assert (await client.get('/api/observability/runs')).status_code==403
    assert (await client.get('/api/observability/archive/'+'1'*32,params={'digest':'a'*64})).status_code==403
    assert (await client.get('/api/observability/finops/runtime-usage')).status_code==200
    authorize(app,Role.ADMIN)
    assert (await client.get('/api/observability/finops/runtime-usage')).status_code==403
    authorize(app)
    assert (await client.get('/api/observability/runs',params={'environment':'production'})).status_code==403
    assert (await client.get('/api/observability/runs/'+prod.run_id)).status_code==404
    authorize(app,environments=('development','production'))
    assert (await client.get('/api/observability/runs/'+prod.run_id,params={'environment':'development'})).status_code==404
    response=await client.get('/api/observability/runs',params={'limit':1})
    cursor=response.json()['next_cursor'];assert cursor
    assert (await client.get('/api/observability/runs',params={'limit':1,'cursor':cursor,'environment':'production'})).status_code==400
    for method in ('POST','PUT','PATCH','DELETE'):
        assert (await client.request(method,'/api/observability/sse-receipts')).status_code==405
    from backend.api.app import create_app as business_app
    assert any(r.path=='/api/observability/sse-receipts' and r.methods=={'POST'} for r in business_app().routes)


@pytest.mark.asyncio
async def test_explicit_cors_and_source_scope(reader):
    app,client,*_=reader
    for origin,status in [('http://localhost:5173',200),('http://attacker.invalid',400)]:
        r=await client.options('/api/observability/runs',headers={'Origin':origin,'Access-Control-Request-Method':'GET'})
        assert r.status_code==status
        assert r.headers.get('access-control-allow-origin')==('http://localhost:5173' if status==200 else None)
        assert r.headers.get('access-control-allow-credentials') is None
    assert (await client.options('/api/observability/runs',headers={'Origin':'http://localhost:5173','Access-Control-Request-Method':'POST'})).status_code==400
    for origin in ('*','null','https://example.com/','https://u:p@example.com','https://*.example.com'):
        with pytest.raises(ValueError):ReadConfiguration(allowed_origins=(origin,))
    with pytest.raises(ValueError):ReadConfiguration.from_environment({},environment='production')


@pytest.mark.asyncio
async def test_startup_and_every_get_has_zero_producer_side_effects(reader,monkeypatch):
    app,client,run,prod,db,repo=reader
    from sqlalchemy import event
    attempted_writes=[]
    def forbid_write(_conn,_cursor,statement,_parameters,_context,_executemany):
        if not statement.lstrip().upper().startswith(('SELECT','PRAGMA')):
            attempted_writes.append(statement.split()[0])
            raise AssertionError('Reader attempted a database write')
    event.listen(app.state.read_database.engine.sync_engine,'before_cursor_execute',forbid_write)
    targets=[
        'backend.api.app.warmup_shared_model',
        'backend.observability.runtime.acquire',
        'backend.observability.persistence.Coordinator.start',
        'backend.observability.slo_sources.Writer.__init__',
        'backend.observability.finops.usage_ledger.Ledger.start',
        'backend.observability.finops.billing_ingestion.Ingestion.start',
        'backend.observability.finops.reconciliation_worker.Worker.start',
        'backend.observability.finops.reconciliation_worker.Worker.run_period',
        'backend.observability.finops.unit_economics_worker.Worker.start',
        'backend.observability.finops.unit_economics_worker.Worker.run_period',
        'backend.observability.finops.control_worker.Worker.start',
        'backend.observability.finops.control_worker.Worker.evaluate',
        'backend.observability.finops.budgets.evaluate',
        'backend.observability.finops.forecasting.forecast',
        'backend.observability.finops.anomalies.evaluate',
        'backend.observability.retention_worker.RetentionWorker.run',
        'google.genai.models.Models.generate_content',
        'google.genai.models.AsyncModels.generate_content',
    ]
    spies=[]
    for target in targets:
        spy=Mock(side_effect=AssertionError('Forbidden reader side effect'))
        monkeypatch.setattr(target,spy);spies.append(spy)
    # Exercise actual startup under the spies, not just the fixture's preceding startup.
    other=create_app(config=config(finops_enabled=True),settings=app.state.observability_service.settings,isolated_test=True)
    async with other.router.lifespan_context(other):
        assert other.state.observability_service.runtime is None
        assert other.state.observability_service.coordinator is None
    for path in READ_PATHS:
        authorize(app,Role.FINOPS if '/finops/' in path else Role.ADMIN if path in ('/config','/policies') else Role.DEVELOPER_SRE)
        path=path.replace('{run_id}',run.run_id).replace('{session_id}','session-1').replace('{trace_id}','1'*32)
        path=path.replace('{slo_id}','availability').replace('{period}','2026-10-01').replace('{unit_id}','COST_PER_TURN')
        path=path.replace('{budget_id}','missing').replace('{generation_id}','a'*64).replace('{anomaly_id}','a'*64)
        r=await client.get('/api/observability'+path,params={'digest':'a'*64} if '/archive/' in path else {})
        assert r.status_code in (200,400,404,503),(path,r.status_code,r.text)
    assert all(s.call_count==0 for s in spies)
    assert attempted_writes==[]
    authorize(app,Role.FINOPS)
    h=(await client.get('/api/observability/finops/ledger-health')).json()
    assert h['admissions_failed'] is None and h['admission_scope']=='process_local_anoc_unavailable'


@pytest.mark.asyncio
async def test_privacy_and_bounded_unavailable(reader):
    app,client,run,*_=reader
    r=await client.get('/api/observability/runs/'+run.run_id)
    assert r.status_code==200 and r.json()['technical']['trace_id']=='1'*32
    prohibited=('prompt','response_body','authorization','command_text','sql','password','reasoning','teams_body','tool_arguments')
    assert not any(key in r.json() for key in prohibited)
    # Invalid user input must never return raw validation text or secrets.
    sentinel='Bearer SECRET_PRIVATE_PROMPT'
    response=await client.get('/api/observability/runs',params={'limit':sentinel})
    assert response.status_code==400 and sentinel not in response.text
    app.state.observability_service.settings=ReadConfiguration(read_deadline=.05)
    original=app.state.observability_service.repository.list_runs
    async def slow(*args,**kwargs):await asyncio.sleep(.3);return await original(*args,**kwargs)
    app.state.observability_service.repository.list_runs=slow
    assert (await client.get('/api/observability/runs')).status_code==503

@pytest.mark.asyncio
async def test_real_published_billing_and_archive_reconstruct_without_writer(reader,tmp_path,monkeypatch):
    app,client,run,prod,db,repo=reader
    from backend.tests._m11_financial import billing,catalog,WINDOW,mapping
    from backend.observability.finops.billing_sources import BillingSourceAdapter,PricingSourceAdapter
    from backend.observability.finops.billing_repository import Repository as BillingRepository
    from backend.observability.finops.billing_ingestion import Ingestion
    from backend.observability.read_service import bind_repositories
    billing_adapter=BillingSourceAdapter([billing('10')]);pricing_adapter=PricingSourceAdapter([catalog()])
    writer=Ingestion(BillingRepository(db.sessions),'development',[billing_adapter,pricing_adapter],mapping=mapping())
    try:
        assert await writer.ingest(billing_adapter.source,WINDOW)=='PUBLISHED'
        assert await writer.ingest(pricing_adapter.source,WINDOW)=='PUBLISHED'
    finally:await writer.close()
    service=app.state.observability_service
    service.settings=service.settings.model_copy(update={'billing_scope':billing_adapter.scope,'pricing_scope':pricing_adapter.scope})
    bind_repositories(service,app.state.read_database.sessions)
    forbidden=Mock(side_effect=AssertionError('GET invoked financial source job'))
    monkeypatch.setattr(Ingestion,'ingest',forbidden)
    monkeypatch.setattr(billing_adapter,'extract',forbidden)
    authorize(app,Role.FINOPS)
    import time
    began=time.perf_counter()
    response=await client.get('/api/observability/finops/billing-summary',params={'start':'2026-01-10','end':'2026-01-11'})
    print('OBSERVABILITY_PUBLISHED_FINOPS_GET_MS='+str((time.perf_counter()-began)*1000))
    assert response.status_code==200,response.text
    assert response.json()['amounts'][0]['net']=='10'
    assert response.json()['source']['mode']=='TEST_FIXTURE'
    assert not any(x in response.text for x in ('ACCOUNT_SENTINEL','PROJECT_SENTINEL','RESOURCE_SENTINEL','SECRET_SENTINEL','SELECT'))
    assert (await client.get('/api/observability/finops/pricing-status')).json()['earliest_pricing_as_of']
    assert forbidden.call_count==0
    from backend.tests.test_m15_archive import request,MARKERS,TID
    from backend.observability.archive import FilesystemArchive
    path=tmp_path/'archive';archive=FilesystemArchive(path,'development')
    commit,=archive.submit(request(environment='development').SerializeToString())
    # Use the independent app's read-only archive lifecycle, not a writer override.
    settings=service.settings.model_copy(update={'archive_root':path})
    independent=create_app(config=service.config,settings=settings,isolated_test=True)
    authorize(independent)
    before={p:p.read_bytes() for p in path.rglob('*') if p.is_file()}
    async with independent.router.lifespan_context(independent):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=independent),base_url='http://read') as c:
            res=await c.get('/api/observability/archive/'+TID,params={'digest':commit.digest})
            assert res.status_code==200,res.text
            assert len(res.json()['spans'])==2 and any(s.get('parent_span_id') for s in res.json()['spans'])
            assert not any(marker in res.text for marker in MARKERS)
    assert before=={p:p.read_bytes() for p in path.rglob('*') if p.is_file()}


@pytest.mark.asyncio
async def test_cancelled_archive_keeps_slot_until_filesystem_read_finishes(reader):
    import asyncio
    import threading
    from types import SimpleNamespace
    app,*_=reader
    service=app.state.observability_service
    started=threading.Event();release=threading.Event()
    def reconstruct(*_):
        started.set();release.wait(2);return b'bounded'
    archive=SimpleNamespace(reconstruct=reconstruct)
    task=asyncio.create_task(service.reconstruct_archive(archive,None,'1'*32,'a'*64))
    try:
        assert await asyncio.to_thread(started.wait,1)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):await task
        assert service.archive_reads.locked()
        with pytest.raises(TimeoutError):
            async with asyncio.timeout(.02):await service.archive_reads.acquire()
    finally:
        release.set()
    async with asyncio.timeout(1):
        await service.archive_reads.acquire()
        service.archive_reads.release()


@pytest.mark.asyncio
async def test_production_reader_uses_existing_safe_json_without_producer_startup():
    import io
    import logging
    stream=io.StringIO();handler=logging.StreamHandler(stream)
    logger=logging.getLogger('backend.observability.read_test')
    logger.addHandler(handler)
    try:
        app=create_app(config=config().model_copy(update={'otel_environment':'production'}),settings=ReadConfiguration())
        async with app.router.lifespan_context(app):
            assert app.state.observability_service.runtime is None
            logger.warning('PRIVATE_PROMPT %s','SECRET_TOKEN',extra={'authorization':'PRIVATE_BEARER'})
            record=json.loads(stream.getvalue().strip())
            assert record['environment']=='production'
            assert not any(value in stream.getvalue() for value in ('PRIVATE_', 'SECRET_TOKEN'))
    finally:
        logger.removeHandler(handler)


@pytest.mark.asyncio
async def test_access_never_promotes_anoc_dev_identity_or_browser_authority(reader):
    app,client,*_=reader
    app.dependency_overrides.pop(verified_principal_provider)
    response=await client.get('/api/observability/access',headers={
        'X-SLOPANOC-DEV-USER':'admin','X-Role':'Admin',
        'X-Capabilities':'financial_data,technical_diagnostics',
        'X-Environment':'production','Authorization':'Bearer unverified',
        'Cookie':'session=unverified'})
    assert response.status_code==401
    assert 'capabilities' not in response.json()


@pytest.mark.asyncio
async def test_access_reports_only_validated_capabilities_and_environment(reader):
    app,client,*_=reader
    from backend.observability.schemas import Permission
    for role in (Role.FINOPS,Role.DEVELOPER_SRE,Role.USER,Role.ADMIN):
        authorize(app,role)
        r=await client.get('/api/observability/access')
        assert r.status_code==200
        assert r.json()=={'verified':True,'capabilities':sorted(p.value for p in ROLE_PERMISSIONS[role]),
            'environments':['development']}
    authorize(app)
    assert (await client.get('/api/observability/access?environment=production')).status_code==403
    app.dependency_overrides[verified_principal_provider]=lambda:Principal(
        'reader-test',Role.USER,frozenset({Permission.FINANCIAL_DATA}),frozenset({'development'}),True)
    assert (await client.get('/api/observability/access')).status_code==401


@pytest.mark.asyncio
@pytest.mark.parametrize('role,capabilities,financial,technical',[
    ('Operator',['own_progress','operational_metadata'],403,False),
    ('Developer / SRE',['operational_metadata','technical_diagnostics'],403,True),
    ('FinOps',['financial_data'],200,False),
])
async def test_real_shared_local_adapter_access_and_capability_gates(reader,tmp_path,monkeypatch,role,capabilities,financial,technical):
    app,client,run,*_=reader
    app.dependency_overrides.pop(verified_principal_provider)
    file=tmp_path/'auth.json';file.write_text(json.dumps({'default_subject':'api-user','users':{
        'api-user':{'role':role,'capabilities':capabilities,'environments':['development']}}}))
    monkeypatch.setenv('SLOPANOC_AUTH_MODE','development')
    monkeypatch.setenv('SLOPANOC_DEV_AUTH_POLICY_FILE',str(file))
    access=await client.get('/api/observability/access',headers={'X-Role':'Admin','X-Environment':'production'})
    assert access.status_code==200
    assert access.json()=={'verified':True,'capabilities':sorted(capabilities),'environments':['development']}
    assert (await client.get('/api/observability/access?environment=production')).status_code==403
    assert (await client.get('/api/observability/access',headers={'X-SLOPANOC-DEV-USER':'unknown'})).status_code==401
    assert (await client.get('/api/observability/finops/runtime-usage')).status_code==financial
    detail=await client.get('/api/observability/runs/'+run.run_id)
    if 'operational_metadata' in capabilities:
        assert detail.status_code==200
        assert ('technical' in detail.json())==technical
    else:assert detail.status_code==403
    # A technical-only route must not leak data to Operator or FinOps identities.
    archive=await client.get('/api/observability/archive/'+'1'*32,params={'digest':'a'*64})
    assert archive.status_code==(503 if technical else 403)
