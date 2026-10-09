"""Local synthetic measurements: no production capacity/cost claim."""
import json,time,statistics,resource
from copy import deepcopy
from types import SimpleNamespace
from decimal import Decimal
import pytest
from backend.tests._m12_reconciliation import *
from backend.tests.test_observability_api import api,authorize
from backend.observability.schemas import Role
from backend.observability.finops.reconciliation import reconcile
from backend.observability.finops.allocation import classify,validate
from backend.observability.finops.reconciliation_contracts import controls


def sample(fn,n=5):
    values=[]
    for _ in range(n):
        at=time.perf_counter();fn();values.append((time.perf_counter()-at)*1000)
    return dict(median_ms=statistics.median(values),p95_ms=max(values),n=n)

@pytest.mark.asyncio
async def test_local_grouped_high_volume_and_28day_measurement(system,api,monkeypatch):
    report={};rss=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss;r,_,pricing=inputs();reg=registry()
    report['mapping_load']=sample(lambda:Registry.model_validate(reg.model_dump()),30)
    bulk=[]
    for count in (1000,10000,100000):
        raw=charge('0.1','1')
        rows=tuple(dict(raw,sku={'id':'sku-input' if i%100==0 else 'sku-'+str(i%100)}) for i in range(count))
        ex=extraction(rows);at=time.perf_counter();projection=normalize(ex,source_mapping());source_ms=(time.perf_counter()-at)*1000
        manifest=SimpleNamespace(fingerprint=projection.fingerprint,row_count=projection.rows,controls=list(projection.controls),generation='a'*64,export_time=projection.export_time)
        at=time.perf_counter();b=billing_input(ex,manifest,source_mapping(),SCOPE,'development');input_ms=(time.perf_counter()-at)*1000
        at=time.perf_counter();result=reconcile(r,b,pricing,reg);engine_ms=(time.perf_counter()-at)*1000
        assert Decimal(result['amounts'][0]['net'])==Decimal(count)/10
        assert result['amounts'][0]['count']==count and len(b['buckets'])==100
        assert input_ms<120000
        bulk.append(input_ms)
        report[str(count)]=dict(rows=count,billing_buckets=100,serialized_billing_input_bytes=len(json.dumps(b,separators=(',',':')).encode()),source_normalization_ms=source_ms,billing_materialization_ms=input_ms,one_day_engine_ms=engine_ms)
    # Tenfold population growth must remain grouped, not pairwise explosive.
    assert bulk[2]<bulk[1]*30+1000
    decisions={c['bucket']:('UNALLOCATED','NONE','UNMAPPED') for c in b['components']}
    report['classification']=sample(lambda:classify(b['components'],decisions))
    classified=classify(b['components'],decisions)
    report['controls']=sample(lambda:validate(b['components'],classified))
    report['quantity_matching_and_engine']=sample(lambda:reconcile(r,b,pricing,reg))
    # 28 distinct frozen-day calculations, not an unbounded full-history job.
    at=time.perf_counter()
    for i in range(28):
        day=JAN+timedelta(days=i);rr=deepcopy(r);bb=deepcopy(b);pp=deepcopy(pricing)
        rr['period']=bb['period']=pp['period']=day.date().isoformat()
        for bucket in bb['buckets']:
            bucket['usage_start']=day.isoformat();bucket['usage_end']=(day+timedelta(hours=1)).isoformat()
        assert reconcile(rr,bb,pp,reg)['controls']['count']=='PASS'
    report['28_day_engine_ms']=(time.perf_counter()-at)*1000
    await capture(system.runtime)
    at=time.perf_counter();runtime=await system.worker.runtime_provider.snapshot('development',WINDOW);report['runtime_db_snapshot_ms']=(time.perf_counter()-at)*1000
    # CPU aggregation scaling separately from source publication/DB reads.
    row=SimpleNamespace(event_id='',payload_hash='hash',record_kind='BASE',original_event_id=None,
        metadata_safe={'identity':{'provider':'gcp.gemini','model':'gemini-2.5-flash','operation_type':'GENERATION'},'region':'europe-west1'})
    from backend.observability.finops.contracts import QUANTITIES
    for field in QUANTITIES:setattr(row,field,1 if field=='input_tokens' else None)
    runtime_rows=[SimpleNamespace(**dict(vars(row),event_id=str(i))) for i in range(100000)]
    at=time.perf_counter();synthetic=runtime_input(runtime_rows,[],'development',WINDOW);report['runtime_100k_aggregation_ms']=(time.perf_counter()-at)*1000
    assert synthetic['buckets'][0]['quantities']['input_tokens']['observed']=='100000'
    await system.ingestion.ingest('DETAILED_BILLING',WINDOW);await system.worker.drain()
    # Separate persisted M11 input loading and publication transaction from the
    # end-to-end worker measurement. Instrument only this isolated repository.
    original_billing=system.repo.billing;original_publish=system.repo.publish
    async def measured_billing(*args,**kwargs):
        at=time.perf_counter();value=await original_billing(*args,**kwargs)
        report['persisted_billing_input_load_ms']=(time.perf_counter()-at)*1000
        return value
    async def measured_publish(*args,**kwargs):
        at=time.perf_counter();value=await original_publish(*args,**kwargs)
        report['publication_transaction_ms']=(time.perf_counter()-at)*1000
        return value
    monkeypatch.setattr(system.repo,'billing',measured_billing)
    monkeypatch.setattr(system.repo,'publish',measured_publish)
    at=time.perf_counter();assert await system.worker.run_period(WINDOW,NOW)=='PUBLISHED';report['input_load_compute_publish_ms']=(time.perf_counter()-at)*1000
    monkeypatch.setattr(system.repo,'billing',original_billing)
    monkeypatch.setattr(system.repo,'publish',original_publish)
    samples=[]
    for _ in range(5):
        at=time.perf_counter();await system.worker.run_period(WINDOW,NOW);samples.append((time.perf_counter()-at)*1000)
    report['idempotent_publication']={'median_ms':statistics.median(samples),'p95_ms':max(samples),'n':5}
    app,client,_,_,backend=api;backend.financial_sources=system.sources;backend.reconciliation_repository=system.repo;authorize(app,Role.FINOPS)
    import backend.observability.finops.reconciliation_service as service
    monkeypatch.setattr(service,'now',lambda:NOW)
    samples=[]
    for _ in range(5):
        at=time.perf_counter();response=await client.get('/api/observability/finops/reconciliation/2026-01-10');samples.append((time.perf_counter()-at)*1000);assert response.status_code==200,response.text
    report['api']={'median_ms':statistics.median(samples),'p95_ms':max(samples),'n':5}
    report['peak_rss_growth_bytes_macos']=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss-rss
    print('M12_PERFORMANCE '+json.dumps(report,sort_keys=True))
