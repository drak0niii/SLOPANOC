"""Distinct hard-crash orders of actual owned backend, Collector and bridge PIDs."""
import asyncio
from collections import Counter
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import httpx
import pytest
import yaml
from backend.tests._m1_otel import receiver
from backend.tests.test_observability_collector import COLLECTOR,binary,free_port,collector_env
from backend.tests.test_m16_collector_resilience import wait_ready,split_trace,merged,send
from backend.observability.archive import FilesystemArchive
from backend.observability.database import Database
from backend.observability.finops.repository import Repository
from backend.observability.finops.usage_ledger import Ledger
from backend.observability.config import ObservabilityConfig

BRIDGE_CODE='''
import time,sys
from pathlib import Path
from backend.observability.archive import FilesystemArchive
from backend.observability.archive_bridge import local_bridge
with local_bridge(FilesystemArchive(sys.argv[1],'test')) as endpoint:
 Path(sys.argv[2]).write_text(endpoint)
 while True:time.sleep(.1)
'''

def until(predicate,seconds=10):
    deadline=time.monotonic()+seconds
    while time.monotonic()<deadline:
        if predicate():return
        time.sleep(.02)
    raise AssertionError('Owned process boundary not reached within bound')

@pytest.mark.parametrize('order',[('backend','collector'),('collector','backend'),('collector','bridge'),('bridge','collector'),('backend_bridge',),('collector_bridge',)])
def test_owned_combined_hard_crash_orders(tmp_path,order):
    processes={};handles=[];archive_root=tmp_path/'archive';root=tmp_path/'backend';root.mkdir()
    endpoint_file=tmp_path/'bridge_endpoint'
    def bridge():
        endpoint_file.unlink(missing_ok=True)
        error=(tmp_path/'bridge.stderr').open('a');handles.append(error)
        p=subprocess.Popen([sys.executable,'-c',BRIDGE_CODE,str(archive_root),str(endpoint_file)],stdout=subprocess.DEVNULL,stderr=error)
        processes['bridge']=p;until(lambda:endpoint_file.exists() or p.poll() is not None)
        assert p.poll() is None
        return endpoint_file.read_text()
    def collector(cloud,bridge_url):
        cfg=yaml.safe_load((COLLECTOR/'config.local.yaml').read_text())
        for name in ('tail_sampling/cloud','tail_sampling/archive'):cfg['processors'][name]['decision_wait']='3s'
        path=tmp_path/'collector.yaml';path.write_text(yaml.safe_dump(cfg));port,metric=free_port(),free_port()
        env=collector_env(OTLP_PORT=str(port),HEALTH_PORT=str(free_port()),SELF_METRICS_PORT=str(metric),LOCAL_SINK_ENDPOINT=cloud.endpoint,ARCHIVE_BRIDGE_ENDPOINT=bridge_url)
        error=(tmp_path/'collector.stderr').open('a');handles.append(error)
        p=subprocess.Popen([binary(),'--config='+str(path)],env=env,stdout=subprocess.DEVNULL,stderr=error);processes['collector']=p
        url=f'http://127.0.0.1:{port}';wait_ready(p,url);return url
    def kill(name):
        p=processes[name]
        if p.poll() is None:p.kill()
        p.wait(5)
    try:
        with receiver() as cloud:
            url_bridge=bridge();url=collector(cloud,url_bridge)
            retained=split_trace(91000,'FAILED');partial=split_trace(91001,'FAILED')
            # Actual durable bridge commit precedes crash; immutable retry is verified later.
            response=httpx.post(url_bridge+'/v1/traces',content=merged(retained[1:]).SerializeToString(),headers={'Content-Type':'application/x-protobuf'},timeout=2)
            assert response.status_code==200
            send(url,partial[1])
            error=(tmp_path/'backend.stderr').open('a');handles.append(error)
            p=subprocess.Popen([sys.executable,str(Path(__file__).with_name('_m16_backend_child.py')),str(root),'hold',url],stdout=subprocess.DEVNULL,stderr=error)
            processes['backend']=p;until(lambda:(root/'ready').exists() or p.poll() is not None,15)
            assert p.poll() is None
            before=(root/'dispatch').read_bytes();counts=Counter(before.decode().splitlines())
            assert counts=={'agent':1,'provider':1,'tool':1}
            from backend.tests._m16_harness import resources
            process_resources_before={name:resources(child.pid) for name,child in processes.items()}
            backend_internal=json.loads((root/'ready').read_text())
            assert backend_internal['db_checked_out']==backend_internal['session_db_checked_out']==0
            assert backend_internal['trace_queue']==backend_internal['log_queue']==0
            for name in order:
                if name in ('backend_bridge','collector_bridge'):
                    components=('backend','bridge') if name=='backend_bridge' else ('collector','bridge')
                    for component in components:
                        if processes[component].poll() is None:processes[component].kill()
                    for component in components:processes[component].wait(5)
                else:kill(name)
            # Stop remaining owned processes, then fresh readers/exporters only.
            for name in ('backend','collector','bridge'):kill(name)
            url_bridge=bridge();url=collector(cloud,url_bridge)
            reopened=FilesystemArchive(archive_root,'test')
            committed=list((archive_root/'test'/retained[0]).glob('*.manifest.json'))
            assert len(committed)==1
            preserved=committed[0].read_bytes()
            send(url,partial[2]);time.sleep(5)
            assert not (archive_root/'test'/partial[0]/'manifest.json').exists()
            assert len(list((archive_root/'test').rglob('*.manifest.json')))==1
            send(url,partial[1]);time.sleep(2)
            until(lambda:len(list((archive_root/'test').rglob('*.manifest.json')))==2)
            result,=reopened.submit(merged(retained[1:]).SerializeToString());assert result.duplicate
            assert committed[0].read_bytes()==preserved
            async def recovery():
                db=Database('sqlite+aiosqlite:///'+str(root/'state.db'),isolated_test=True)
                try:
                    repo=Repository(db.sessions);ledger=Ledger(repo,ObservabilityConfig())
                    assert await ledger.recover()==0 and (await repo.health('development'))['debt']==0
                finally:await db.close()
            asyncio.run(recovery())
            assert (root/'dispatch').read_bytes()==before
            process_resources_after={name:resources(processes[name].pid) for name in ('collector','bridge')}
            assert all(value['fds'] is not None and value['sockets'] is not None
                       for value in (*process_resources_before.values(),*process_resources_after.values()))
            print('M16_COMBINED_CRASH='+json.dumps(dict(order=list(order),actual_owned_processes=3,
                dispatches=dict(counts),business_replayed=0,previous_manifest_immutable=True,late_root_partial_then_byte_retry_complete=True,
                process_resources_before=process_resources_before,process_resources_after=process_resources_after,
                backend_internal_resources=backend_internal,archive_completed_versions=2,
                archive_staging_chunks=len(list((archive_root/'.staging').rglob('*.chunk'))),
                owned_temporary_files=sum(path.is_file() for path in tmp_path.rglob('*')))))
    finally:
        for p in processes.values():
            if p.poll() is None:p.kill()
            p.wait(5)
        for handle in handles:handle.close()
