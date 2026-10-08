"""Local SQLite measurements, not a production capacity commitment."""
import json
import statistics
import time
from datetime import timedelta
from pathlib import Path
import pytest
from sqlalchemy import insert
from backend.observability.slo_contract import DEFINITIONS
from backend.observability.slo_evaluator import Bucket, Counts, Snapshot, evaluate
from backend.observability.slo_rollups import Rollups, SREBucketRow
from backend.observability.slo_sources import Writer, RuntimeProvider
from backend.observability.slo_service import slos
from backend.observability.service import Service
from backend.observability.authorization import Principal
from backend.observability.schemas import Role, ROLE_PERMISSIONS
from backend.tests._m7_storage import storage, config
from backend.tests.test_observability_slo_evaluator import NOW
from backend.tests.test_observability_slo_rollups import receipt


def report(samples):
    return {'median_ms':round(statistics.median(samples),4),'max_ms':round(max(samples),4),'repetitions':len(samples)}


@pytest.mark.asyncio
async def test_representative_28day_rollups_query_parity_and_performance(tmp_path):
    db,repo=await storage(tmp_path/'performance.db')
    rollups=Rollups(db.sessions);cfg=config()
    keys=[key for key,d in DEFINITIONS.items() if d.source=='SRE_ROLLUPS']
    buckets=tuple(Bucket(NOW-timedelta(minutes=i+1),Counts(good=99,bad=int(i%997==0))) for i in range(28*24*60))
    rows=[dict(environment='development',slo_id=key,at=b.at,good=b.counts.good,bad=b.counts.bad,unknown=0,excluded=0) for key in keys for b in buckets]
    async with db.sessions() as session,session.begin():
        for offset in range(0,len(rows),5000):
            await session.execute(insert(SREBucketRow),rows[offset:offset+5000])
    await rollups.heartbeat('development',NOW-timedelta(days=29))
    # Gap truthfulness is tested elsewhere; fixture explicitly establishes known
    # uninterrupted source history rather than fabricating it through heartbeats.
    from backend.observability.slo_rollups import SRESourceRow
    async with db.sessions() as session,session.begin():
        row=await session.get(SRESourceRow,'development');row.updated_at=NOW
    metrics={}
    try:
        fixture=Snapshot('development',buckets,NOW,NOW-timedelta(days=29))
        samples=[]
        for _ in range(5):
            started=time.perf_counter();expected=evaluate('availability',fixture,now=NOW);samples.append((time.perf_counter()-started)*1000)
        metrics['one_slo_raw_40320_minutes']=report(samples)
        compact=await rollups.snapshot('development','availability',NOW,0)
        assert len(compact.buckets)<=6
        actual=evaluate('availability',compact,now=NOW)
        for field in ('eligible','good','bad','unknown','remaining_fraction','burn_windows','burn_tiers','state'):
            assert actual[field]==expected[field],field
        samples=[]
        for _ in range(5):
            started=time.perf_counter()
            for key in DEFINITIONS:evaluate(key,compact,now=NOW)
            samples.append((time.perf_counter()-started)*1000)
        metrics['full_set_compact_evaluation']=report(samples)
        samples=[]
        for _ in range(5):
            started=time.perf_counter();await rollups.record('local','availability',NOW,Counts(good=1));samples.append((time.perf_counter()-started)*1000)
        metrics['transactional_rollup_update']=report(samples)
        service=Service(repo,cfg);service.slo_provider=RuntimeProvider(rollups,cfg)
        principal=Principal('synthetic',Role.OPERATOR,ROLE_PERMISSIONS[Role.OPERATOR],frozenset({'development'}),True)
        samples=[]
        for _ in range(5):
            started=time.perf_counter();page=await slos(service,principal,clock=lambda:NOW);samples.append((time.perf_counter()-started)*1000)
            assert len(page.items)==len(DEFINITIONS)
            assert next(v for v in page.items if v.slo_id=='availability').state.value=='HEALTHY'
        metrics['protected_api_full_set']=report(samples)
        writer=Writer(rollups,cfg,clock=lambda:NOW)
        samples=[];r=receipt()
        for i in range(100):
            started=time.perf_counter();assert writer.publish(r.model_copy(update={'version':i}));samples.append((time.perf_counter()-started)*1000)
        metrics['bounded_receipt_publication']=report(samples)
        await writer.drain();await writer.close()
        metrics['dataset']={'days':28,'minutes_per_slo':40320,'slo_populations':len(keys),'aggregate_rows':len(rows),'database':'isolated SQLite','scope':'local synthetic only'}
        Path('/private/tmp/slopanoc-m9-performance.json').write_text(json.dumps(metrics,indent=2)+'\n')
        print('M9 local performance:',json.dumps(metrics))
    finally:await db.close()
