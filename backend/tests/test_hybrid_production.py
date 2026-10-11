"""Production boundaries with disposable SQL and mocked Google clients only."""
import base64
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from datetime import timedelta
from uuid import uuid4
import httpx
import pytest
from sqlalchemy import select
from backend.observability.hybrid.config import HybridConfig
from backend.observability.hybrid.google_transport import GooglePubSubAuditTransport
from backend.observability.hybrid.push_app import create_app, GooglePushVerifier, PushAuthenticationUnavailable
from backend.observability.hybrid.production import capture_for
from backend.observability.hybrid.capture import Capture, make_event
from backend.observability.hybrid.models import AuditRow, OutboxRow, ReceiptRow
from backend.observability.hybrid.workers import Publisher, Projector
from backend.observability.hybrid.store import OutboxStore, ProjectorStore
from backend.observability.config import ObservabilityConfig
from backend.observability.projection import utcnow
from backend.tests.test_hybrid_phase1_runtime import pipeline, count, rows
from backend.tests._m7_storage import state, config

PROJECT='pr-msn-dev-gl-slopai-01'
TOPIC=f'projects/{PROJECT}/topics/slopanoc-development-audit-events-v1'
SA=f'slopanoc-development-push@{PROJECT}.iam.gserviceaccount.com'

def cfg(**kw):
    return HybridConfig(environment='development',project=PROJECT,audit_topic=TOPIC,
        push_audience='https://projector.example.run.app',push_service_account=SA,**kw)

def audit():
    run=str(uuid4())
    return make_event('run.accepted','development','run',run,1,utcnow(),run_id=run,status='RUNNING')

def wrapped(e):
    return {'subscription':f'projects/{PROJECT}/subscriptions/slopanoc-development-audit-projector-v1',
        'message':{'messageId':'12345','data':base64.b64encode(e.model_dump_json().encode()).decode(),
            'attributes':{'event_id':str(e.event_id),'schema_version':'1','event_type':e.event_type,
                'environment':e.environment,'run_id':str(e.run_id)}}}

@pytest.mark.parametrize('enabled',[False,True])
@pytest.mark.asyncio
async def test_actual_anoc_lifespan_capture_composition(monkeypatch,pipeline,enabled):
    from backend.api import app as module
    from backend.config.settings import Settings
    from backend.observability import database as dbmodule
    from backend.cases import progression_store
    db,p,_=pipeline
    monkeypatch.setenv('SLOPANOC_HYBRID_AUDIT_ENABLED',str(enabled).lower())
    monkeypatch.setenv('SLOPANOC_HYBRID_ENVIRONMENT','development')
    settings=Settings({'SLOPANOC_PROJECTION_ENABLED':'true','SLOPANOC_FINOPS_ENABLED':'true',
        'SLOPANOC_OTEL_ENVIRONMENT':'development'})
    monkeypatch.setattr(module,'get_settings',lambda:settings)
    # Dedicated test engines; real lifespan creates its repositories/coordinator/ledger.
    class Binding:
        def __init__(self,url):self.sessions=db.sessions
        async def close(self):pass
    monkeypatch.setattr(dbmodule,'Database',Binding)
    store=SimpleNamespace(audit_capture=None,audit_environment=None)
    monkeypatch.setattr(progression_store,'get_case_progression_store',lambda:store)
    monkeypatch.setattr('backend.authentication.validate_identity_configuration',lambda _:None)
    app=module.create_app()
    async with app.router.lifespan_context(app):
        service=app.state.observability_service
        assert (service.repository.audit_capture is not None)==enabled
        assert (service.accounting.repository.audit_capture is not None)==enabled
        assert (store.audit_capture is not None)==enabled
        await service.repository.persist(state())
        assert await count(db,OutboxRow)==int(enabled)
    assert store.audit_capture is None

@pytest.mark.parametrize('values',[{'SLOPANOC_HYBRID_AUDIT_ENABLED':'yes'},
    {'SLOPANOC_HYBRID_PUBLISHER_ENABLED':'true'},
    {'SLOPANOC_HYBRID_PROJECT':'other-project','SLOPANOC_HYBRID_AUDIT_TOPIC':TOPIC}])
def test_config_fail_closed(values):
    with pytest.raises(ValueError):HybridConfig.from_environment(values)

def test_capture_requires_existing_owners_and_environment():
    assert capture_for(HybridConfig(),ObservabilityConfig()) is None
    with pytest.raises(ValueError):capture_for(cfg(capture_enabled=True),ObservabilityConfig())

@pytest.mark.asyncio
async def test_google_adapter_lazy_safe_message_and_capacity():
    client=SimpleNamespace(publish=AsyncMock(),transport=SimpleNamespace(close=AsyncMock()))
    factory=Mock(return_value=client)
    transport=GooglePubSubAuditTransport(cfg(audit_publication_enabled=True),client_factory=factory)
    factory.assert_not_called()
    e=audit();await transport.publish(e.model_dump_json().encode())
    args=client.publish.call_args.kwargs
    assert args['request']['topic']==TOPIC
    message=args['request']['messages'][0]
    assert json.loads(message['data'])['event_id']==str(e.event_id)
    assert message['attributes']==wrapped(e)['message']['attributes']
    with pytest.raises(ValueError):await transport.publish(b'{"prompt":"SECRET"}')
    assert client.publish.await_count==1
    await transport.close();client.transport.close.assert_awaited_once()
    with pytest.raises(RuntimeError):await transport.publish(e.model_dump_json().encode())

@pytest.mark.asyncio
async def test_publish_timeout_bounded():
    import asyncio
    client=SimpleNamespace(publish=AsyncMock(side_effect=lambda **kw:None))
    async def slow(**kw):await asyncio.sleep(5)
    client.publish=slow
    transport=GooglePubSubAuditTransport(cfg(audit_publication_enabled=True,publish_timeout_seconds=1),client_factory=lambda:client)
    with pytest.raises(TimeoutError):await transport.publish(audit().model_dump_json().encode())

@pytest.mark.asyncio
async def test_push_commit_duplicate_conflict_and_auth(pipeline):
    db,p,_=pipeline
    verifier=SimpleNamespace(verify=AsyncMock(return_value=True))
    app=create_app(config=cfg(projector_enabled=True),projector=p.projector,
        test_verifier=verifier,isolated_test=True)
    e=audit();body=wrapped(e)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://test') as client:
        assert (await client.post('/internal/observability/events/audit',json=body)).status_code==200
        assert (await client.post('/internal/observability/events/audit',json=body)).json()['state']=='DUPLICATE'
        changed=e.model_copy(update={'timestamp':e.timestamp+timedelta(seconds=1)})
        assert (await client.post('/internal/observability/events/audit',json=wrapped(changed))).status_code==409
        verifier.verify.return_value=False
        assert (await client.post('/internal/observability/events/audit',json=body,
            headers={'x-user-email':SA,'x-role':'Admin'})).status_code==401
    assert await count(db,ReceiptRow)==1

@pytest.mark.parametrize('mutation',['base64','attribute','subscription','secret','oversize','type'])
@pytest.mark.asyncio
async def test_push_rejects_permanent_input(pipeline,mutation):
    db,p,_=pipeline
    app=create_app(config=cfg(projector_enabled=True),projector=p.projector,
        test_verifier=SimpleNamespace(verify=AsyncMock(return_value=True)),isolated_test=True)
    body=wrapped(audit());headers={}
    if mutation=='base64':body['message']['data']='@@@@'
    if mutation=='attribute':body['message']['attributes']['event_id']=str(uuid4())
    if mutation=='subscription':body['subscription']='projects/foreign/subscriptions/no'
    if mutation=='secret':body['message']['attributes']['prompt']='FORBIDDEN'
    if mutation=='oversize':body['message']['data']='A'*40000
    if mutation=='type':headers={'content-type':'text/plain'}
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://test') as client:
        response=await client.post('/internal/observability/events/audit',json=body,headers=headers)
        assert response.status_code in (400,413,415)
    assert await count(db,ReceiptRow)==0

@pytest.mark.parametrize('claims',[{'email':SA,'email_verified':True},
    {'email':'attacker@example.com','email_verified':True},{'email':SA,'email_verified':False}])
@pytest.mark.asyncio
async def test_google_token_signature_audience_identity(monkeypatch,claims):
    from google.oauth2 import id_token
    verify=Mock(return_value=claims)
    monkeypatch.setattr(id_token,'verify_oauth2_token',verify)
    verifier=GooglePushVerifier(cfg(projector_enabled=True))
    assert await verifier.verify('Bearer test-jwt')==(claims=={'email':SA,'email_verified':True})
    assert verify.call_args.kwargs['audience']=='https://projector.example.run.app'
    assert not await verifier.verify(None)
    verify.side_effect=ValueError('invalid signature')
    assert not await verifier.verify('Bearer bad')

@pytest.mark.asyncio
async def test_committed_owner_google_outage_then_recovery(pipeline):
    db,p,_=pipeline;run=state();await p.run_repository.persist(run)
    client=SimpleNamespace(publish=AsyncMock(side_effect=RuntimeError('offline')))
    configuration=cfg(audit_publication_enabled=True)
    transport=GooglePubSubAuditTransport(configuration,client_factory=lambda:client)
    publisher=Publisher(OutboxStore(db.sessions,configuration),transport)
    now=utcnow();assert await publisher.tick(now)==0
    assert (await rows(db,OutboxRow))[0].publication_state=='RETRYING'
    assert await count(db,AuditRow)==1
    client.publish.side_effect=None
    assert await publisher.tick(now+timedelta(seconds=2))==1
    assert (await rows(db,OutboxRow))[0].publication_state=='PUBLISHED'

@pytest.mark.asyncio
async def test_projector_outage_broker_model_eventual_dlq(pipeline,monkeypatch):
    db,p,_=pipeline;run=state();await p.run_repository.persist(run)
    app=create_app(config=cfg(projector_enabled=True),projector=p.projector,
        test_verifier=SimpleNamespace(verify=AsyncMock(return_value=True)),isolated_test=True)
    monkeypatch.setattr(p.projector,'consume',AsyncMock(return_value='RETRY'))
    deliveries=0;dlq=[]
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://test') as client:
        body=wrapped(audit())
        for _ in range(5):
            response=await client.post('/internal/observability/events/audit',json=body)
            assert response.status_code==503;deliveries+=1
        dlq.append('bounded_mock_policy')
    assert deliveries==5 and len(dlq)==1 and await count(db,AuditRow)==1
    # Actual Google best-effort forwarding is deployment validation, not this mock.

@pytest.mark.asyncio
async def test_case_progression_capture_same_transaction(tmp_path):
    from backend.cases.db import CaseDatabase
    from backend.cases.service import CaseService
    from backend.cases.progression_store import CaseProgressionStore
    from backend.cases.troubleshooting_progression import TroubleshootingProgression
    from backend.observability.hybrid.models import HybridBase
    db=CaseDatabase('sqlite+aiosqlite:///'+str(tmp_path/'case.sqlite'))
    try:
        case=await CaseService(db).create_case('test','Synthetic','Synthetic')
        async with db._engine.begin() as connection:await connection.run_sync(HybridBase.metadata.create_all)
        store=CaseProgressionStore(db,audit_capture=Capture(),audit_environment='development')
        assert await store.save(case.case_id,TroubleshootingProgression(),None,'session-test')==1
        async with db.session() as s:
            row=await s.scalar(select(AuditRow))
            assert row.event_type=='governance.progression.recorded' and row.safe_payload['session_id']=='session-test'
        class Fail(Capture):
            async def append(self,s,e):await super().append(s,e);raise RuntimeError('rollback')
        store.audit_capture=Fail()
        with pytest.raises(RuntimeError):await store.save(case.case_id,TroubleshootingProgression(),1,'session-test')
        assert (await store.load(case.case_id)).version==1
        async with db.session() as s:assert len((await s.scalars(select(AuditRow))).all())==1
    finally:await db.close()


@pytest.mark.asyncio
async def test_disabled_worker_factories_initialize_nothing(monkeypatch):
    from backend.observability.hybrid import production
    monkeypatch.setattr(production.HybridConfig,'from_environment',lambda:HybridConfig())
    forbidden=Mock(side_effect=AssertionError('disabled initialized resource'))
    monkeypatch.setattr(production,'Database',forbidden)
    monkeypatch.setattr(production,'GooglePubSubAuditTransport',forbidden)
    for factory in (production.create_publisher_app,production.create_projector_app):
        app=factory()
        async with app.router.lifespan_context(app):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://test') as client:
                assert (await client.get('/health')).json()['state']=='disabled'
    forbidden.assert_not_called()


def test_production_rejects_test_auth_adapter():
    production_config=HybridConfig(environment='production',project=PROJECT,
        audit_topic=f'projects/{PROJECT}/topics/slopanoc-production-audit-events-v1',
        projector_enabled=True)
    with pytest.raises(ValueError):create_app(config=production_config,test_verifier=object(),isolated_test=True)

@pytest.mark.asyncio
async def test_optimized_capture_conflict_still_atomic(pipeline):
    db,p,_=pipeline;e=audit()
    async with db.sessions() as s,s.begin():assert await Capture().append(s,e)
    async with db.sessions() as s,s.begin():assert not await Capture().append(s,e)
    with pytest.raises(ValueError):
        async with db.sessions() as s,s.begin():
            await Capture().append(s,e.model_copy(update={'timestamp':e.timestamp+timedelta(seconds=1)}))
    assert await count(db,AuditRow)==await count(db,OutboxRow)==1

@pytest.mark.asyncio
async def test_push_environment_and_privacy_categories(pipeline):
    db,p,_=pipeline
    app=create_app(config=cfg(projector_enabled=True),projector=p.projector,
        test_verifier=SimpleNamespace(verify=AsyncMock(return_value=True)),isolated_test=True)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://test') as client:
        e=audit();foreign=e.model_copy(update={'environment':'staging'})
        response=await client.post('/internal/observability/events/audit',json=wrapped(foreign))
        assert response.json()['state']=='ENVIRONMENT_MISMATCH'
        body=wrapped(e);raw=e.model_dump(mode='json');raw['payload_safe']['prompt']='FORBIDDEN'
        body['message']['data']=base64.b64encode(json.dumps(raw).encode()).decode()
        assert (await client.post('/internal/observability/events/audit',json=body)).json()['state']=='PERMANENT_INVALID'
    assert await count(db,ReceiptRow)==0


def test_review_sql_names_and_non_destructive_rollout():
    import re
    from pathlib import Path
    from backend.observability.models import Base
    from backend.observability.hybrid.models import HybridBase
    from backend.observability.finops.models import FinOpsBase
    from backend.observability import policy_models  # register shared Base tables
    from backend.observability.finops.billing_models import BillingBase
    from backend.observability.finops.reconciliation_models import ReconciliationBase
    from backend.observability.finops.unit_economics_models import UnitEconomicsBase
    from backend.observability.finops.control_models import ControlBase
    from backend.observability import slo_rollups  # register shared Base tables
    root=Path('docs/Telemetry/validation')
    roles=(root/'HYBRID_PHASE1_DATABASE_ROLES.sql').read_text()
    known=set().union(*(b.metadata.tables for b in (Base,HybridBase,FinOpsBase,BillingBase,ReconciliationBase,UnitEconomicsBase,ControlBase)))
    assert set(re.findall(r'public\.([a-z_]+)',roles))<=known
    sql=(root/'HYBRID_PHASE1_PRODUCTION_ROLLOUT.sql').read_text()
    assert not re.search(r'^(DROP|ALTER|TRUNCATE|RENAME)\b',sql,re.M)
    assert all('alembic_version' in line for line in sql.splitlines() if line.startswith(('DELETE','UPDATE')))
    assert sql.count('CREATE TABLE observability_critical_audit')==1
    assert 'slopanoc_case_troubleshooting_progressions' in sql and "version_num='d26a1b7c9e40'" in sql

@pytest.mark.asyncio
async def test_auth_cancellation_retains_capacity_until_verification_finishes(monkeypatch):
    import asyncio
    import threading
    from google.oauth2 import id_token
    entered=threading.Event();release=threading.Event()
    def verify(*args,**kwargs):
        entered.set();release.wait(2)
        return {'email':SA,'email_verified':True}
    monkeypatch.setattr(id_token,'verify_oauth2_token',verify)
    verifier=GooglePushVerifier(cfg(projector_enabled=True,projector_max_inflight=1))
    task=asyncio.create_task(verifier.verify('Bearer token'))
    try:
        assert await asyncio.to_thread(entered.wait,1)
        task.cancel();await asyncio.gather(task,return_exceptions=True)
        with pytest.raises(PushAuthenticationUnavailable):await verifier.verify('Bearer another')
        assert len(verifier.active)==1 and verifier.slots.locked()
    finally:
        release.set()
        await asyncio.gather(*tuple(verifier.active),return_exceptions=True)
    assert not verifier.slots.locked()


@pytest.mark.asyncio
async def test_auth_certificate_outage_is_retryable(pipeline,monkeypatch):
    from google.oauth2 import id_token
    from google.auth.exceptions import TransportError
    db,p,_=pipeline
    monkeypatch.setattr(id_token,'verify_oauth2_token',Mock(side_effect=TransportError('offline')))
    app=create_app(config=cfg(projector_enabled=True),projector=p.projector,isolated_test=True)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://test') as client:
        response=await client.post('/internal/observability/events/audit',json=wrapped(audit()),headers={'authorization':'Bearer test'})
    assert response.status_code==503 and response.json()['state']=='TRANSIENT_FAILURE'
    assert await count(db,ReceiptRow)==0

@pytest.mark.parametrize('workload',['publisher','projector'])
@pytest.mark.asyncio
async def test_enabled_worker_factory_uses_configured_production_components(pipeline,monkeypatch,workload):
    import asyncio
    from backend.observability.hybrid import production
    from google.pubsub_v1.services import publisher as google_publisher
    from google.oauth2 import id_token
    db,p,_=pipeline
    configuration=cfg(audit_publication_enabled=workload=='publisher',projector_enabled=workload=='projector')
    monkeypatch.setattr(production.HybridConfig,'from_environment',lambda:configuration)
    monkeypatch.setenv('SLOPANOC_HYBRID_DATABASE_URL','postgresql+asyncpg://unused@127.0.0.1/unused')
    close=AsyncMock()
    class Binding:
        def __init__(self,url):self.sessions=db.sessions
        async def close(self):await close()
    monkeypatch.setattr(production,'Database',Binding)
    client=SimpleNamespace(publish=AsyncMock(),transport=SimpleNamespace(close=AsyncMock()))
    factory=Mock(return_value=client)
    monkeypatch.setattr(google_publisher,'PublisherAsyncClient',factory)
    monkeypatch.setattr(id_token,'verify_oauth2_token',Mock(return_value={'email':SA,'email_verified':True}))
    if workload=='publisher':
        await p.run_repository.persist(state())
        app=production.create_publisher_app()
        async with app.router.lifespan_context(app):
            assert isinstance(app.state.publisher,Publisher)
            for _ in range(100):
                if (await rows(db,OutboxRow))[0].publication_state=='PUBLISHED':break
                await asyncio.sleep(.01)
            assert (await rows(db,OutboxRow))[0].publication_state=='PUBLISHED'
        client.publish.assert_awaited_once();client.transport.close.assert_awaited_once()
    else:
        app=production.create_projector_app()
        async with app.router.lifespan_context(app):
            assert isinstance(app.state.projector,Projector)
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://test') as http:
                response=await http.post('/internal/observability/events/audit',json=wrapped(audit()),headers={'authorization':'Bearer signed-mock'})
                assert response.status_code==200,response.text
        factory.assert_not_called()
    close.assert_awaited_once()
