"""Local SQLite measurements; these do not establish production PostgreSQL capacity."""
import json
import statistics
import time
import resource
from datetime import timedelta
from uuid import uuid4
import pytest
from sqlalchemy import insert,select,func
from backend.tests.test_finops_runtime_ledger import accounting,identity,payload,started,NOW
from backend.observability.finops.models import AttemptRow,InboxRow,LedgerRow,BucketRow
from backend.observability.finops.contracts import QUANTITIES

@pytest.mark.asyncio
async def test_local_accounting_latency_and_100000_row_synthetic_population(accounting,tmp_path):
    db,repo,ledger=accounting;measurements={}
    async def measure(name,call):
        begin=time.perf_counter();result=await call();measurements.setdefault(name,[]).append((time.perf_counter()-begin)*1000);return result
    for _ in range(30):
        i=identity();p=payload(i)
        await measure('admission',lambda:repo.admit(i))
        await measure('start',lambda:repo.start(i,i.started_at))
        await measure('capture',lambda:repo.capture(p))
        await measure('materialization',lambda:repo.materialize(i.event_id))
        await measure('duplicate_capture',lambda:repo.capture(p))
        await measure('duplicate_materialization',lambda:repo.materialize(i.event_id))
    # Bounded fixture batches create a100k persisted accounting population for
    # production-shaped read/index tests. Bulk fixture throughput is NOT hot-path throughput.
    peak_before=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss;begin=time.perf_counter()
    for chunk in range(100):
        attempts=[];events=[];buckets={}
        for n in range(chunk*1000,(chunk+1)*1000):
            i=identity(model='gemini-2.5-pro' if n%2 else 'gemini-2.5-flash',started_at=NOW-timedelta(minutes=n%40320+1))
            p=payload(i);safe=p.model_dump(mode='json');safe.pop('usage')
            attempts.append(dict(environment=i.environment,attempt_id=str(i.attempt_id),identity=i.model_dump(mode='json'),state='USAGE_CAPTURED',admitted_at=i.started_at,started_at=i.started_at,provider_finished_at=NOW,deadline_at=i.deadline_at,captured_at=NOW))
            events.append(dict(event_id=i.event_id,environment=i.environment,attempt_id=str(i.attempt_id),record_kind='BASE',payload_hash=p.digest(),metadata_safe=safe,observed_at=i.started_at,created_at=NOW,**{q:getattr(p.usage,q) for q in QUANTITIES}))
            key=(i.started_at,i.model)
            buckets[key]=buckets.get(key,0)+1
        async with db.sessions() as s,s.begin():
            await s.execute(insert(AttemptRow),attempts);await s.execute(insert(LedgerRow),events)
            from backend.observability.finops.repository import insert_for
            values=[]
            for (at,model),count in buckets.items():
                keys=dict(environment='development',at=at,provider='gcp.gemini',model=model,agent='team_manager',workload='USER_TURN',operation='orchestration',operation_type='GENERATION')
                values.append(dict(**keys,good=count,bad=0,quantity_known=0,quantity_partial=count,
                    **{q:100*count if q=='input_tokens' else 0 for q in QUANTITIES},
                    **{q+'_known':count if q=='input_tokens' else 0 for q in QUANTITIES}))
            stmt=insert_for(s,BucketRow).values(values)
            await s.execute(stmt.on_conflict_do_update(index_elements=['environment','at','provider','model','agent','workload','operation','operation_type'],
                set_={k:getattr(BucketRow,k)+stmt.excluded[k] for k in ('good','quantity_partial','input_tokens','input_tokens_known')}))
    fixture_seconds=time.perf_counter()-begin
    async with db.sessions() as s:
        assert (await s.execute(select(func.count()).select_from(LedgerRow))).scalar_one()==100030
    for _ in range(5):
        rows,more=await measure('usage_100000',lambda:repo.usage('development',NOW-timedelta(days=28),NOW))
        assert sum(r['attempts'] for r in rows)==100030 and not more
        await measure('health_100000',lambda:repo.health('development'))
        await measure('completeness_100000',lambda:repo.buckets('development',NOW-timedelta(days=28),NOW))
    for _ in range(100):
        i=identity();await started(repo,i);await repo.capture(payload(i))
    await measure('recovery_100_batch',ledger.recover)
    assert (await repo.health('development'))['pending']==0
    peak_after=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    report={k:{'median_ms':round(statistics.median(v),3),'p95_ms':round(sorted(v)[min(len(v)-1,int(.95*len(v)))],3),'samples':len(v)} for k,v in measurements.items()}
    report.update(engine='isolated_SQLite',fixture_rows=100000,fixture_seconds=round(fixture_seconds,3),batch_bound=1000,
        rss_peak_growth_native=peak_after-peak_before,capacity_claim='NONE')
    print('M10_PERFORMANCE '+json.dumps(report,sort_keys=True))
    (tmp_path/'performance.json').write_text(json.dumps(report))
