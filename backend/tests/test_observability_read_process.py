"""Two real local processes: terminate/slow/burst/restart reader independently."""
import asyncio
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import time
import httpx
import pytest
from backend.tests._m7_storage import storage,state


def free_port():
    with socket.socket() as s:s.bind(('127.0.0.1',0));return s.getsockname()[1]

async def ready(client,base,process):
    start=time.perf_counter()
    for _ in range(120):
        if process.poll() is not None:
            raise AssertionError('Owned fixture process failed startup: '+process.fixture_stderr.read_text()[-2000:])
        try:
            if (await client.get(base+'/health')).status_code==200:return (time.perf_counter()-start)*1000
        except httpx.TransportError:pass
        await asyncio.sleep(.1)
    raise AssertionError('Owned fixture startup deadline')

@pytest.mark.asyncio
async def test_separate_process_failure_restart_burst_and_measurements(tmp_path):
    root=tmp_path/'reader-process-isolated';root.mkdir();(root/'disposable.marker').touch()
    (root/'auth.json').write_text(json.dumps({'default_subject':'api-user','users':{
        'api-user':{'role':'Operator','capabilities':['own_progress','operational_metadata'],
        'environments':['development']}}}))
    db,repo=await storage(root/'data.sqlite');await repo.persist(state());await db.close()
    child=Path(__file__).with_name('_observability_plane_child.py')
    processes=[]
    def spawn(mode,port):
        # Children get only this disposable fixture binding, never inherited runtime URLs.
        env={k:v for k,v in os.environ.items() if not k.startswith(('SLOPANOC_','GOOGLE_','GCLOUD_','GCP_'))}
        # Preserve the supervisor's loopback-only guard activation in owned children.
        if os.environ.get('SLOPANOC_M16_LOCAL_TEST') == '1':env['SLOPANOC_M16_LOCAL_TEST']='1'
        env['PYTHONPATH']=str(Path(__file__).resolve().parents[2])+os.pathsep+env.get('PYTHONPATH','')
        # Drain child diagnostics into the disposable fixture directory: an
        # unread stderr pipe can block the service during the 30-request burst.
        stderr_path=root/f'{mode}-{len(processes)}.stderr'
        with stderr_path.open('wb') as stderr_file:
            p=subprocess.Popen([sys.executable,str(child),mode,str(port),str(root)],env=env,
                stdout=subprocess.DEVNULL,stderr=stderr_file)
        p.fixture_stderr=stderr_path
        processes.append(p);return p
    business_port,reader_port=free_port(),free_port()
    business_base=f'http://127.0.0.1:{business_port}';reader_base=f'http://127.0.0.1:{reader_port}'
    measurements={}
    try:
        anoc=spawn('anoc',business_port);reader=spawn('read',reader_port)
        async with httpx.AsyncClient(timeout=15) as client:
            business_startup,reader_startup=await asyncio.gather(ready(client,business_base,anoc),ready(client,reader_base,reader))
            identity=(await client.get(business_base+'/_fixture/identity')).json()
            assert identity=={'subject':'api-user'}
            assert (await client.get(reader_base+'/_fixture/identity')).json()==identity
            access=(await client.get(reader_base+'/api/observability/access')).json()
            assert access=={'verified':True,'capabilities':['operational_metadata','own_progress'],
                'environments':['development']}
            assert (await client.get(reader_base+'/api/observability/finops/runtime-usage')).status_code==403
            assert (await client.post(reader_base+'/api/observability/sse-receipts',json={})).status_code==405
            measurements['shared_development_identity']=True
            measurements['anoc_startup_ms']=business_startup
            measurements['reader_startup_ms']=reader_startup
            async def business():
                start=time.perf_counter();r=await client.post(business_base+'/api/sessions')
                assert r.status_code==201,r.text
                return (time.perf_counter()-start)*1000
            measurements['business_idle_ms']=[await business() for _ in range(5)]
            start=time.perf_counter();r=await client.get(reader_base+'/api/observability/runs')
            assert r.status_code==200 and len(r.json()['items'])==1
            measurements['trace_first_load_ms']=(time.perf_counter()-start)*1000
            measurements['cached_trace_get_ms']=[]
            for _ in range(5):
                start=time.perf_counter();assert (await client.get(reader_base+'/api/observability/runs')).status_code==200
                measurements['cached_trace_get_ms'].append((time.perf_counter()-start)*1000)
            # Slow reader requests are independently cancellable at the client deadline.
            slow=asyncio.create_task(client.get(reader_base+'/api/observability/runs',headers={'X-Fixture-Slow':'true'},timeout=.05))
            measurements['business_slow_reader_ms']=await business()
            with pytest.raises(httpx.TimeoutException):await slow
            start=time.perf_counter()
            burst=[asyncio.create_task(client.get(reader_base+'/api/observability/runs')) for _ in range(30)]
            measurements['business_during_burst_ms']=[await business() for _ in range(5)]
            results=await asyncio.gather(*burst)
            assert all(r.status_code in (200,429,503) for r in results)
            measurements['read_burst_ms']=(time.perf_counter()-start)*1000
            measurements['read_burst_statuses']={str(code):sum(r.status_code==code for r in results) for code in (200,429,503)}
            stats=(await client.get(reader_base+'/_fixture/stats')).json()
            assert stats['pid']==reader.pid and all(v==0 for v in stats['counts'].values())
            reader.terminate();reader.wait(timeout=10)
            with pytest.raises(httpx.TransportError):await client.get(reader_base+'/health')
            assert anoc.poll() is None;measurements['business_reader_down_ms']=await business()
            reader=spawn('read',reader_port)
            measurements['reader_restart_ms']=await ready(client,reader_base,reader)
            assert (await client.get(reader_base+'/api/observability/runs')).status_code==200
            assert (await client.get(reader_base+'/api/observability/access')).json()==access
            assert (await client.get(reader_base+'/_fixture/identity')).json()==identity
            assert anoc.poll() is None
            anoc_stats=(await client.get(business_base+'/_fixture/stats')).json()
            reader_stats=(await client.get(reader_base+'/_fixture/stats')).json()
            assert anoc_stats['pid']==anoc.pid and reader_stats['pid']!=anoc.pid
            assert all(v==0 for v in anoc_stats['counts'].values()) and all(v==0 for v in reader_stats['counts'].values())
            measurements['dispatch_counts']=anoc_stats['counts'];measurements['separate_pids']=True
        print('OBSERVABILITY_PROCESS_MEASUREMENTS='+json.dumps(measurements,sort_keys=True))
    finally:
        for p in processes:
            if p.poll() is None:p.terminate()
            try:p.wait(timeout=10)
            except subprocess.TimeoutExpired:p.kill();p.wait(timeout=5)
