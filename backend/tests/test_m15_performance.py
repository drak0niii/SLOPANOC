"""Reproducible local timings; these do not qualify production capacity."""
import json
import statistics
import time
from datetime import timedelta
import pytest
from sqlalchemy import insert
from backend.observability.archive import FilesystemArchive,sanitize_request
from backend.observability.privacy_policy import attributes,exception,Surface
from backend.observability.sampling_policy import decide
from backend.observability.retention_worker import RetentionWorker
from backend.observability.models import SummaryRow
from backend.tests.test_m15_archive import request,principal,TID
from backend.tests._m7_storage import storage,state
from backend.observability.projection import utcnow
from backend.observability.stages import RunStatus

def measure(call,n):
    samples=[]
    for _ in range(n):
        start=time.perf_counter();call();samples.append((time.perf_counter()-start)*1000)
    samples.sort()
    return {'iterations':n,'mean_ms':statistics.mean(samples),'p95_ms':samples[min(n-1,int(n*.95))]}

def test_local_policy_and_archive_overhead(tmp_path):
    results={}
    values={'slopanoc.status':'COMPLETED','slopanoc.run_id':'a'*32,'prompt':'PRIVATE_PROMPT'}
    results['producer_projection']=measure(lambda:attributes(values,producer='turn',surface=Surface.TRACE,environment='test'),1000)
    results['structured_exception']=measure(lambda:exception(ValueError('PRIVATE_SQL'),code='DATABASE_QUERY_ERROR',subsystem='database_query',operation='db.client'),1000)
    results['sampling_decision']=measure(lambda:decide(TID,'HEALTHY',.1),10000)
    for spans in (2,100,1000):
        raw=request(children=spans-1).SerializeToString()
        results[f'sanitize_{spans}_spans']=measure(lambda:sanitize_request(raw,'test'),5)
        archive=FilesystemArchive(tmp_path/str(spans),'test')
        start=time.perf_counter();commit,=archive.submit(raw)
        assert commit.state=='COMPLETE'
        results[f'commit_{spans}_spans']={'elapsed_ms':(time.perf_counter()-start)*1000,'input_bytes':len(raw)}
        results[f'reconstruct_{spans}_spans']=measure(lambda:archive.reconstruct(principal(),TID,commit.digest),5)
        results[f'retry_{spans}_spans']=measure(lambda:archive.submit(raw),5)
    print('M15_LOCAL_PERFORMANCE='+json.dumps(results,sort_keys=True))

@pytest.mark.asyncio
async def test_local_bounded_retention_high_volume(tmp_path):
    db,repo=await storage(tmp_path/'performance.sqlite')
    now=utcnow()+timedelta(days=100)
    try:
        # Populate via the actual projection contract, then use its immutable safe
        # summary shape for the bulk local fixture (never a shared database).
        await repo.persist(state(status=RunStatus.FAILED))
        from sqlalchemy import select
        async with db.sessions() as session:
            row=(await session.execute(select(SummaryRow.__table__))).mappings().one()
            values=[{**dict(row),'run_id':f'00000000-0000-4000-8000-{n:012d}'} for n in range(1000)]
        async with db.sessions() as session,session.begin():await session.execute(insert(SummaryRow),values)
        worker=RetentionWorker(db.sessions,'development',batch=100,seconds=2)
        results={}
        start=time.perf_counter()
        async with db.sessions() as session:
            candidates=(await session.execute(select(SummaryRow.run_id).where(SummaryRow.environment=='development',SummaryRow.expiry_at<=now).limit(100))).all()
        results['candidate_query']={'elapsed_ms':(time.perf_counter()-start)*1000,'candidates':len(candidates)}
        for dry in (True,False):
            start=time.perf_counter();report=await worker.run('exceptional_summary',now,dry_run=dry)
            results['dry_run' if dry else 'delete_batch']={'elapsed_ms':(time.perf_counter()-start)*1000,'scanned':report.scanned,'deleted':report.deleted}
            assert report.scanned==100 and report.deleted==(0 if dry else 100)
        print('M15_LOCAL_RETENTION_PERFORMANCE='+json.dumps(results,sort_keys=True))
    finally:await db.close()

from backend.observability.slo_rollups import SREReceiptRow
from backend.tests.test_observability_api import api,authorize
from backend.observability.schemas import Role

@pytest.mark.asyncio
async def test_protected_policy_and_archive_api_timings(api,tmp_path):
    app,client,repo,state,backend=api
    authorize(app,Role.DEVELOPER_SRE)
    archive=FilesystemArchive(tmp_path/'api-archive','development');app.state.observability_archive=archive
    commit,=archive.submit(request(environment='development',children=99).SerializeToString())
    results={}
    for name,path in (('policy','/api/observability/policies'),('run_retention',f'/api/observability/runs/{state.run_id}/retention'),
        ('reconstruction','/api/observability/archive/'+TID+'?digest='+commit.digest+'&limit=100')):
        authorize(app,Role.ADMIN if name=='policy' else Role.DEVELOPER_SRE)
        samples=[]
        for _ in range(10):
            start=time.perf_counter();response=await client.get(path);samples.append((time.perf_counter()-start)*1000)
            assert response.status_code==200 and 'PRIVATE_' not in response.text,(name,response.status_code,response.text)
        results[name]={'iterations':10,'mean_ms':statistics.mean(samples),'max_ms':max(samples),'response_bytes':len(response.content)}
    batch=FilesystemArchive(tmp_path/'batch','test')
    start=time.perf_counter();input_bytes=0
    for n in range(10):
        value=request(children=99)
        for r in value.resource_spans:
            for scope in r.scope_spans:
                for span in scope.spans:span.trace_id=bytes.fromhex(format(n+100,'032x'))
        raw=value.SerializeToString();input_bytes+=len(raw)
        assert batch.submit(raw)[0].state=='COMPLETE'
    results['batch_exceptional']={'traces':10,'spans':1000,'input_bytes':input_bytes,'elapsed_ms':(time.perf_counter()-start)*1000}
    print('M15_LOCAL_API_BATCH_PERFORMANCE='+json.dumps(results,sort_keys=True))
