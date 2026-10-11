"""Native pinned queues and common limiter; explicit overload, sibling recovery."""
import json
import subprocess
import time
import httpx
import pytest
import yaml
from backend.tests.test_m16_collector_resilience import split_trace,merged,send,wait_ready,metric_sum
from backend.tests.test_observability_collector import COLLECTOR,binary,free_port,collector_env
from backend.tests._m1_otel import receiver
from backend.tests.test_m15_collector import traces
from backend.observability.archive import FilesystemArchive
from backend.observability.archive_bridge import local_bridge
from backend.tests._m16_harness import resources

@pytest.mark.parametrize('failed_branch',['cloud','archive'])
def test_native_default_exporter_queue_overload_sibling_survives(tmp_path,failed_branch):
    config=yaml.safe_load((COLLECTOR/'config.local.yaml').read_text())
    for name in ('tail_sampling/cloud','tail_sampling/archive'):config['processors'][name]['decision_wait']='1s'
    # Test-only batch=1 exposes the actual128-request default queue independently
    # of the200-span production batch. Bounds/timeouts/retry policy unchanged.
    config['processors']['batch'].update(send_batch_size=1,send_batch_max_size=1)
    archive=FilesystemArchive(tmp_path/'archive','test')
    original_submit=archive.submit
    unavailable=True
    if failed_branch=='archive':archive.submit=lambda _: (_ for _ in ()).throw(OSError('synthetic unavailable'))
    cases=[split_trace(70000+n,'FAILED') for n in range(200)]
    with receiver(status=lambda:503 if failed_branch=='cloud' and unavailable else 200) as cloud,local_bridge(archive) as bridge:
        path=tmp_path/'collector.yaml';path.write_text(yaml.safe_dump(config))
        port,metric=free_port(),free_port();url=f'http://127.0.0.1:{port}';metrics=f'http://127.0.0.1:{metric}/metrics'
        env=collector_env(OTLP_PORT=str(port),HEALTH_PORT=str(free_port()),SELF_METRICS_PORT=str(metric),LOCAL_SINK_ENDPOINT=cloud.endpoint,ARCHIVE_BRIDGE_ENDPOINT=bridge)
        with (tmp_path/'stderr').open('w') as error:
            process=subprocess.Popen([binary(),'--config='+str(path)],env=env,stdout=subprocess.DEVNULL,stderr=error)
            try:
                wait_ready(process,url);baseline=resources(process.pid);samples=[];queue_peak=0;per_branch_peak=0
                with httpx.Client(timeout=2) as client:
                    for index,case in enumerate(cases):
                        response=client.post(url+'/v1/traces',content=merged(case[1:]).SerializeToString(),headers={'Content-Type':'application/x-protobuf'})
                        assert response.status_code==200
                        if index%10==0:
                            health=client.get(metrics).text;queue_peak=max(queue_peak,metric_sum(health,'exporter_queue_size'))
                            depths=[float(line.split()[-1]) for line in health.splitlines() if line.startswith('otelcol_exporter_queue_size{')]
                            if depths:per_branch_peak=max(per_branch_peak,max(depths))
                            samples.append(resources(process.pid)['rss'])
                        time.sleep(.035)
                time.sleep(4);health=httpx.get(metrics,timeout=2).text
                dropped=metric_sum(health,'enqueue_failed_spans')
                assert dropped>0 and queue_peak<=256 and per_branch_peak<=128
                completed=len(list((tmp_path/'archive'/'test').rglob('*.manifest.json')))
                cloud_spans={ (s.trace_id,s.span_id) for s in traces(cloud.snapshot()) }
                if failed_branch=='archive':assert len(cloud_spans)==400 and completed==0
                else:assert completed==200
                assert max(samples)-baseline['rss']<64*1024*1024
                # Heal the same process/queue, rather than recreating a fresh
                # Collector and calling that exporter recovery.
                unavailable=False;archive.submit=original_submit
                deadline=time.monotonic()+15
                while time.monotonic()<deadline:
                    health=httpx.get(metrics,timeout=2).text
                    if metric_sum(health,'exporter_queue_size')==0:break
                    time.sleep(.1)
                assert metric_sum(health,'exporter_queue_size')==0
                recovery=split_trace(79999,'FAILED')
                send(url,merged(recovery[1:]))
                deadline=time.monotonic()+5
                while time.monotonic()<deadline:
                    if any(recovery[0] in p.parts for p in (tmp_path/'archive'/'test').rglob('*.manifest.json')):break
                    time.sleep(.1)
                assert any(recovery[0] in p.parts for p in (tmp_path/'archive'/'test').rglob('*.manifest.json'))
                print('M16_EXPORTER_SATURATION='+json.dumps(dict(failed_branch=failed_branch,input_traces=200,input_spans=400,
                    queue_capacity_per_branch=128,queue_depth_peak_combined=queue_peak,queue_depth_peak_per_branch=per_branch_peak,
                    recovered_queue_depth=0,recovery_exception_archived=True,explicit_enqueue_failed_spans=dropped,
                    archive_eligible=200,archive_complete=completed,archive_failed_incomplete=200-completed,
                    cloud_unique_received_spans=len(cloud_spans),collector_rss_warm=baseline['rss'],collector_rss_peak=max(samples),
                    classification='INTENTIONAL_EXPORTER_OVERLOAD',business_dispatches=0)))
            finally:process.terminate();process.wait(5)


def test_native_common_memory_limiter_refusal_and_recovery(tmp_path):
    archive=FilesystemArchive(tmp_path/'archive','test')
    with receiver() as cloud,local_bridge(archive) as bridge:
        outcomes=[]
        for limit in (1,160):
            cfg=yaml.safe_load((COLLECTOR/'config.local.yaml').read_text())
            cfg['processors']['memory_limiter'].update(limit_mib=limit,spike_limit_mib=0 if limit==1 else 32)
            for name in ('tail_sampling/cloud','tail_sampling/archive'):cfg['processors'][name]['decision_wait']='1s'
            path=tmp_path/'limiter.yaml';path.write_text(yaml.safe_dump(cfg));port,metric=free_port(),free_port();url=f'http://127.0.0.1:{port}'
            env=collector_env(OTLP_PORT=str(port),HEALTH_PORT=str(free_port()),SELF_METRICS_PORT=str(metric),LOCAL_SINK_ENDPOINT=cloud.endpoint,ARCHIVE_BRIDGE_ENDPOINT=bridge)
            with (tmp_path/'limiter.stderr').open('a') as error:
                process=subprocess.Popen([binary(),'--config='+str(path)],env=env,stdout=subprocess.DEVNULL,stderr=error)
                try:
                    wait_ready(process,url);time.sleep(2)
                    case=split_trace(80000+limit,'FAILED')
                    response=httpx.post(url+'/v1/traces',content=merged(case[1:]).SerializeToString(),headers={'Content-Type':'application/x-protobuf'},timeout=2)
                    time.sleep(3)
                    health=httpx.get(f'http://127.0.0.1:{metric}/metrics',timeout=2).text
                    refused=metric_sum(health,'refused_spans')
                    if limit==1:assert response.status_code!=200 and refused>=2 and not list((tmp_path/'archive'/'test').rglob('*.manifest.json'))
                    else:assert response.status_code==200 and len(list((tmp_path/'archive'/'test').rglob('*.manifest.json')))==1
                    outcomes.append(dict(limiter_mib=limit,http_status=response.status_code,refused_spans=refused,rss=resources(process.pid)['rss']))
                finally:process.terminate();process.wait(5)
        assert len(traces(cloud.snapshot()))==2
        print('M16_COMMON_PRESSURE='+json.dumps(dict(outcomes=outcomes,shared_limiter_couples_both_branches=True,
            supported160mib_recovered=True,business_dispatches=0,loss_claimed_complete=False)))
