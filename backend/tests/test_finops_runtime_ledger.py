"""Isolated accounting durability, idempotency and physical population tests."""
import asyncio
from datetime import datetime, timezone, timedelta
from uuid import uuid4
import pytest
import pytest_asyncio
from sqlalchemy import select, func, update
from backend.observability.database import Database
from backend.observability.config import ObservabilityConfig
from backend.observability.finops.models import FinOpsBase, AttemptRow, InboxRow, LedgerRow, BucketRow, SourceRow
from backend.observability.finops.contracts import Identity, Payload, Adjustment
from backend.observability.finops.repository import Repository
from backend.observability.finops.usage_ledger import Ledger, install, release, AdmissionFailed
from backend.observability.finops.slo_source import AccountingSource
from backend.observability.model_usage import ModelUsage
from backend.observability.slo_evaluator import evaluate

NOW=datetime(2026,10,8,12,tzinfo=timezone.utc)

def identity(**changes):
    return Identity(environment='development',attempt_id=uuid4(),logical_call_id=uuid4(),provider_request_id=uuid4(),attempt=1,
        provider='gcp.gemini',model='gemini-2.5-flash',agent='team_manager',operation='orchestration',
        operation_type='GENERATION',workload='USER_TURN',started_at=NOW-timedelta(minutes=1),deadline_at=NOW+timedelta(minutes=5)).model_copy(update=changes)

def payload(i,tokens=100):
    return Payload(identity=i,observed_at=NOW,provider_finished_at=NOW,
        usage=ModelUsage(input_tokens=tokens,availability='PARTIAL' if tokens is not None else 'UNKNOWN',source='provider'),
        status='COMPLETED',config_version='a'*16)

@pytest_asyncio.fixture
async def accounting(tmp_path):
    db=Database('sqlite+aiosqlite:///'+str(tmp_path/'accounting.db'),isolated_test=True)
    async with db.engine.begin() as c:await c.run_sync(FinOpsBase.metadata.create_all)
    repo=Repository(db.sessions);ledger=Ledger(repo,ObservabilityConfig())
    yield db,repo,ledger
    release(ledger);await db.close()

async def started(repo,i):
    await repo.admit(i,NOW-timedelta(days=29));await repo.start(i,i.started_at)

async def ledger_count(db):
    async with db.sessions() as s:return (await s.execute(select(func.count()).select_from(LedgerRow))).scalar_one()

@pytest.mark.asyncio
async def test_single_duplicate_conflict_unknown_zero_and_retry(accounting):
    db,repo,ledger=accounting;i=identity();await started(repo,i);p=payload(i)
    assert await repo.capture(p)=='ACCEPTED';assert await repo.materialize(i.event_id)=='PERSISTED'
    for _ in range(3):
        assert await repo.capture(p)=='DUPLICATE';assert await repo.materialize(i.event_id)=='DUPLICATE'
    j=identity();await started(repo,j);await repo.capture(payload(j,200));await repo.materialize(j.event_id)
    assert await ledger_count(db)==2
    rows,_=await repo.usage('development',NOW-timedelta(hours=1),NOW)
    assert rows[0]['quantities']['input_tokens']=={'observed':300,'known':2,'unknown':0}
    assert await repo.capture(payload(i,999))=='CONFLICT'
    async with db.sessions() as s:
        assert (await s.get(LedgerRow,i.event_id)).input_tokens==100
        assert (await s.get(AttemptRow,('development',str(i.attempt_id)))).state=='CONFLICT'
    assert (await repo.health('development'))['conflicts']==1
    for n in (None,0):
        k=identity();await started(repo,k);await repo.capture(payload(k,n));await repo.materialize(k.event_id)
        async with db.sessions() as s:assert (await s.get(LedgerRow,k.event_id)).input_tokens==n
    rows,_=await repo.usage('development',NOW-timedelta(hours=1),NOW)
    assert rows[0]['quantities']['input_tokens']=={'observed':300,'known':3,'unknown':1}

@pytest.mark.asyncio
async def test_twenty_concurrent_duplicate_writes(accounting):
    db,repo,ledger=accounting;i=identity();await started(repo,i);p=payload(i)
    async def write():
        await repo.capture(p);return await repo.materialize(i.event_id)
    results=await asyncio.gather(*(write() for _ in range(20)))
    assert results.count('PERSISTED')==1 and await ledger_count(db)==1
    rows,_=await repo.usage('development',NOW-timedelta(hours=1),NOW)
    assert rows[0]['attempts']==1 and rows[0]['quantities']['input_tokens']['observed']==100

@pytest.mark.asyncio
async def test_crash_windows_admission_debt_inbox_ledger_restart(accounting):
    db,repo,ledger=accounting
    # A: no accounting admission, no provider execution, no population.
    assert await ledger_count(db)==0
    # B: durable admission only; expiry closes as NOT_STARTED, no missing usage.
    b=identity(deadline_at=NOW-timedelta(seconds=1));await repo.admit(b,NOW)
    await repo.checkpoint('development',NOW)
    async with db.sessions() as s:assert (await s.get(AttemptRow,('development',str(b.attempt_id)))).state=='NOT_STARTED'
    assert (await repo.health('development'))['debt']==0
    # C: transport entered; unrecordable final payload remains debt across repo restart.
    c=identity(deadline_at=NOW-timedelta(seconds=1));await started(repo,c)
    restarted=Repository(db.sessions);await restarted.checkpoint('development',NOW)
    async with db.sessions() as s:assert (await s.get(AttemptRow,('development',str(c.attempt_id)))).state=='OUTCOME_UNKNOWN'
    assert (await restarted.health('development'))['debt']==1
    # D: committed safe inbox is enough to recover without any provider callback.
    d=identity();await started(repo,d);await repo.capture(payload(d))
    replacement=Ledger(restarted,ObservabilityConfig())
    assert await replacement.recover()==1;assert await ledger_count(db)==1
    # E: committed event before acknowledgement; late accounting replay is unchanged.
    await restarted.capture(payload(d));await restarted.materialize(d.event_id)
    assert await ledger_count(db)==1

@pytest.mark.asyncio
@pytest.mark.parametrize('starts,captured,unknown',[(100,100,0),(100,99,0),(90,90,0),(100,100,5),(0,0,0)])
async def test_m9_completeness_population_and_quantity_coverage(accounting,starts,captured,unknown):
    db,repo,ledger=accounting
    await repo.checkpoint('development',NOW-timedelta(days=29))
    for n in range(100 if starts else 0):
        i=identity();await repo.admit(i,NOW-timedelta(days=29))
        if n>=starts:await repo.not_started(i);continue
        await repo.start(i,NOW-timedelta(minutes=1))
        if n<captured:
            p=payload(i,None if n<unknown else 100)
            if n>=unknown:p=p.model_copy(update={'usage':ModelUsage(input_tokens=100,output_tokens=5,total_tokens=105,availability='KNOWN',source='provider')})
            await repo.capture(p);await repo.materialize(i.event_id)
    await repo.checkpoint('development',NOW)
    snap=await AccountingSource(repo).snapshot('development','cost_ledger_completeness',NOW)
    value=evaluate('cost_ledger_completeness',snap,now=NOW)
    assert value['eligible']==starts and value['good']==captured and value['bad']==starts-captured
    assert value['current_value']==(captured/starts if starts else None)
    assert value['state']==('INSUFFICIENT_DATA' if not starts else 'BREACHED' if captured<starts else 'HEALTHY')
    assert value['remaining_fraction'] is None and not value['burn_tiers']
    if unknown:
        rows,_=await repo.usage('development',NOW-timedelta(hours=1),NOW)
        assert rows[0]['quantity_known']==95 and rows[0]['quantities']['input_tokens']['unknown']==5
    stale=evaluate('cost_ledger_completeness',snap,now=NOW+timedelta(minutes=6))
    assert stale['state']=='STALE_DATA'

@pytest.mark.asyncio
async def test_immutable_correction_replay(accounting):
    db,repo,ledger=accounting;i=identity();await started(repo,i);await repo.capture(payload(i));await repo.materialize(i.event_id)
    a=Adjustment(correction_id=uuid4(),original_event_id=i.event_id,environment='development',reason='QUANTITY_CORRECTION',process='accounting_recovery',at=NOW,quantities={'input_tokens':-20})
    assert await repo.adjust(a)=='PERSISTED';assert await repo.adjust(a)=='DUPLICATE'
    rows,_=await repo.usage('development',NOW-timedelta(hours=1),NOW)
    assert rows[0]['attempts']==1 and rows[0]['quantities']['input_tokens']['observed']==80
    async with db.sessions() as s:assert (await s.get(LedgerRow,i.event_id)).input_tokens==100


@pytest.mark.asyncio
async def test_late_usage_supplements_null_coverage_without_mutating_base(accounting):
    db,repo,ledger=accounting;i=identity();await started(repo,i)
    await repo.capture(payload(i,None));await repo.materialize(i.event_id)
    a=Adjustment(correction_id=uuid4(),original_event_id=i.event_id,environment='development',
        reason='PROVIDER_LATE_USAGE',process='provider_usage_adapter',at=NOW,
        quantities={'input_tokens':7,'output_tokens':4,'total_tokens':11})
    assert await repo.adjust(a)=='PERSISTED';assert await repo.adjust(a)=='DUPLICATE'
    rows,_=await repo.usage('development',NOW-timedelta(hours=1),NOW)
    assert rows[0]['quantity_known']==1 and rows[0]['quantity_partial']==0
    assert rows[0]['quantities']['input_tokens']=={'observed':7,'known':1,'unknown':0}
    async with db.sessions() as s:assert (await s.get(LedgerRow,i.event_id)).input_tokens is None
    with pytest.raises(ValueError):await repo.adjust(a.model_copy(update={'correction_id':uuid4()}))

@pytest.mark.parametrize('field',['prompt','response','teams_content','command','tool_args','sql','url','authorization','vectors','chain_of_thought'])
def test_payload_rejects_restricted_fields_before_any_persistence(field):
    from pydantic import ValidationError
    body=payload(identity()).model_dump()
    with pytest.raises(ValidationError):Payload.model_validate(body|{field:'PRIVATE_SENTINEL'})
    body['identity'][field]='PRIVATE_SENTINEL'
    with pytest.raises(ValidationError):Payload.model_validate(body)
