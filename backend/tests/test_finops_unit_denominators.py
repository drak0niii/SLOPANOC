from datetime import timedelta
from decimal import Decimal
from uuid import uuid4
import pytest
from sqlalchemy import delete,select
from backend.tests._m13_unit_economics import *
from backend.observability.models import Base
from backend.observability.slo_rollups import Receipt,SREReceiptRow,SRESourceRow,Rollups
from backend.observability.finops.unit_denominators import TurnProvider,ModelProvider,turn_counts,utcnow
from backend.observability.finops.unit_economics_models import UnitEconomicsBase,SnapshotRow
from backend.observability.finops.unit_economics_repository import Repository,turn_scope
from backend.observability.finops.unit_economics_contracts import Policy

def receipt(**changes):
    return Receipt(run_id=str(uuid4()),environment='development',subject_digest='a'*64,accepted_at=JAN+timedelta(hours=1),
        deadline_at=JAN+timedelta(hours=2),terminal_at=JAN+timedelta(hours=1,minutes=1),terminal_status='COMPLETED',
        terminal_count=1,valid_outcome=True,request_class='GENERAL',**changes)

@pytest.mark.asyncio
async def test_daily_snapshot_uses_canonical_m9_then_survives_receipt_expiry(system):
    db=system.db;now=JAN+timedelta(days=1,minutes=20)
    async with db.engine.begin() as c:
        await c.run_sync(Base.metadata.create_all);await c.run_sync(UnitEconomicsBase.metadata.create_all)
    async with db.sessions() as s,s.begin():
        s.add(SRESourceRow(environment='development',coverage_start=JAN-timedelta(days=1),updated_at=now,capture_complete=True))
    rollups=Rollups(db.sessions);r=receipt();await rollups.save(r,now)
    p=TurnProvider(db.sessions,'development',600,Policy(business_population_proven=True))
    snap=await p.snapshot(WINDOW,now);assert snap.complete and snap.counts['ACCEPTED_TURN']==1
    repo=Repository(db.sessions);key,rev=await repo.reserve('development',turn_scope('development'),WINDOW.partition,'M9')
    assert await repo.save_snapshot(key,rev,snap)=='PUBLISHED'
    await rollups.prune(now+timedelta(days=4))
    async with db.sessions() as s:assert not (await s.scalars(select(SREReceiptRow))).all()
    _,retained=await repo.snapshot('development',turn_scope('development'),WINDOW.partition,'M9')
    assert retained==snap and retained.counts['AVAILABLE_TURN']==1
    # The frozen snapshot can still price a compatible late billing day after
    # all operational receipts are gone; no source receipt reconstruction.
    from backend.observability.finops.unit_economics import calculate
    data=economics_inputs();data['snapshots'][0]=retained
    assert Decimal(find(calculate(**data))['direct_cost_per_unit'])==60
    missing=await p.snapshot(WINDOW,now+timedelta(days=4))
    assert not missing.complete and missing.counts['ACCEPTED_TURN'] is None

def test_cross_midnight_acceptance_and_success_vs_terminal_cancel_semantics():
    r=receipt().model_copy(update={'accepted_at':WINDOW.end-timedelta(seconds=10),'terminal_at':WINDOW.end+timedelta(seconds=5),'deadline_at':WINDOW.end+timedelta(seconds=100)})
    failed=r.model_copy(update={'run_id':str(uuid4()),'terminal_status':'FAILED','valid_outcome':False})
    counts,_=turn_counts([r,failed],WINDOW.end+timedelta(minutes=5))
    assert counts['ACCEPTED_TURN']==2 and counts['AVAILABLE_TURN']==1 and counts['TIMELY_TERMINAL_TURN']==2
    cancel=r.model_copy(update={'terminal_status':'CANCELLED','valid_outcome':False,'cancel_origin':'USER_OR_CLIENT_CANCEL'})
    counts,_=turn_counts([cancel],WINDOW.end+timedelta(minutes=5))
    assert counts['AVAILABLE_TURN']==0 and counts['TIMELY_TERMINAL_TURN']==1

@pytest.mark.asyncio
async def test_missing_source_gap_early_capture_and_no_business_scope_assumption(system):
    async with system.db.engine.begin() as c:await c.run_sync(Base.metadata.create_all)
    snap=await TurnProvider(system.db.sessions,'development',600).snapshot(WINDOW,NOW)
    assert not snap.complete and not snap.business_population_proven and snap.freshness=='UNAVAILABLE'

@pytest.mark.asyncio
async def test_m10_snapshot_reproduces_exact_financial_evidence_and_freezes_attempts(system):
    await seed(system)
    row=await Repository(system.db.sessions).financial('development',SCOPE,WINDOW.partition)
    snap=await ModelProvider(system.db.sessions,'development').snapshot(WINDOW,row['runtime'],utcnow())
    assert snap.complete and snap.window_aligned and snap.counts['PROVIDER_ATTEMPT']==1 and snap.counts['MODEL_OPERATION']==1
    assert snap.runtime_digest==row['runtime']['ledger_digest']
    await capture(system.runtime)
    newer=await ModelProvider(system.db.sessions,'development').snapshot(WINDOW,row['runtime'],utcnow())
    assert not newer.window_aligned and newer.runtime_digest is None

@pytest.mark.asyncio
async def test_delayed_accounting_denominator_stays_start_day_price_alignment_unproven(system):
    from backend.observability.finops.models import LedgerRow
    await capture(system.runtime)
    # Test fixture moves only the source clock to reproduce delayed capture; production adapter is read-only.
    async with system.db.sessions() as s,s.begin():
        row=(await s.scalars(select(LedgerRow))).one();row.observed_at=WINDOW.end+timedelta(hours=1)
    snap=await ModelProvider(system.db.sessions,'development').snapshot(WINDOW,now=utcnow())
    assert snap.counts['PROVIDER_ATTEMPT']==1 and not snap.window_aligned

@pytest.mark.asyncio
async def test_corrected_snapshot_is_append_only_and_stale_writer_fenced(system):
    async with system.db.engine.begin() as c:await c.run_sync(UnitEconomicsBase.metadata.create_all)
    repo=Repository(system.db.sessions);a=economics_inputs()['snapshots'][0]
    key,rev=await repo.reserve('development',turn_scope('development'),WINDOW.partition,'M9')
    await repo.save_snapshot(key,rev,a)
    key,old=await repo.reserve('development',turn_scope('development'),WINDOW.partition,'M9')
    _,new=await repo.reserve('development',turn_scope('development'),WINDOW.partition,'M9')
    b=a.model_copy(update={'source_digest':'f'*64,'counts':dict(a.counts,ACCEPTED_TURN=11)})
    assert await repo.save_snapshot(key,new,b)=='PUBLISHED'
    assert await repo.save_snapshot(key,old,a)=='SUPERSEDED'
    async with system.db.sessions() as s:assert len((await s.scalars(select(SnapshotRow))).all())==2

def test_safe_acceptance_cohort_operational_context_uses_canonical_outcomes():
    from backend.observability.finops.unit_denominators import turn_contexts
    good=receipt().model_copy(update={'duration_ms':100})
    failed=good.model_copy(update={'run_id':str(uuid4()),'valid_outcome':False,'terminal_status':'FAILED','duration_ms':300})
    context=turn_contexts([good,failed],WINDOW.end)['GENERAL_TURN']
    assert Decimal(context.availability_ratio)==Decimal('0.5') and Decimal(context.timely_terminal_ratio)==1
    assert Decimal(context.mean_latency_ms)==200 and context.latency_observed==2
    assert context.source=='M9_ACCEPTANCE_COHORT'
