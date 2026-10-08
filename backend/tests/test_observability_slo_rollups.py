from datetime import timedelta
from uuid import uuid4
import pytest
from pydantic import ValidationError
from backend.observability.slo_rollups import Receipt,Rollups,SREBucketRow,SREReceiptRow,contributions
from backend.observability.slo_sources import subject_digest
from backend.observability.slo_contract import CancelOrigin,RequestClass,LATENCY_IDS,DEFINITIONS
from backend.tests._m7_storage import storage
from backend.tests.test_observability_slo_evaluator import NOW


@pytest.mark.asyncio
async def test_capture_gap_requires_a_new_full_window_before_recovery(tmp_path):
    from backend.observability.slo_rollups import SRESourceRow
    db,_=await storage(tmp_path/'coverage.db');roll=Rollups(db.sessions)
    try:
        await roll.heartbeat('development',NOW,False)
        await roll.heartbeat('development',NOW+timedelta(seconds=60),True)
        async with db.sessions() as session:
            row=await session.get(SRESourceRow,'development')
            assert not row.capture_complete and row.coverage_start==NOW
        # Explicitly model uninterrupted healthy heartbeats since the gap.
        async with db.sessions() as session,session.begin():
            row=await session.get(SRESourceRow,'development');row.updated_at=NOW+timedelta(days=28)-timedelta(seconds=1)
        await roll.heartbeat('development',NOW+timedelta(days=28),True)
        assert (await roll.snapshot('development','availability',NOW+timedelta(days=28),0)).capture_complete
    finally:await db.close()


def receipt(**changes):
    values=dict(run_id=str(uuid4()),environment='development',subject_digest=subject_digest('owner'),accepted_at=NOW-timedelta(minutes=10),deadline_at=NOW-timedelta(minutes=7),terminal_at=NOW-timedelta(minutes=9),terminal_status='COMPLETED',terminal_count=1,valid_outcome=True,request_class=RequestClass.GENERAL,duration_ms=1000,trace_complete=True,version=1)
    values.update(changes);return Receipt(**values)

@pytest.mark.parametrize('outcome',['answer','clarification','source_gap','safe_policy_rejection','approval_request'])
def test_valid_governed_application_outcomes(outcome):
    # Outcome owner supplies valid_outcome, never text matching in the evaluator.
    value=contributions(receipt(valid_outcome=True),NOW)
    assert value['availability']['good']==1 and value['availability']['bad']==0

@pytest.mark.parametrize('status,origin,kind',[('FAILED',CancelOrigin.FAILURE,'bad'),('TIMEOUT',CancelOrigin.DEADLINE,'bad'),('CANCELLED',CancelOrigin.USER,'excluded'),('CANCELLED',CancelOrigin.SERVICE,'bad'),('CANCELLED',CancelOrigin.UNKNOWN,'bad')])
def test_cancellation_denominators(status,origin,kind):
    v=contributions(receipt(terminal_status=status,cancel_origin=origin,valid_outcome=False),NOW)
    assert v['availability'][kind]==1
    assert v['terminal_completion']['good']==1
    assert sum(v['availability'][k] for k in ('good','bad','excluded','unknown'))==1

@pytest.mark.parametrize('status',['COMPLETED','FAILED','TIMEOUT','CANCELLED'])
def test_all_terminal_states_exact_deadline(status):
    r=receipt(terminal_status=status);r=r.model_copy(update={'terminal_at':r.deadline_at})
    assert contributions(r,NOW)['terminal_completion']['good']==1

@pytest.mark.parametrize('changes',[{'terminal_at':None,'terminal_status':None,'terminal_count':0},{'terminal_count':2},{'terminal_at':NOW}])
def test_missing_duplicate_late_closure(changes):assert contributions(receipt(**changes),NOW)['terminal_completion']['bad']==1

def test_unresolved_not_premature_failure():
    r=receipt(deadline_at=NOW+timedelta(minutes=1),terminal_at=None,terminal_status=None,terminal_count=0)
    assert contributions(r,NOW)=={}

@pytest.mark.parametrize('cls',list(LATENCY_IDS))
@pytest.mark.parametrize('delta',[-.001,0,.001])
def test_root_duration_threshold_boundary(cls,delta):
    key=LATENCY_IDS[cls];threshold=DEFINITIONS[key].threshold_seconds*1000
    v=contributions(receipt(request_class=cls,duration_ms=threshold+delta),NOW)[key]
    assert v['good']==int(delta<=0) and v['bad']==int(delta>0)

def test_unknown_class_duration_and_privacy():
    v=contributions(receipt(request_class=RequestClass.UNKNOWN),NOW)
    assert all(v[k]['unknown']==1 for k in LATENCY_IDS.values())
    assert contributions(receipt(duration_ms=None),NOW)['latency_general']['unknown']==1
    with pytest.raises(ValidationError):receipt(prompt='SYNTHETIC_PROHIBITED_MARKER')

@pytest.mark.asyncio
async def test_durable_idempotency_update_restart_and_sse(tmp_path):
    db,_=await storage(tmp_path/'sre.db');roll=Rollups(db.sessions)
    r=receipt(sse_expected=True,emitted_at=NOW-timedelta(minutes=9),transport='relay_succeeded')
    await roll.save(r,NOW);await roll.save(r,NOW)
    await roll.heartbeat('development',NOW)
    v=await roll.snapshot('development','availability',NOW,0)
    assert sum(b.counts.good for b in v.buckets)==1
    assert not await roll.receive(r.run_id,subject_digest('foreign'),('development',),r.emitted_at+timedelta(seconds=5))
    assert await roll.receive(r.run_id,r.subject_digest,('development',),r.emitted_at+timedelta(seconds=5))
    assert await roll.receive(r.run_id,r.subject_digest,('development',),r.emitted_at+timedelta(seconds=6))
    await roll.save(r,NOW)  # delayed relay snapshot must not erase browser ack
    v=await roll.snapshot('development','sse_delivery',NOW,0)
    assert sum(b.counts.good for b in v.buckets)==1 and sum(b.counts.bad for b in v.buckets)==0
    assert not (await roll.snapshot('production','availability',NOW,0)).available
    await db.close()
    from backend.observability.database import Database
    db=Database('sqlite+aiosqlite:///'+str(tmp_path/'sre.db'),isolated_test=True)
    v=await Rollups(db.sessions).snapshot('development','availability',NOW,0)
    assert sum(b.counts.good for b in v.buckets)==1
    await db.close()

@pytest.mark.asyncio
async def test_orphan_settlement_does_not_change_m2_and_late_evidence(tmp_path):
    db,_=await storage(tmp_path/'orphan.db');roll=Rollups(db.sessions)
    r=receipt(terminal_at=None,terminal_status=None,terminal_count=0)
    await roll.save(r,r.accepted_at);assert await roll.settle(NOW)==1
    assert r.terminal_status is None
    await roll.heartbeat('development',NOW)
    v=await roll.snapshot('development','terminal_completion',NOW,0)
    assert sum(b.counts.bad for b in v.buckets)==1
    await roll.save(r.model_copy(update={'version':2,'terminal_at':r.deadline_at,'terminal_status':'TIMEOUT','terminal_count':1}),NOW)
    v=await roll.snapshot('development','terminal_completion',NOW,0)
    assert sum(b.counts.bad for b in v.buckets)==0 and sum(b.counts.good for b in v.buckets)==1
    await db.close()

@pytest.mark.parametrize('kwargs,kind',[({'browser_received':True},'good'),({'transport':'relay_failed'},'bad'),({'transport':'backpressure_failed'},'bad'),({'transport':'unknown'},'unknown'),({'transport':'client_disconnected','emitted_at':None},'excluded'),({},'bad')])
def test_sse_distinct_transport_semantics(kwargs,kind):
    r=receipt(sse_expected=True,emitted_at=NOW-timedelta(minutes=9),transport='relay_succeeded')
    r=r.model_copy(update=kwargs)
    assert contributions(r,NOW)['sse_delivery'][kind]==1
    assert contributions(r,NOW)['availability']['good']==1
