"""Actual pinned Collector common-redaction and independent branch evidence."""
import pytest
import subprocess
import time
import socket
import httpx
import yaml
from opentelemetry.proto.collector.trace.v1.trace_service_pb2 import ExportTraceServiceRequest
from backend.tests._m1_otel import receiver
from backend.tests.test_observability_collector import COLLECTOR,binary,free_port,collector_env
from backend.tests.test_m15_archive import request,MARKERS,TID,ROOT
from backend.observability.archive import attrs,set_attrs,FilesystemArchive
from backend.observability.archive_bridge import local_bridge

def traces(records):
    result=[]
    for path,data,_ in records:
        if path!='/v1/traces': continue
        value=ExportTraceServiceRequest();value.ParseFromString(data)
        result.extend(s for r in value.resource_spans for scope in r.scope_spans for s in scope.spans)
    return result

@pytest.mark.parametrize("healthy,down",[(100,False),(10,False),(100,True),("invalid",False)])
def test_actual_common_redaction_then_independent_healthy_exceptional_routing(tmp_path,healthy,down):
    config=yaml.safe_load((COLLECTOR/'config.local.yaml').read_text())
    from backend.observability.sampling_policy import collector_environment
    resolved=collector_environment({'HEALTHY_TRACE_PERCENT':healthy})
    healthy=float(resolved['HEALTHY_TRACE_PERCENT'])
    config['processors']['tail_sampling/cloud']['policies'][-1]['probabilistic']['sampling_percentage']=healthy
    archive=FilesystemArchive(tmp_path/'archive','test')
    if down:
        def unavailable(_data):raise OSError('synthetic local sink unavailable')
        archive.submit=unavailable
    with receiver() as cloud,receiver() as before,local_bridge(archive) as bridge:
        config['exporters']['otlphttp/common-test']={'endpoint':before.endpoint}
        config['service']['pipelines']['traces']['exporters'].append('otlphttp/common-test')
        path=tmp_path/'collector.yaml';path.write_text(yaml.safe_dump(config))
        port,health,metric=free_port(),free_port(),free_port()
        with (tmp_path/'stderr.log').open('w+') as errors:
            process=subprocess.Popen([binary(),'--config='+str(path)],stdout=subprocess.DEVNULL,stderr=errors,
                env=collector_env(OTLP_PORT=str(port),HEALTH_PORT=str(health),SELF_METRICS_PORT=str(metric),
                    LOCAL_SINK_ENDPOINT=cloud.endpoint,ARCHIVE_BRIDGE_ENDPOINT=bridge,TAIL_DECISION_WAIT='1s',HEALTHY_TRACE_PERCENT=str(healthy)))
            try:
                deadline=time.monotonic()+10
                while time.monotonic()<deadline:
                    with socket.socket() as sock:
                        if sock.connect_ex(('127.0.0.1',port))==0: break
                    assert process.poll() is None
                    time.sleep(.02)
                expected=[]; ingress_timings=[]; started=time.perf_counter(); input_bytes=0
                for n,cls in enumerate(('HEALTHY',)*20+('SAFETY','GOVERNED','FAILED','TIMEOUT','EVER_STALLED')):
                    value=request(cls);tid=format(n+100,'032x');expected.append(tid)
                    for r in value.resource_spans:
                        r.schema_url=MARKERS[9]
                        fields=attrs(r.resource.attributes)
                        fields.update({'service.version':MARKERS[0], 'slopanoc.release_id':MARKERS[1], 'cloud.region':MARKERS[2], 'slopanoc.git_sha':MARKERS[3], 'slopanoc.cloud_run_revision':MARKERS[4]})
                        set_attrs(r.resource.attributes,fields)
                        for scope in r.scope_spans:
                            scope.schema_url=MARKERS[9]
                            scope.scope.version=MARKERS[7]
                            set_attrs(scope.scope.attributes,{'prompt':MARKERS[0]})
                            for span in scope.spans:
                                span.trace_id=bytes.fromhex(tid);del span.links[:]
                                fields=attrs(span.attributes)
                                if 'slopanoc.archive.expected_span_ids' in fields:
                                    fields['slopanoc.archive.expected_span_ids']=','.join(fields['slopanoc.archive.expected_span_ids'])
                                if cls in ('HEALTHY','EVER_STALLED','GOVERNED'):fields['slopanoc.status']='COMPLETED'
                                set_attrs(span.attributes,fields)
                    input_bytes+=len(value.SerializeToString()); ingress_start=time.perf_counter()
                    response=httpx.post(f'http://127.0.0.1:{port}/v1/traces',content=value.SerializeToString(),
                        headers={'Content-Type':'application/x-protobuf'},timeout=2)
                    ingress_timings.append((time.perf_counter()-ingress_start)*1000)
                    assert response.status_code==200
                deadline=time.monotonic()+8
                while time.monotonic()<deadline:
                    if {s.trace_id.hex() for s in traces(cloud.snapshot())} >= set(expected[20:]) and (down or len(list((tmp_path/'archive').rglob('*.manifest.json')))==5):
                        time.sleep(.5);break
                    time.sleep(.05)
                observed={s.trace_id.hex() for s in traces(cloud.snapshot())}
                assert set(expected[20:])<=observed
                if healthy==100:assert observed==set(expected)
                else:assert 5<=len(observed)<15
                assert len({s.trace_id.hex() for s in traces(before.snapshot())})==25
                manifests=list((tmp_path/'archive').rglob('*.manifest.json'))
                assert len(manifests)==(0 if down else 5)
                assert not (tmp_path/'archive'/'test'/expected[0]).exists()
                safe_before=b''.join(data for _,data,_ in before.snapshot())
                assert safe_before and all(marker.encode() not in safe_before for marker in MARKERS)
                import json,statistics
                print('M15_LOCAL_COLLECTOR_PERFORMANCE='+json.dumps({'healthy_percent':healthy,'sink_down':down,'traces':25,'spans':50,'input_bytes':input_bytes,'ingress_mean_ms':statistics.mean(ingress_timings),'ingress_max_ms':max(ingress_timings),'redacted_branches_batch_elapsed_ms':(time.perf_counter()-started)*1000}))
                # Exercise M15 metric/export privacy on the actual native pipeline.
                from opentelemetry.proto.collector.metrics.v1.metrics_service_pb2 import ExportMetricsServiceRequest
                message=ExportMetricsServiceRequest();resource=message.resource_metrics.add()
                set_attrs(resource.resource.attributes,{'service.name':'slopanoc','deployment.environment.name':'test','slopanoc.telemetry_schema_version':'1'})
                scope=resource.scope_metrics.add();scope.scope.name='slopanoc.observability'
                metric=scope.metrics.add();metric.name='slopanoc.archive.eligible';metric.unit='1'
                metric.sum.is_monotonic=True;metric.sum.aggregation_temporality=2
                point=metric.sum.data_points.add();point.as_int=1;point.time_unix_nano=1000
                set_attrs(point.attributes,{'environment':'test','operation':'projection','status':'RUNNING','window':'event'})
                poison=metric.sum.data_points.add();poison.CopyFrom(point)
                set_attrs(poison.attributes,{'environment':'test','operation':'projection','status':'RUNNING','window':'event','run_id':MARKERS[0]})
                extra=metric.sum.data_points.add();extra.CopyFrom(poison)
                exemplar=poison.exemplars.add();exemplar.as_int=1;exemplar.trace_id=bytes.fromhex(TID);exemplar.span_id=bytes.fromhex(ROOT)
                set_attrs(exemplar.filtered_attributes,{'secret':MARKERS[7]})
                assert httpx.post(f'http://127.0.0.1:{port}/v1/metrics',content=message.SerializeToString(),headers={'Content-Type':'application/x-protobuf'},timeout=2).status_code==200
                deadline=time.monotonic()+4;points=[]
                while time.monotonic()<deadline:
                    points=[p for path,data,_ in cloud.snapshot() if path=='/v1/metrics'
                        for r in ExportMetricsServiceRequest.FromString(data).resource_metrics for scope in r.scope_metrics
                        for m in scope.metrics if m.name=='slopanoc.archive.eligible' for p in m.sum.data_points]
                    if points:break
                    time.sleep(.05)
                assert len(points)==1 and points[0].as_int==1 and not points[0].exemplars
                assert {a.key for a in points[0].attributes}=={'environment','operation','status','window'}
                assert all(marker.encode() not in data for marker in MARKERS for _,data,_ in cloud.snapshot())
                all_files=b''.join(p.read_bytes() for p in (tmp_path/'archive').rglob('*') if p.is_file())
                assert all(marker.encode() not in all_files for marker in MARKERS)
            finally:
                process.terminate();process.wait(5)
