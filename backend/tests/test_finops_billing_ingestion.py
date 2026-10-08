import asyncio
from decimal import Decimal
from dataclasses import replace
from datetime import timedelta
import pytest
import pytest_asyncio
from sqlalchemy import select,func
from backend.tests._m11_financial import *
from backend.tests.test_finops_runtime_ledger import accounting
from backend.observability.database import Database
from backend.observability.finops.billing_models import BillingBase,PublicationRow,StateRow
from backend.observability.finops.billing_repository import Repository
from backend.observability.finops.billing_sources import BillingSourceAdapter,PricingSourceAdapter,FocusSourceAdapter
from backend.observability.finops.billing_ingestion import Ingestion
from backend.observability.finops.billing_service import FinancialSources
from backend.observability.finops.billing_normalization import normalize
from backend.observability.finops.billing_contracts import Policy

@pytest_asyncio.fixture
async def financial(tmp_path):
    db=Database('sqlite+aiosqlite:///'+str(tmp_path/'billing.db'),isolated_test=True)
    async with db.engine.begin() as c:await c.run_sync(BillingBase.metadata.create_all)
    repo=Repository(db.sessions);adapter=BillingSourceAdapter([billing()]);pricing=PricingSourceAdapter([catalog()]);focus=FocusSourceAdapter()
    worker=Ingestion(repo,'development',[adapter,pricing,focus],mapping=mapping())
    yield db,repo,adapter,pricing,worker,FinancialSources(repo,[adapter,pricing,focus],worker=worker)
    await worker.close();await db.close()

@pytest.mark.asyncio
async def test_replay_corrective_lines_manifest_history_and_reopen(financial):
    db,repo,a,p,w,s=financial
    assert await w.ingest(a.source,WINDOW)=='PUBLISHED'
    first=(await repo.current('development',a.source,a.scope))[0]
    assert await w.ingest(a.source,WINDOW)=='IDENTICAL'
    assert len(await repo.history(first[0].key))==1
    a.rows=(billing(),billing('-10','202602',cost_type='adjustment'),billing('5','202602',export_time='2026-02-15T00:00:00Z'))
    assert await w.ingest(a.source,WINDOW)=='PUBLISHED'
    assert len(await repo.history(first[0].key))==2
    await db.close()
    reopened=Repository(db.sessions)
    rows=await reopened.current('development',a.source,a.scope)
    assert rows[0][1].row_count==3 and rows[0][1].late_rows==1
    assert sum(Decimal(g['net']) for g in rows[0][2].summaries)==5
    assert first[1].controls[0]['net']=='10'

@pytest.mark.asyncio
async def test_partial_schema_failure_preserves_previous_and_independent_health(financial):
    db,repo,a,p,w,s=financial
    await w.ingest(a.source,WINDOW);await w.ingest(p.source,WINDOW)
    generation=(await repo.current('development',a.source,a.scope))[0][0].generation
    async def partial(window):return extraction([billing('999')],complete=False)
    a.extract=partial
    assert await w.ingest(a.source,WINDOW)=='FAILED'
    rows=await repo.current('development',a.source,a.scope)
    assert rows[0][0].generation==generation and rows[0][2].summaries[0]['net']=='10'
    assert s.health(a.source,rows,JAN+timedelta(hours=3)).state=='STALE'
    assert s.health(p.source,await repo.current('development',p.source,p.scope),JAN+timedelta(hours=3)).state=='FRESH'
    async def broken(window):return extraction([{'cost':'1'}])
    a.extract=broken;assert await w.ingest(a.source,WINDOW)=='FAILED'
    assert s.health(a.source,await repo.current('development',a.source,a.scope),NOW).reason=='SCHEMA_INCOMPATIBLE'

@pytest.mark.asyncio
async def test_fencing_concurrent_publish_and_failed_transaction(financial,monkeypatch):
    db,repo,a,p,w,s=financial
    key,old=await repo.reserve('development',a,WINDOW)
    _,new=await repo.reserve('development',a,WINDOW)
    ex=extraction([billing()]);projection=normalize(ex,mapping())
    assert await repo.publish(key,old,ex,projection)=='SUPERSEDED'
    assert await repo.publish(key,new,ex,projection)=='PUBLISHED'
    # Actual concurrent duplicate workers converge through durable monotonic tokens.
    results=await asyncio.gather(*(w.ingest(a.source,WINDOW) for _ in range(10)))
    assert 'FAILED' not in results and len(await repo.history(key))==1
    async def failure(*args):raise RuntimeError('SECRET_SENTINEL SQL SELECT')
    monkeypatch.setattr(repo,'publish',failure)
    a.rows=(billing('999'),)
    assert await w.ingest(a.source,WINDOW)=='FAILED'
    assert (await repo.current('development',a.source,a.scope))[0][2].summaries[0]['net']=='10'

@pytest.mark.asyncio
async def test_cycle_automatic_fixture_source_and_late_overlap_survive_restart(financial,monkeypatch):
    db,repo,a,p,w,s=financial
    # Short isolated retention fixture; delayed row added after first publication.
    w.policy=Policy(retained_days=3,lookback_days=2)
    import backend.observability.finops.billing_repository as persistence
    monkeypatch.setattr(persistence,'now',lambda:JAN+timedelta(hours=12))
    await w.cycle(JAN+timedelta(hours=12),max_partitions=2)
    a.rows+=(billing('5',invoice='202602',export_time='2026-02-15T00:00:00Z'),)
    monkeypatch.setattr(persistence,'now',lambda:JAN+timedelta(days=1,hours=12))
    await w.cycle(JAN+timedelta(days=1,hours=12),max_partitions=4)
    rows=await repo.current('development',a.source,a.scope)
    jan=next(c for _,pub,c in rows if pub.window_start==JAN)
    assert sum(Decimal(g['net']) for g in jan.summaries)==15
    assert w.metrics.counts[a.source,'late_rows']>0
    await db.close()
    assert (await Repository(db.sessions).current('development',a.source,a.scope))

@pytest.mark.asyncio
async def test_outage_stale_and_empty_not_healthy(financial):
    db,repo,a,p,w,s=financial
    await w.ingest(a.source,WINDOW)
    rows=await repo.current('development',a.source,a.scope)
    assert s.health(a.source,rows,JAN+timedelta(hours=3)).state=='FRESH'
    assert s.health(a.source,rows,JAN+timedelta(days=2)).state=='DELAYED'
    assert s.health(a.source,rows,NOW).state=='STALE'
    a.fail=True;assert await w.ingest(a.source,WINDOW)=='FAILED'
    assert s.health(a.source,await repo.current('development',a.source,a.scope),JAN+timedelta(hours=3)).state=='STALE'
    assert s.health('FOCUS',[],NOW).state=='UNAVAILABLE'
    assert FinancialSources().health(a.source,[],NOW).state=='NOT_CONFIGURED'

@pytest.mark.asyncio
async def test_fresh_export_cannot_make_old_pricing_snapshot_fresh(financial):
    db,repo,a,p,w,s=financial
    p.rows=(catalog(export_time=NOW.isoformat()),)
    await w.ingest(p.source,WINDOW)
    h=s.health(p.source,await repo.current('development',p.source,p.scope),NOW)
    assert h.state=='STALE' and h.as_of==JAN and h.last_export==NOW

@pytest.mark.asyncio
async def test_price_history_survives_two_published_daily_snapshots(financial):
    from backend.observability.finops.pricing import lookup
    db,repo,a,p,w,s=financial
    p.rows=(catalog(),catalog('200',JAN+timedelta(days=1)))
    await w.ingest(p.source,WINDOW)
    await w.ingest(p.source,Window(JAN+timedelta(days=1),JAN+timedelta(days=2)))
    all_prices=tuple(x for _,_,c in await repo.current('development',p.source,p.scope) for x in c.prices)
    assert lookup(all_prices,'sku-input',JAN)['price']['tiers'][0]['account_price']=='100'
    assert lookup(all_prices,'sku-input',JAN-timedelta(days=1))['state']=='PRICE_UNAVAILABLE'

@pytest.mark.asyncio
async def test_price_and_focus_supplement_do_not_modify_runtime_ledger(financial,accounting):
    from backend.tests.test_finops_runtime_ledger import identity,payload,started
    from backend.observability.finops.models import LedgerRow
    from backend.observability.finops.billing_sources import FocusSourceAdapter
    db,repo,a,p,w,s=financial
    _,runtime_repo,ledger=accounting
    i=identity();await started(runtime_repo,i);await runtime_repo.capture(payload(i));await runtime_repo.materialize(i.event_id)
    async with runtime_repo.sessions() as session:
        before=(await session.get(LedgerRow,i.event_id)).metadata_safe
    await w.ingest(a.source,WINDOW);await w.ingest(p.source,WINDOW)
    a.fail=True;await w.ingest(a.source,WINDOW)
    async with runtime_repo.sessions() as session:
        row=await session.get(LedgerRow,i.event_id)
        assert row.metadata_safe==before and row.input_tokens==100
