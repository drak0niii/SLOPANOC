"""Pinned native Collector first blocking gate: terminal evidence after early children."""
import json
import os
from pathlib import Path
import subprocess
import time

import httpx
import pytest
import yaml
from backend.tests._m1_otel import receiver
from backend.tests.test_observability_collector import COLLECTOR, binary, free_port, collector_env
from backend.tests.test_m15_archive import request
from backend.tests.test_m15_collector import traces
from backend.observability.archive import FilesystemArchive, attrs, set_attrs, sanitize_request
from backend.observability.archive_bridge import local_bridge


def send(endpoint, value):
    response = httpx.post(endpoint+'/v1/traces', content=value.SerializeToString(),
                          headers={'Content-Type': 'application/x-protobuf'}, timeout=2)
    assert response.status_code == 200


def split_trace(n, cls):
    value = sanitize_request(request(cls).SerializeToString(), 'test')
    root, child = value.resource_spans[0].scope_spans[0].spans
    tid = format(n, '032x')
    root.trace_id = child.trace_id = bytes.fromhex(tid)
    del root.links[:]
    del child.links[:]
    fields = attrs(root.attributes)
    ids = fields['slopanoc.archive.expected_span_ids']
    fields['slopanoc.archive.expected_span_ids'] = ','.join(ids) if isinstance(ids, (tuple, list)) else ids
    fields['slopanoc.status'] = 'COMPLETED' if cls in ('HEALTHY', 'EVER_STALLED', 'GOVERNED') else cls
    set_attrs(root.attributes, fields)
    early = type(value)(); early.CopyFrom(value)
    del early.resource_spans[0].scope_spans[0].spans[:1]
    late = type(value)(); late.CopyFrom(value)
    del late.resource_spans[0].scope_spans[0].spans[1:]
    return tid, early, late


def wait_ready(process, endpoint):
    limit = time.monotonic()+10
    while time.monotonic() < limit:
        assert process.poll() is None
        try:
            httpx.get(endpoint, timeout=.2)
            return
        except httpx.TransportError:
            time.sleep(.02)
    pytest.fail('Local Collector did not start')


@pytest.mark.parametrize('case',['repaired','unsafe_previous_window'])
def test_late_exceptional_first_blocking_gate(tmp_path,case):
    config = yaml.safe_load((COLLECTOR/'config.local.yaml').read_text())
    # DEFAULT configured decision timing, not an invented future processor feature.
    default = config['processors']['tail_sampling/cloud']['decision_wait']
    configured = float(default.split(':-')[1].rstrip('}s'))
    assert configured == 240
    seconds = (configured if os.environ.get('SLOPANOC_M16_FULL_WINDOW') == '1' else 10) if case == 'repaired' else 2
    for name in ('tail_sampling/cloud', 'tail_sampling/archive'):
        config['processors'][name]['decision_wait'] = str(seconds)+'s'
    config['processors']['tail_sampling/cloud']['policies'][-1]['probabilistic']['sampling_percentage'] = 10
    archive = FilesystemArchive(tmp_path/'archive', 'test')
    cases = [split_trace(i+100, cls) for i, cls in enumerate(
        ('FAILED', 'TIMEOUT', 'EVER_STALLED') + ('HEALTHY',)*20)]
    with receiver() as cloud, local_bridge(archive) as bridge:
        path = tmp_path/'collector.yaml'; path.write_text(yaml.safe_dump(config))
        port, metric = free_port(), free_port()
        endpoint = f'http://127.0.0.1:{port}'
        env = collector_env(OTLP_PORT=str(port), HEALTH_PORT=str(free_port()), SELF_METRICS_PORT=str(metric),
                            LOCAL_SINK_ENDPOINT=cloud.endpoint, ARCHIVE_BRIDGE_ENDPOINT=bridge)
        with (tmp_path/'collector.stderr').open('w+') as error:
            process = subprocess.Popen([binary(), '--config='+str(path)], env=env,
                                       stdout=subprocess.DEVNULL, stderr=error)
            try:
                wait_ready(process, endpoint)
                for tid, early, late in cases:
                    send(endpoint, early)
                # Longer than original local2s window; shorter than repaired default.
                time.sleep(180 if seconds == 240 else 4)
                for tid, early, late in cases:
                    send(endpoint, late)
                end = time.monotonic()+seconds+5
                while time.monotonic() < end:
                    if len(list((tmp_path/'archive'/'test').rglob('*.manifest.json'))) == 3:
                        break
                    time.sleep(.1)
                seen = traces(cloud.snapshot())
                observed = {s.trace_id.hex() for s in seen}
                manifests = list((tmp_path/'archive'/'test').rglob('*.manifest.json'))
                metrics = httpx.get(f'http://127.0.0.1:{metric}/metrics', timeout=2).text
                report = {'decision_wait_seconds': seconds, 'traces':len(cases), 'spans':46,
                          'exceptional_complete':len(manifests), 'cloud_traces':len(observed),
                          'late_after_original_window_seconds':180 if seconds == 240 else 4,
                          'cloud_span_names':[s.name for s in seen],
                          'cloud_classes':[attrs(s.attributes).get('slopanoc.trace_class') for s in seen]}
                
                print('M16_TAIL_GATE='+json.dumps(report))
                if case == 'unsafe_previous_window':
                    assert len(manifests) == 0
                    return
                assert len(manifests) == 3, 'late exceptional traces lost: ALWAYS_KEEP broken'
                for tid, _, _ in cases[:3]:
                    assert tid in observed
                    assert len([s for s in seen if s.trace_id.hex() == tid]) == 2
                assert not any((tmp_path/'archive'/'test'/tid).exists() for tid,_,_ in cases[3:])
                assert len(observed) < len(cases), 'long healthy traces must remain sampleable'
                assert 'sampling_trace_dropped_too_early' in metrics or 'tail_sampling' in metrics
            finally:
                process.terminate(); process.wait(5)

from contextlib import contextmanager
from backend.tests._m16_harness import resources, envelope
from opentelemetry.proto.collector.trace.v1.trace_service_pb2 import ExportTraceServiceRequest

@contextmanager
def native(tmp_path, archive, cloud, *, wait=3, capacity=256, cache=0):
    config = yaml.safe_load((COLLECTOR/'config.local.yaml').read_text())
    for name in ('tail_sampling/cloud', 'tail_sampling/archive'):
        config['processors'][name].update(decision_wait=f'{wait}s', num_traces=capacity)
        if cache:
            config['processors'][name]['decision_cache'] = {'sampled_cache_size':cache, 'non_sampled_cache_size':cache}
    config['processors']['tail_sampling/cloud']['policies'][-1]['probabilistic']['sampling_percentage'] = 10
    config['service']['telemetry']['metrics']['level']='detailed'
    path=tmp_path/'native.yaml'; path.write_text(yaml.safe_dump(config))
    metric,port=free_port(),free_port()
    with local_bridge(archive) as bridge, (tmp_path/'stderr.log').open('w+') as error:
        env=collector_env(OTLP_PORT=str(port), HEALTH_PORT=str(free_port()), SELF_METRICS_PORT=str(metric),
                          LOCAL_SINK_ENDPOINT=cloud.endpoint, ARCHIVE_BRIDGE_ENDPOINT=bridge)
        process=subprocess.Popen([binary(),'--config='+str(path)],env=env,stdout=subprocess.DEVNULL,stderr=error)
        endpoint=f'http://127.0.0.1:{port}'
        try:
            wait_ready(process,endpoint)
            yield process,endpoint,f'http://127.0.0.1:{metric}/metrics'
        finally:
            process.terminate(); process.wait(5)

def merged(values):
    result=ExportTraceServiceRequest()
    for value in values:
        result.resource_spans.extend(value.resource_spans)
    return result

def metric_sum(text, name):
    return sum(float(line.rsplit(' ',1)[1]) for line in text.splitlines()
               if not line.startswith('#') and name in line.split(' ',1)[0] and not line.split(' ',1)[0].endswith(('_count','_sum')))

@pytest.mark.parametrize('capacity,count',[(256,128),(256,256),(16,24)])
def test_tail_pending_burst_and_early_eviction_boundary(tmp_path,capacity,count):
    archive=FilesystemArchive(tmp_path/'archive','test')
    cases=[split_trace(n+1000,'FAILED') for n in range(count)]
    with receiver() as cloud,native(tmp_path,archive,cloud,wait=3,capacity=capacity) as (process,url,metrics):
        warmed=resources(process.pid); start=time.monotonic()
        send(url,merged([e for _,e,_ in cases]))
        pending=httpx.get(metrics,timeout=2).text
        print('M16_PENDING_METRIC_NAMES='+json.dumps(sorted({line.split('{',1)[0].split(' ',1)[0] for line in pending.splitlines() if not line.startswith('#') and ('tail_sampling' in line or 'queue' in line)})))
        rss=resources(process.pid)
        time.sleep(.5)
        send(url,merged([r for _,_,r in cases]))
        end=time.monotonic()+8
        while time.monotonic()<end and len(list((tmp_path/'archive'/'test').rglob('*.manifest.json')))<min(count,capacity):
            time.sleep(.1)
        complete=len(list((tmp_path/'archive'/'test').rglob('*.manifest.json')))
        final=httpx.get(metrics,timeout=2).text
        evicted=metric_sum(final,'sampling_trace_dropped_too_early')
        envelope('collector',[time.monotonic()-start],decision_wait=3,num_traces=capacity,offered_traces=count,
                 spans=count*2,archive_complete=complete,evictions=evicted,
                 rss_startup=warmed['rss'],rss_after_ingress_sample=rss['rss'],
                 resources_warm=warmed,resources_pending=rss,resources_settled=resources(process.pid),
                 pending_per_branch=(metric_sum(pending,'new_trace_id_received')-metric_sum(pending,'sampling_trace_dropped_too_early'))/2,
                 pending_measurement='native new trace IDs minus early evictions before first decision; two independent branches',
                 cloud_retained_spans=len(traces(cloud.snapshot())),archive_eligible=count,archive_failed_incomplete=count-complete,
                 bridge_rss_in_process=resources()['rss'],queue_depth=metric_sum(final,'exporter_queue_size'),
                 local_synthetic=True)
        if count<=capacity:
            assert complete==count and evicted==0
        else:
            assert evicted>0 and complete<count # truthful pressure boundary, never false full success
        assert rss['rss']-warmed['rss']<64*1024*1024

@pytest.mark.parametrize('cache',[0,128])
def test_late_span_inherits_drop_and_keep_decisions(tmp_path,cache):
    archive=FilesystemArchive(tmp_path/'archive','test')
    early_cases=[split_trace(n+3000,'HEALTHY') for n in range(20)]
    kept=split_trace(4000,'FAILED')
    with receiver() as cloud,native(tmp_path,archive,cloud,wait=1,capacity=64,cache=cache) as (_,url,metrics):
        send(url,merged([early for _,early,_ in early_cases]))
        send(url,merged([kept[1],kept[2]]))
        time.sleep(3)
        # Deliberately unsafe fixture: future exception after a completed DROP cannot revise it.
        send(url,merged([split_trace(n+3000,'FAILED')[2] for n in range(20)]))
        send(url,kept[1])
        time.sleep(2)
        seen=traces(cloud.snapshot())
        assert len([s for s in seen if s.trace_id.hex()==kept[0]])>=3
        assert len(list((tmp_path/'archive'/'test').rglob('*.manifest.json')))==1
        print('M16_CACHE='+json.dumps({'cache_size':cache,'late_drop_not_reclassified':True,'late_keep_forwarded':True}))

@pytest.mark.parametrize('failed_branch',['cloud','archive'])
def test_independent_exporter_outage(tmp_path,failed_branch):
    archive=FilesystemArchive(tmp_path/'archive','test')
    if failed_branch=='archive':
        archive.submit=lambda _: (_ for _ in ()).throw(OSError('synthetic unavailable'))
    with receiver(status=503 if failed_branch=='cloud' else 200) as cloud,native(tmp_path,archive,cloud,wait=1) as (_,url,metrics):
        case=split_trace(9000,'FAILED');send(url,merged(case[1:]))
        time.sleep(4)
        if failed_branch=='cloud':assert len(list((tmp_path/'archive'/'test').rglob('*.manifest.json')))==1
        else:assert len(traces(cloud.snapshot()))==2 and not list((tmp_path/'archive'/'test').rglob('*.manifest.json'))
        health=httpx.get(metrics,timeout=2).text
        assert 'exporter' in health

@pytest.mark.parametrize('repeat',range(3))
def test_restart_volatile_loss_stays_partial_then_byte_retry_recovers(tmp_path,repeat):
    archive=FilesystemArchive(tmp_path/'archive','test');case=split_trace(9991,'FAILED')
    with receiver() as cloud:
        with native(tmp_path,archive,cloud,wait=1) as (process,url,_):
            send(url,case[1]);process.kill();process.wait(5)
        archive=FilesystemArchive(tmp_path/'archive','test')
        assert not list((tmp_path/'archive'/'test').rglob('*.manifest.json'))
        with native(tmp_path,archive,cloud,wait=1) as (_,url,_):
            send(url,case[2]);time.sleep(3)
            assert not list((tmp_path/'archive'/'test').rglob('*.manifest.json'))
            # Retry telemetry bytes only; neither process has business dispatch capability.
            send(url,case[1]);time.sleep(2)
            assert len(list((tmp_path/'archive'/'test').rglob('*.manifest.json')))==1
        reopened=FilesystemArchive(tmp_path/'archive','test')
        result,=reopened.submit(merged(case[1:]).SerializeToString())
        assert result.duplicate and result.state=='COMPLETE'
        print('M16_RESTART='+json.dumps({'repeat':repeat,'sequence':'collector_hard_kill_bridge_graceful_reopen','volatile_child_loss':'PARTIAL','byte_retry':'COMPLETE','business_dispatches':0}))

def test_sampling_window_derivation_rejects_unsafe_overrides():
    from scripts.observability.m16_sampling_preflight import evaluate
    from backend.observability.config import ObservabilityConfig
    result=evaluate()
    assert result['required_window_seconds']==212 and result['required_population']==230
    assert result['pass_local_planning']
    assert not evaluate(decision_wait=2)['pass_local_planning']
    assert not evaluate(ObservabilityConfig(turn_timeout_seconds=300))['pass_local_planning']
    assert not evaluate(traces_per_second=2)['pass_local_planning']
    print('M16_WINDOW='+json.dumps(result))

@pytest.mark.skipif(os.environ.get('SLOPANOC_M16_FULL_WINDOW')!='1',reason='explicit full-window local profile')
def test_full_window_pending_pressure(tmp_path):
    archive=FilesystemArchive(tmp_path/'archive','test')
    cases=[split_trace(n+20000,('FAILED','TIMEOUT','EVER_STALLED','GOVERNED')[n%4]) for n in range(128)]
    healthy=[split_trace(n+21000,'HEALTHY') for n in range(20)]
    all_cases=cases+healthy
    with receiver() as cloud,native(tmp_path,archive,cloud,wait=240) as (process,url,metrics):
        baseline=resources(process.pid);began=time.monotonic()
        send(url,merged([e for _,e,_ in all_cases]));
        ingress=httpx.get(metrics,timeout=2).text
        samples=[baseline['rss']]
        until=began+180
        while time.monotonic()<until:
            samples.append(resources(process.pid)['rss']);time.sleep(min(1,max(0,until-time.monotonic())))
        resident=resources(process.pid)
        send(url,merged([r for _,_,r in all_cases]))
        end=began+260
        while time.monotonic()<end and len(list((tmp_path/'archive'/'test').rglob('*.manifest.json')))<128:time.sleep(.2)
        complete=len(list((tmp_path/'archive'/'test').rglob('*.manifest.json')))
        assert complete==128
        seen=traces(cloud.snapshot());exceptional=[s for s in seen if int.from_bytes(s.trace_id,'big')<21000]
        assert len({s.trace_id for s in exceptional})==128 and len(exceptional)==256
        assert len({s.trace_id for s in seen})<148
        assert all(not (tmp_path/'archive'/'test'/tid).exists() for tid,_,_ in healthy)
        assert metric_sum(httpx.get(metrics,timeout=2).text,'sampling_trace_dropped_too_early')==0
        print('M16_FULL_PRESSURE='+json.dumps({'seconds':time.monotonic()-began,'decision_wait':240,
              'terminal_after_seconds':180,'traces':128,'complete':complete,'cloud_spans':len(seen),
              'rss_warm':baseline['rss'],'rss_resident':resident['rss'],'rss_peak':max(samples),'num_traces':256,
              'incoming_traces':148,'incoming_spans':296,'exceptional_classes':{'FAILED':32,'TIMEOUT':32,'EVER_STALLED':32,'GOVERNED':32},
              'healthy_long':20,'pending_per_branch':metric_sum(ingress,'new_trace_id_received')/2,
              'evictions':0,'archive_eligible':128,'archive_failed_incomplete':0,'queue_depth':metric_sum(httpx.get(metrics,timeout=2).text,'exporter_queue_size'),
              'bridge_in_process_rss':resources()['rss'],'resource_samples':len(samples),'business_dispatches':0}))
