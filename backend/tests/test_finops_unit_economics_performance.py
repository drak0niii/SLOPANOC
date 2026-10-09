"""Isolated synthetic measurements; never a production capacity assertion."""
import json
import resource
import statistics
import time
from types import SimpleNamespace
import pytest
from backend.tests._m13_unit_economics import *
from backend.tests.test_finops_unit_economics_publication import setup
from backend.tests.test_observability_api import api,authorize
from backend.observability.schemas import Role
from backend.observability.finops.unit_denominators import runtime_counts,utcnow
from backend.observability.finops.unit_economics import calculate
from backend.observability.finops.unit_drivers import distribute

def measure(fn,n=5):
    times=[]
    for _ in range(n):
        start=time.perf_counter();fn();times.append((time.perf_counter()-start)*1000)
    return {'median_ms':statistics.median(times),'p95_ms':max(times),'samples':n}

@pytest.mark.asyncio
async def test_grouped_pipeline_publication_and_cached_read_measurements(system,api,monkeypatch):
    report={};rss=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    for n in (1000,10000,100000):
        attempts=[];bases={}
        for i in range(n):
            key=str(i)
            attempts.append(SimpleNamespace(attempt_id=key,started_at=JAN,state='USAGE_CAPTURED',identity={
                'provider':'gcp.gemini','model':'gemini-2.5-flash','operation_type':'GENERATION',
                'workload':'USER_TURN','logical_call_id':str(i//2)}))
            bases[key]=SimpleNamespace(metadata_safe={'region':'europe-west1'},observed_at=JAN)
        report[str(n)+'_attempt_grouping']=measure(lambda:runtime_counts(attempts,bases,WINDOW),3)
        populations,logical,aligned=runtime_counts(attempts,bases,WINDOW)
        assert len(populations)==1 and populations[0].attempts==n and logical==n//2 and aligned
    data=economics_inputs()
    from backend.tests.test_finops_unit_denominators import receipt
    from backend.observability.finops.unit_denominators import turn_counts,turn_contexts
    base=receipt().model_copy(update={'duration_ms':100})
    for n in (1000,10000,100000):
        turns=[base.model_copy(update={'run_id':str(i)}) for i in range(n)]
        report[str(n)+'_turn_grouping']=measure(lambda:(turn_counts(turns,WINDOW.end),turn_contexts(turns,WINDOW.end)),3)
        assert turn_counts(turns,WINDOW.end)[0]['ACCEPTED_TURN']==n
        assert report[str(n)+'_turn_grouping']['p95_ms']<60000
    report['registry_day']=measure(lambda:calculate(**data))
    report['driver_residual_1000_populations']=measure(lambda:distribute('30',{str(i):i+1 for i in range(1000)}))
    start=time.perf_counter()
    for i in range(28):
        daily=deepcopy(data);day=JAN+timedelta(days=i);period=day.date().isoformat()
        daily['billing']['period']=daily['runtime']['period']=period
        daily['snapshots']=[s.model_copy(update={'period':period}) for s in daily['snapshots']]
        for bucket in daily['billing']['buckets']:
            bucket['usage_start']=day.isoformat();bucket['usage_end']=(day+timedelta(hours=1)).isoformat()
        assert calculate(**daily)['period']==period
    report['28_independent_daily_generations_ms']=(time.perf_counter()-start)*1000
    repo,worker=await setup(system)
    start=time.perf_counter();f=await repo.financial('development',SCOPE,WINDOW.partition)
    report['frozen_m12_input_load_ms']=(time.perf_counter()-start)*1000
    assert f
    original=repo.publish
    async def measured_publish(*args,**kwargs):
        start=time.perf_counter();result=await original(*args,**kwargs)
        report['publication_transaction_ms']=(time.perf_counter()-start)*1000
        return result
    monkeypatch.setattr(repo,'publish',measured_publish)
    start=time.perf_counter();assert await worker.run_period(WINDOW,utcnow())=='PUBLISHED'
    report['source_load_compute_publish_ms']=(time.perf_counter()-start)*1000
    app,client,_,_,backend=api;authorize(app,Role.FINOPS)
    backend.unit_economics_repository=repo;backend.financial_sources=system.sources
    import backend.observability.finops.unit_economics_service as reads
    monkeypatch.setattr(reads,'now',lambda:NOW)
    times=[]
    for _ in range(5):
        start=time.perf_counter()
        r=await client.get('/api/observability/finops/unit-economics/PROVIDER_ATTEMPT',params={'start':WINDOW.partition,'end':'2026-01-11'})
        times.append((time.perf_counter()-start)*1000);assert r.status_code==200
    report['cached_api']={'median_ms':statistics.median(times),'p95_ms':max(times),'samples':5}
    report['peak_rss_growth_bytes_macos']=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss-rss
    assert report['source_load_compute_publish_ms']<60000 and max(times)<2000
    assert report['100000_attempt_grouping']['p95_ms']<60000
    print('M13_PERFORMANCE '+json.dumps(report,sort_keys=True))
