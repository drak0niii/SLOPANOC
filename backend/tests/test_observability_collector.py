"""Config validation and an actual isolated SDK->Collector->OTLP sink pipeline."""
from pathlib import Path
import json
import os
import socket
import subprocess
import time
import yaml
import pytest
from opentelemetry.proto.collector.trace.v1.trace_service_pb2 import ExportTraceServiceRequest
from backend.observability.config import ObservabilityConfig
from backend.observability.runtime import Runtime
from backend.observability.logging import emit
from backend.observability.tracing import span
from backend.observability.schemas import OperationalEvent
from backend.tests._m1_otel import receiver

ROOT=Path(__file__).resolve().parents[2]
COLLECTOR=ROOT/'infra/observability/collector'


def binary():
    value=os.environ.get('SLOPANOC_TEST_COLLECTOR_BINARY','/private/tmp/m1-collector/otelcol-contrib')
    if not Path(value).is_file():
        pytest.fail('Pinned Collector validation binary required; see collector README')
    return value


def free_port():
    with socket.socket() as sock:
        sock.bind(('127.0.0.1',0));return sock.getsockname()[1]


def collector_env(**changes):
    # No ADC credentials, proxies, inherited cloud or production configuration.
    return {'PATH':os.environ.get('PATH',''), 'OTLP_PORT':'4318','HEALTH_PORT':'13133',
        'SELF_METRICS_PORT':'8888','SLOPANOC_ENVIRONMENT':'test',
        'GOOGLE_CLOUD_PROJECT':'slopanoc-local-test','LOCAL_SINK_ENDPOINT':'http://127.0.0.1:9999',**changes}


@pytest.mark.parametrize('filename',['config.yaml','config.local.yaml'])
def test_config_syntax_components_pin_and_native_validate(filename):
    version=json.loads((COLLECTOR/'version.json').read_text())
    assert version['version']=='0.160.0' and ':latest' not in version['production_image']
    report=subprocess.run([binary(),'--version'],capture_output=True,text=True,timeout=5)
    assert '0.160.0' in report.stdout+report.stderr
    path=COLLECTOR/filename
    value=yaml.safe_load(path.read_text())
    assert {'memory_limiter','filter/safe_scope','transform/redact','resource/environment','batch'} <= set(value['processors'])
    assert 'health_check' in value['extensions']
    assert set(value['service']['pipelines'])=={'traces','metrics','logs'}
    assert 'tail_sampling' not in value['processors']
    if filename=='config.yaml':
        assert 'googlecloud' in value['exporters']
        assert 'resourcedetection/gcp' in value['processors']
    else:
        assert 'googlecloud' not in path.read_text()
        assert 'resourcedetection' not in value['processors']
    result=subprocess.run([binary(),'validate','--config='+str(path)],env=collector_env(),
        capture_output=True,text=True,timeout=10)
    assert result.returncode==0,result.stderr


@pytest.mark.parametrize('sample_kind', ['foundation', 'turn', 'model', 'execution', 'dependency', 'reliability', 'sre'])
def test_sdk_sample_travels_through_actual_local_collector(tmp_path, sample_kind):
    port,health_port,metric_port=free_port(),free_port(),free_port()
    with receiver() as sink:
        collector_stderr=(tmp_path/'collector-stderr.log').open('w+')
        process=subprocess.Popen([binary(),'--config='+str(COLLECTOR/'config.local.yaml')],
            env=collector_env(OTLP_PORT=str(port),HEALTH_PORT=str(health_port),
                SELF_METRICS_PORT=str(metric_port),LOCAL_SINK_ENDPOINT=sink.endpoint),
            stdout=subprocess.DEVNULL,stderr=collector_stderr,text=True)
        r=None
        try:
            deadline=time.monotonic()+5
            while time.monotonic()<deadline:
                with socket.socket() as sock:
                    if sock.connect_ex(('127.0.0.1',port))==0: break
                if process.poll() is not None:
                    collector_stderr.seek(0)
                    raise AssertionError(collector_stderr.read())
                time.sleep(.02)
            else: raise AssertionError('Collector never listened')
            r=Runtime(ObservabilityConfig(observability_enabled=True,otel_enabled=True,
                otel_exporter_otlp_endpoint=f'http://127.0.0.1:{port}',otel_batch_interval_seconds=.01,
                otel_environment="test"))
            if sample_kind == 'foundation':
                with span(r,OperationalEvent.INITIALIZED,{'message_count':4}):
                    emit(r,OperationalEvent.INITIALIZED)
            else:
                import asyncio
                from backend.api.chat_service import ChatService
                from backend.api.session_service import ApiSessionService
                from backend.tests._api_fakes import FakeRunner
                from backend.observability.turn_trace import current_turn
                async def actual_turn():
                    sessions = ApiSessionService()
                    sid = await sessions.create_session()
                    async def log_inside(*args):
                        emit(r, OperationalEvent.LEGACY)
                        # Valid lifecycle name still cannot carry raw content.
                        if sample_kind == 'model':
                            import httpx
                            from backend.tests.test_observability_model_provider import fake_model, payload, request, SENTINELS
                            from backend.observability.model_adapter import instrument_model
                            from backend.observability.model_instrumentation import _active
                            def reply(req):
                                attempt = _active.get().attempts[-1]
                                attempt.span.set_attribute('gen_ai.input.messages', ' '.join(SENTINELS))
                                attempt.span.add_event('gen_ai.user.message', {'content':' '.join(SENTINELS)})
                                return httpx.Response(200,json=payload())
                            async with fake_model(reply) as (base, _):
                                responses = [x async for x in instrument_model(base,'team_manager','orchestration').generate_content_async(request())]
                                assert responses
                        if sample_kind == 'execution':
                            from backend.observability.agent_instrumentation import Execution
                            from backend.observability.tool_instrumentation import tool_scope, observe_result
                            execution = Execution('agent', 'incident_manager', runtime=r)
                            with execution.attached():
                                with tool_scope('teams_get_messages') as tool_execution:
                                    observe_result(tool_execution, {'messages':[{'body':'M4_PRIVATE_TEAMS'}]})
                                    tool_execution.span.set_attribute('command','M4_PRIVATE_COMMAND')
                                    tool_execution.span.add_event('tool.completed', {'arguments':'M4_PRIVATE_ARGS'})
                                    emit(r,OperationalEvent.LEGACY,metadata={'prompt':'M4_PRIVATE_PROMPT'})
                            execution.finish()
                        if sample_kind == 'dependency':
                            from backend.observability.tool_instrumentation import tool_scope
                            from backend.observability.dependency_instrumentation import dependency_scope
                            with tool_scope('teams_get_messages'):
                                for dep, op, name in [('power_automate_gateway', 'teams.getMessages', 'http.client'),
                                        ('case_db', 'SELECT', 'db.client'), ('chat_attachments', 'upload', 'storage.client'),
                                        ('secret_manager', 'access_secret_version', 'secretmanager.client')]:
                                    with dependency_scope(dep, op, name) as child:
                                        child.update(**{'http.route': 'power_automate.teams.getMessages', 'slopanoc.bytes': 4})
                                        child.span.set_attribute('url.full', 'M5_PRIVATE_URL')
                                        child.span.set_attribute('db.query.text', 'M5_PRIVATE_SQL')
                                        child.span.set_attribute('http.request.header.authorization', 'M5_PRIVATE_TOKEN')
                                        child.span.add_event('tool.completed', {'body':'M5_PRIVATE_BODY'})
                        if sample_kind == 'sre':
                            from backend.observability.finops.metrics import record as accounting_record
                            accounting_record(r,'ledger_persisted',1)
                            accounting_record(r,'admission_duration',.005)
                            from backend.observability.slo_metrics import record, publish_result
                            from backend.observability.slo_alerts import confirm_safety
                            from backend.observability.slo_evaluator import evaluate, Snapshot, Bucket, Counts
                            from datetime import datetime, timezone, timedelta
                            at=datetime.now(timezone.utc).replace(second=0,microsecond=0)
                            record(r,'slopanoc.turn.accepted',1,'accepted','RUNNING')
                            record(r,'slopanoc.turn.duration',8,'GENERAL','COMPLETED')
                            publish_result(r,evaluate('availability',Snapshot('test',(Bucket(at-timedelta(minutes=1),Counts(good=990,bad=10)),),at,at-timedelta(days=29)),now=at))
                            confirm_safety(r,'unauthorized_command_exposure')
                        if sample_kind == 'reliability':
                            from backend.observability.deadlines import current_controller, observed
                            from backend.observability.reliability_contract import Category
                            owner = current_controller()
                            owner.emit('reliability.deadline_nearing', Category.MODEL,
                                **{'slopanoc.remaining_seconds':1.,'slopanoc.worker_disposition':'running',
                                   'slopanoc.outcome_certainty':'OUTCOME_UNKNOWN','prompt':'M6_PRIVATE_PROMPT'})
                            observed('stall',Category.TURN,status='STALLED')
                            observed('stall_duration',Category.TURN,.125,status='STALLED')
                        current_turn()._root.add_event('session.load.completed',
                            {'authorization':'Bearer M2_SENTINEL_TOKEN', 'prompt':'M2_SENTINEL_PROMPT'})
                    service = ChatService(sessions, runner=FakeRunner(sessions, side_effect=log_inside),
                        observability_runtime=r)
                    return [event async for event in service.execute_turn_events(sid, 'M2_SENTINEL_PROMPT')]
                events = asyncio.run(actual_turn())
            assert r.flush(1)
            captured=sink.wait('/v1/traces')
            requests=[ExportTraceServiceRequest.FromString(item[1]) for item in captured]
            samples=[sample for request in requests for rs in request.resource_spans
                     for scope in rs.scope_spans for sample in scope.spans]
            if sample_kind == 'foundation':
                sample=next(s for s in samples if s.name=='telemetry.initialized')
                assert sample.attributes[0].key=='message_count'
                assert sample.attributes[0].value.int_value==4
            else:
                roots=[s for s in samples if s.name=='slopanoc.turn']
                assert len(roots)==1
                sample=roots[0]
                attrs={a.key:a.value.string_value for a in sample.attributes}
                assert attrs['slopanoc.run_id']==events[0].run_id
                assert attrs['slopanoc.session_id']==events[0].session_id
                assert attrs['slopanoc.turn_id']=='test-invocation'
                assert attrs['slopanoc.turn_id_origin']=='adk'
                assert attrs['slopanoc.status']=='COMPLETED'
                assert len(attrs['slopanoc.config_version'])==16
                assert {'request.received','session.load.completed','persistence.completed','turn.completed'} <= {e.name for e in sample.events}
                assert not any(b'M2_SENTINEL' in item[1] for item in captured)
                assert all(s.trace_id==sample.trace_id for s in samples)
                if sample_kind == 'reliability':
                    event=next(e for e in sample.events if e.name=='reliability.deadline_nearing')
                    eventattrs={a.key:a.value for a in event.attributes}
                    assert eventattrs['slopanoc.deadline_category'].string_value=='model'
                    assert eventattrs['slopanoc.outcome_certainty'].string_value=='OUTCOME_UNKNOWN'
                    assert eventattrs['slopanoc.remaining_seconds'].double_value==1.
                    assert not any(b'M6_PRIVATE' in item[1] for item in sink.snapshot())
                    from opentelemetry.proto.collector.metrics.v1.metrics_service_pb2 import ExportMetricsServiceRequest
                    sink.wait('/v1/metrics')
                    metrics=[m for item in sink.snapshot() if item[0]=='/v1/metrics' for rs in ExportMetricsServiceRequest.FromString(item[1]).resource_metrics for sc in rs.scope_metrics for m in sc.metrics]
                    histogram=next(m for m in metrics if m.name=='slopanoc.turn.stall_duration')
                    assert histogram.unit=='s'
                    labels={a.key:a.value.string_value for a in histogram.histogram.data_points[0].attributes}
                    assert labels=={'environment':'test','operation':'turn','status':'STALLED'}
                    assert histogram.histogram.data_points[0].count==1
                    assert abs(histogram.histogram.data_points[0].sum-.125)<.00001
                if sample_kind == 'dependency':
                    tool = next(s for s in samples if s.name == 'slopanoc.tool')
                    children = [s for s in samples if s.name in {'http.client','db.client','storage.client','secretmanager.client'} and s.parent_span_id == tool.span_id]
                    assert len(children) == 4 and all(s.parent_span_id == tool.span_id for s in children)
                    assert not any(b'M5_PRIVATE' in item[1] for item in sink.snapshot())
                    from urllib.request import Request, urlopen
                    from opentelemetry.proto.common.v1.common_pb2 import KeyValue, AnyValue
                    injected = next(req for req in requests if any(s.name=='http.client' for rs in req.resource_spans for sc in rs.scope_spans for s in sc.spans))
                    target = next(s for rs in injected.resource_spans for sc in rs.scope_spans for s in sc.spans if s.name=='http.client')
                    target.attributes.extend([KeyValue(key='url.full',value=AnyValue(string_value='M5_PRIVATE_URL')),
                        KeyValue(key='db.query.text',value=AnyValue(string_value='M5_PRIVATE_SQL')),
                        KeyValue(key='http.request.header.authorization',value=AnyValue(string_value='M5_PRIVATE_TOKEN')),
                        KeyValue(key='slopanoc.retry_after_seconds',value=AnyValue(double_value=float('nan')))])
                    for attr in target.attributes:
                        if attr.key == 'http.route':attr.value.string_value='M5_PRIVATE_ROUTE'
                    before=len([item for item in sink.snapshot() if item[0]=='/v1/traces'])
                    with urlopen(Request(f'http://127.0.0.1:{port}/v1/traces',data=injected.SerializeToString(),headers={'Content-Type':'application/x-protobuf'}),timeout=2) as response:
                        assert response.status==200
                    deadline=time.monotonic()+3
                    while len([item for item in sink.snapshot() if item[0]=='/v1/traces'])<=before and time.monotonic()<deadline:time.sleep(.02)
                    assert len([item for item in sink.snapshot() if item[0]=='/v1/traces'])>before
                    assert not any(b'M5_PRIVATE' in item[1] for item in sink.snapshot())
                    approved = [ExportTraceServiceRequest.FromString(item[1]) for item in [item for item in sink.snapshot() if item[0]=='/v1/traces'][before:]]
                    assert any(s.name=='http.client' and any(a.key=='slopanoc.dependency' and a.value.string_value=='power_automate_gateway' for a in s.attributes) for req in approved for rs in req.resource_spans for sc in rs.scope_spans for s in sc.spans)
                    assert all(a.key!='slopanoc.retry_after_seconds' for req in approved for rs in req.resource_spans for sc in rs.scope_spans for s in sc.spans for a in s.attributes)
                    from opentelemetry.proto.collector.metrics.v1.metrics_service_pb2 import ExportMetricsServiceRequest
                    metrics=[m for item in sink.snapshot() if item[0]=='/v1/metrics' for rs in ExportMetricsServiceRequest.FromString(item[1]).resource_metrics for sc in rs.scope_metrics for m in sc.metrics]
                    metric=next(m for m in metrics if m.name=='slopanoc.dependency.duration')
                    assert metric.unit=='s'
                    assert all({a.key for a in p.attributes}=={'dependency','environment','operation','status'} for p in metric.histogram.data_points)
                    # Independently poison metric labels and exemplar payloads.
                    metric_request=next(ExportMetricsServiceRequest.FromString(item[1]) for item in sink.snapshot()
                        if item[0]=='/v1/metrics' and any(m.name=='slopanoc.dependency.duration'
                            for rs in ExportMetricsServiceRequest.FromString(item[1]).resource_metrics for sc in rs.scope_metrics for m in sc.metrics))
                    poisoned=next(m for rs in metric_request.resource_metrics for sc in rs.scope_metrics for m in sc.metrics
                        if m.name=='slopanoc.dependency.duration')
                    point=poisoned.histogram.data_points[0]
                    clean=poisoned.histogram.data_points.add()
                    clean.CopyFrom(point)
                    clean.attributes.append(KeyValue(key='run_id',value=AnyValue(string_value='M5_PRIVATE_METRIC_ID')))
                    point.attributes.append(KeyValue(key='run_id',value=AnyValue(string_value='M5_PRIVATE_METRIC_ID')))
                    exemplar=point.exemplars.add(time_unix_nano=point.time_unix_nano,as_double=1.0)
                    exemplar.filtered_attributes.append(KeyValue(key='authorization',value=AnyValue(string_value='M5_PRIVATE_EXEMPLAR')))
                    before=len([item for item in sink.snapshot() if item[0]=='/v1/metrics'])
                    with urlopen(Request(f'http://127.0.0.1:{port}/v1/metrics',data=metric_request.SerializeToString(),headers={'Content-Type':'application/x-protobuf'}),timeout=2) as response:
                        assert response.status==200
                    deadline=time.monotonic()+3
                    while len([item for item in sink.snapshot() if item[0]=='/v1/metrics'])<=before and time.monotonic()<deadline:time.sleep(.02)
                    forwarded=[item for item in sink.snapshot() if item[0]=='/v1/metrics'][before:]
                    assert forwarded and not any(b'M5_PRIVATE' in item[1] for item in forwarded)
                    safe_metrics=[m for item in forwarded for rs in ExportMetricsServiceRequest.FromString(item[1]).resource_metrics for sc in rs.scope_metrics for m in sc.metrics
                        if m.name=='slopanoc.dependency.duration']
                    assert safe_metrics and all(not p.exemplars for m in safe_metrics for p in m.histogram.data_points)
                    assert sum(len(m.histogram.data_points) for m in safe_metrics)==len(poisoned.histogram.data_points)-1
                if sample_kind == 'execution':
                    execution = next(s for s in samples if s.name=='slopanoc.agent')
                    tool = next(s for s in samples if s.name=='slopanoc.tool')
                    assert tool.parent_span_id==execution.span_id
                    attrs={a.key:a.value for a in tool.attributes}
                    assert attrs['slopanoc.tool'].string_value=='teams_get_messages'
                    assert attrs['slopanoc.result_count'].int_value==1
                    assert attrs['slopanoc.duration_ms'].double_value>=0
                    assert 'tool.started' in {e.name for e in tool.events}
                    assert not any(b'M4_PRIVATE' in item[1] for item in captured)
                    # Deliberately bypass application filters; Collector is independent.
                    from urllib.request import Request, urlopen
                    from opentelemetry.proto.common.v1.common_pb2 import KeyValue, AnyValue
                    injected=next(req for req in requests if any(s.name=='slopanoc.tool' for rs in req.resource_spans for sc in rs.scope_spans for s in sc.spans))
                    target=next(s for rs in injected.resource_spans for sc in rs.scope_spans for s in sc.spans if s.name=='slopanoc.tool')
                    target.attributes.append(KeyValue(key='tool.arguments',value=AnyValue(string_value='M4_PRIVATE_ARGS')))
                    next(a for a in target.attributes if a.key=='slopanoc.result_category').value.string_value='M4_PRIVATE_RESULT'
                    with urlopen(Request(f'http://127.0.0.1:{port}/v1/traces',data=injected.SerializeToString(),headers={'Content-Type':'application/x-protobuf'}),timeout=2) as response:
                        assert response.status==200
                    count=len(sink.snapshot());deadline=time.monotonic()+3
                    while len(sink.snapshot())<=count and time.monotonic()<deadline:time.sleep(.02)
                    assert len(sink.snapshot())>count
                    assert not any(b'M4_PRIVATE' in item[1] for item in sink.snapshot())
                    from opentelemetry.proto.collector.metrics.v1.metrics_service_pb2 import ExportMetricsServiceRequest
                    metric_records=[ExportMetricsServiceRequest.FromString(item[1]) for item in sink.snapshot() if item[0]=='/v1/metrics']
                    metrics=[m for rec in metric_records for rs in rec.resource_metrics for sc in rs.scope_metrics for m in sc.metrics]
                    duration=next(m for m in metrics if m.name=='slopanoc.tool.duration')
                    assert duration.unit=='s' and duration.histogram.data_points[0].count==1
                    assert {a.key for a in duration.histogram.data_points[0].attributes}=={'agent','environment','status','tool','tool_category'}
                if sample_kind == 'model':
                    attempts = [s for s in samples if s.name == 'gen_ai.request']
                    assert len(attempts) == 1
                    attrs = {a.key:a.value for a in attempts[0].attributes}
                    assert attrs['gen_ai.request.model'].string_value == 'gemini-2.5-flash'
                    assert attrs['slopanoc.agent'].string_value == 'team_manager'
                    assert attrs['gen_ai.usage.input_tokens'].int_value == 10
                    assert attrs['gen_ai.usage.output_tokens'].int_value == 6
                    assert attrs['slopanoc.duration_ms'].double_value > 0
                    assert {e.name for e in attempts[0].events} == {'model.request.started','model.request.completed'}
                    assert attempts[0].parent_span_id in {s.span_id for s in samples}
                    from backend.tests.test_observability_model_provider import SENTINELS
                    assert not any(secret.encode() in item[1] for secret in SENTINELS for item in captured)
                    # Exercise Collector defense independently by bypassing application filters.
                    from urllib.request import Request, urlopen
                    from opentelemetry.proto.common.v1.common_pb2 import KeyValue, AnyValue
                    injected = requests[-1]
                    target = next(s for rs in injected.resource_spans for scope in rs.scope_spans for s in scope.spans if s.name=='gen_ai.request')
                    target.attributes.append(KeyValue(key='gen_ai.output.messages',value=AnyValue(string_value=' '.join(SENTINELS))))
                    target.attributes.append(KeyValue(key='authorization',value=AnyValue(string_value=' '.join(SENTINELS))))
                    with urlopen(Request(f'http://127.0.0.1:{port}/v1/traces', data=injected.SerializeToString(),
                        headers={'Content-Type':'application/x-protobuf'}),timeout=2) as response:
                        assert response.status == 200
                    before_count = len(captured)
                    deadline = time.monotonic()+3
                    while time.monotonic() < deadline and len([x for x in sink.snapshot() if x[0]=='/v1/traces']) <= before_count:
                        time.sleep(.02)
                    forwarded = [x for x in sink.snapshot() if x[0]=='/v1/traces']
                    assert len(forwarded) > before_count
                    assert not any(secret.encode() in item[1] for secret in SENTINELS for item in forwarded)

            assert sink.wait('/v1/logs')
            metrics_capture = sink.wait('/v1/metrics')
            if sample_kind == 'sre':
                from opentelemetry.proto.collector.metrics.v1.metrics_service_pb2 import ExportMetricsServiceRequest
                from opentelemetry.proto.common.v1.common_pb2 import KeyValue, AnyValue
                from urllib.request import Request, urlopen
                metric_messages=[ExportMetricsServiceRequest.FromString(item[1]) for item in metrics_capture]
                metrics=[m for message in metric_messages for rm in message.resource_metrics for scope in rm.scope_metrics for m in scope.metrics]
                accounting=next(m for m in metrics if m.name=='slopanoc.accounting.ledger_persisted')
                assert accounting.sum.data_points[0].as_int==1 and accounting.unit=='1'
                admission=next(m for m in metrics if m.name=='slopanoc.accounting.admission_duration')
                assert admission.histogram.data_points[0].sum==.005 and admission.unit=='s'
                duration=next(m for m in metrics if m.name=='slopanoc.turn.duration')
                assert duration.unit=='s' and duration.histogram.data_points[0].count==1
                assert {5,8,20,30,45}<=set(duration.histogram.data_points[0].explicit_bounds)
                budget=next(m for m in metrics if m.name=='slopanoc.slo.budget_remaining')
                assert budget.gauge.data_points[0].as_double==-1
                accepted=next(m for m in metrics if m.name=='slopanoc.turn.accepted')
                assert {a.key for a in accepted.sum.data_points[0].attributes}=={'environment','operation','status','window'}
                injected=metric_messages[-1]
                target=next(m for rm in injected.resource_metrics for scope in rm.scope_metrics for m in scope.metrics if m.name=='slopanoc.turn.accepted')
                poison=target.sum.data_points.add();poison.CopyFrom(target.sum.data_points[0])
                poison.attributes.append(KeyValue(key='run_id',value=AnyValue(string_value='M9_SYNTHETIC_PRIVATE_MARKER')))
                account_target=next(m for rm in injected.resource_metrics for scope in rm.scope_metrics for m in scope.metrics if m.name=='slopanoc.accounting.ledger_persisted')
                account_target.sum.data_points[0].attributes.append(KeyValue(key='attempt_id',value=AnyValue(string_value='M10_SYNTHETIC_PRIVATE_MARKER')))
                before=len([x for x in sink.snapshot() if x[0]=='/v1/metrics'])
                with urlopen(Request(f'http://127.0.0.1:{port}/v1/metrics',data=injected.SerializeToString(),headers={'Content-Type':'application/x-protobuf'}),timeout=2) as response:
                    assert response.status==200
                deadline=time.monotonic()+3
                while time.monotonic()<deadline and len([x for x in sink.snapshot() if x[0]=='/v1/metrics'])<=before:time.sleep(.02)
                forwarded=[x for x in sink.snapshot() if x[0]=='/v1/metrics']
                assert len(forwarded)>before
                assert not any(b'M9_SYNTHETIC_PRIVATE_MARKER' in x[1] or b'M10_SYNTHETIC_PRIVATE_MARKER' in x[1] for x in forwarded)
            if sample_kind == 'model':
                from opentelemetry.proto.collector.metrics.v1.metrics_service_pb2 import ExportMetricsServiceRequest
                metric_messages = [ExportMetricsServiceRequest.FromString(item[1]) for item in metrics_capture]
                metrics = [m for message in metric_messages for rm in message.resource_metrics for scope in rm.scope_metrics for m in scope.metrics]
                duration = next(m for m in metrics if m.name=='gen_ai.client.operation.duration')
                assert duration.unit == 's' and duration.histogram.data_points[0].count == 1
                assert duration.histogram.data_points[0].sum > 0
                point = duration.histogram.data_points[0]
                assert {a.key for a in point.attributes} == {'environment','provider','model','agent','operation','status'}
                assert not point.exemplars

        finally:
            if r:r.close()
            process.terminate()
            try: process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill();process.wait(timeout=2)
            import sys
            if sys.exc_info()[0] is not None:
                collector_stderr.seek(0)
                print(collector_stderr.read()[-6000:],file=sys.stderr)
            collector_stderr.close()
