"""Synthetic fixture builders for M12; no live data or source clients."""
from datetime import timedelta
from types import SimpleNamespace
from uuid import uuid4
from copy import deepcopy
import pytest_asyncio
from backend.tests._m11_financial import JAN,WINDOW,billing,catalog,extraction,mapping as source_mapping
from backend.observability.finops.billing_contracts import digest
from backend.observability.finops.billing_normalization import normalize
from backend.observability.finops.reconciliation_inputs import billing_input,runtime_input,RuntimeProvider
from backend.observability.finops.reconciliation_mapping import Registry,Mapping
from backend.observability.finops.reconciliation_repository import Repository
from backend.observability.finops.reconciliation_models import ReconciliationBase
from backend.observability.finops.billing_models import BillingBase
from backend.observability.finops.models import FinOpsBase
from backend.observability.finops.billing_repository import Repository as SourceRepository
from backend.observability.finops.repository import Repository as RuntimeRepository
from backend.observability.finops.billing_sources import BillingSourceAdapter,PricingSourceAdapter
from backend.observability.finops.billing_ingestion import Ingestion
from backend.observability.finops.billing_service import FinancialSources
from backend.observability.finops.reconciliation_worker import Worker
from backend.observability.database import Database
from backend.observability.finops.contracts import Identity,Payload,Adjustment
from backend.observability.model_usage import ModelUsage

SCOPE=digest('fixture')
NOW=JAN+timedelta(hours=12)

def charge(cost='10',quantity='1000',**changes):
    return billing(cost,**dict(dict(usage=dict(amount=quantity,unit='tokens',amount_in_pricing_units=quantity,pricing_unit='tokens')),**changes))

def registry(**changes):
    m=Mapping(provider='gcp.gemini',service=digest('service-ai'),sku=digest('sku-input'),project=digest('PROJECT_SENTINEL'),
        model='gemini-2.5-flash',field='input_tokens',region='europe-west1',effective_from=JAN-timedelta(days=100),
        effective_to=JAN+timedelta(days=100),classification='DIRECT',evidence='DEDICATED_WORKLOAD')
    return Registry(environment='development',runtime_scope=SCOPE,mappings=(m,),**changes)

def inputs(rows=None,quantity=1000,**quality):
    ex=extraction(rows if rows is not None else [charge()]);p=normalize(ex,source_mapping())
    manifest=SimpleNamespace(fingerprint=p.fingerprint,row_count=p.rows,controls=list(p.controls),generation='a'*64,export_time=p.export_time)
    b=billing_input(ex,manifest,source_mapping(),SCOPE,'development')
    row=SimpleNamespace(event_id='base',payload_hash='hash',record_kind='BASE',original_event_id=None,
        metadata_safe=dict(identity=dict(provider='gcp.gemini',model='gemini-2.5-flash',operation_type='GENERATION'),region='europe-west1'))
    from backend.observability.finops.contracts import QUANTITIES
    for q in QUANTITIES:setattr(row,q,quantity if q=='input_tokens' else None)
    r=runtime_input([row],[SimpleNamespace(attempt_id='a',state='USAGE_CAPTURED')],'development',WINDOW)
    r['quality'].update(quality)
    return r,b,dict(prices=(),kind='PRICING',environment='development',period=WINDOW.partition,generations=[])

async def capture(repo,quantity=1000,**changes):
    i=Identity(environment='development',attempt_id=uuid4(),logical_call_id=uuid4(),provider_request_id=uuid4(),attempt=1,
        provider='gcp.gemini',model='gemini-2.5-flash',agent='team_manager',operation='orchestration',operation_type='GENERATION',
        workload='USER_TURN',started_at=JAN+timedelta(hours=1),deadline_at=NOW)
    await repo.admit(i,JAN-timedelta(days=1));await repo.start(i,i.started_at)
    p=Payload(identity=i,observed_at=NOW,provider_finished_at=NOW,usage=ModelUsage(input_tokens=quantity,
        availability='PARTIAL' if quantity is not None else 'UNKNOWN',source='provider'),status='COMPLETED',
        config_version='a'*16,region='europe-west1')
    await repo.capture(p);await repo.materialize(i.event_id)
    return i

@pytest_asyncio.fixture
async def system(tmp_path):
    db=Database('sqlite+aiosqlite:///'+str(tmp_path/'m12.db'),isolated_test=True)
    async with db.engine.begin() as c:
        for metadata in (FinOpsBase.metadata,BillingBase.metadata,ReconciliationBase.metadata):await c.run_sync(metadata.create_all)
    repo=Repository(db.sessions);runtime=RuntimeRepository(db.sessions);source=SourceRepository(db.sessions)
    a=BillingSourceAdapter([charge()]);p=PricingSourceAdapter([])
    ingestion=Ingestion(source,'development',[a,p],mapping=source_mapping())
    sources=FinancialSources(source,[a,p],worker=ingestion)
    worker=Worker(repo,RuntimeProvider(db.sessions),sources,registry())
    ingestion.reconciliation_consumer=worker.offer
    yield SimpleNamespace(db=db,repo=repo,runtime=runtime,source=source,a=a,p=p,ingestion=ingestion,sources=sources,worker=worker)
    await ingestion.close();await worker.close();await db.close()

async def seed(system):
    await capture(system.runtime)
    assert await system.ingestion.ingest('DETAILED_BILLING',WINDOW)=='PUBLISHED'
    await system.worker.drain()
    assert await system.worker.run_period(WINDOW,NOW)=='PUBLISHED'
    return (await system.repo.current('development',SCOPE,WINDOW.partition,'2026-01-11'))[0]
