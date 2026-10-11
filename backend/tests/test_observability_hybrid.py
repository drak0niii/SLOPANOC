"""Local hybrid contract proof. No cloud, remote DB, worker or business imports."""
from contextlib import asynccontextmanager
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
import ast
import json
import sqlite3
import subprocess
from uuid import uuid4
import pytest
import yaml
from pydantic import ValidationError
from backend.observability.hybrid.contracts import (
    AuditEvent, AuditPayload, TelemetryEvent, TelemetryPayload, OutboxRecord,
    decode_event, event_digest, MAX_EVENT_BYTES)
from backend.observability.hybrid.config import HybridConfig
from scripts.observability.hybrid_collector_config import compose_local

ROOT = Path(__file__).resolve().parents[2]
COLLECTOR = ROOT/'infra/observability/collector'
NOW = datetime(2026, 10, 11, tzinfo=timezone.utc)

def audit(**changes):
    return AuditEvent(event_id=uuid4(), environment='test', timestamp=NOW,
        event_type='usage.captured', producer='accounting',
        payload_safe=AuditPayload(source_record_id=str(uuid4()), source_record_version=1), **changes)

def wire(event):
    return event.model_dump_json().encode()

@pytest.mark.parametrize('event', [audit(), TelemetryEvent(event_id=uuid4(),
    environment='test', timestamp=NOW, event_type='queue.observed',
    payload_safe=TelemetryPayload(queue_depth=3))])
def test_envelope_roundtrip_environment_and_stable_identity(event):
    decoded = decode_event(wire(event), environment='test')
    assert decoded == event
    assert event_digest(decoded) == event_digest(event)
    with pytest.raises(ValueError, match='environment rejected'):
        decode_event(wire(event), environment='production')

@pytest.mark.parametrize('field,value', [('schema_version', 2), ('schema_version', True), ('event_class', 'BUSINESS'),
    ('event_type', 'execute.command'), ('producer', 'user'), ('environment', 'foreign'),
    ('trace_id', '0'*32), ('run_id', 'Bearer secret'), ('timestamp', '2026-10-11')])
def test_unknown_schema_and_unsafe_envelope_rejected(field, value):
    data=audit().model_dump(mode='json'); data[field]=value
    with pytest.raises(ValidationError):
        decode_event(json.dumps(data).encode(), environment='test')

@pytest.mark.parametrize('field', ['prompt','response','secret','authorization','sql',
    'teams_body','reasoning','command','tool_arguments','knowledge_body','attachment'])
def test_raw_content_and_fake_secret_never_enter_payload(field):
    event=audit(); data=event.model_dump(mode='json')
    data['payload_safe'][field]='Bearer HYBRID_FAKE_SECRET'
    with pytest.raises(ValidationError) as failure:
        decode_event(json.dumps(data).encode(), environment='test')
    assert 'HYBRID_FAKE_SECRET' not in str(failure.value)
    assert 'HYBRID_FAKE_SECRET' not in wire(event).decode()
    data=event.model_dump(mode='json'); data[field]='HYBRID_FAKE_SECRET'
    with pytest.raises(ValidationError): decode_event(json.dumps(data).encode(), environment='test')

@pytest.mark.parametrize('payload', [{'queue_depth': True}, {'queue_depth': -1},
    {'retry_count': 2**63}, {'duration_ms': float('inf')}, {'duration_ms': 86400001},
    {'stage': 'invented'}, {'agent': 'secret-agent'}, {'error_code': 'raw traceback'},
    {'arbitrary': {'nested': 'prompt'}}])
def test_telemetry_values_fail_closed(payload):
    with pytest.raises(ValidationError): TelemetryPayload(**payload)

def test_size_and_capture_unknown_are_truthful():
    with pytest.raises(ValueError): decode_event(b' '*(MAX_EVENT_BYTES+1), environment='test')
    value=TelemetryPayload(availability='UNKNOWN_NOT_CAPTURED')
    assert value.duration_ms is None and value.queue_depth is None
    with pytest.raises(ValidationError):
        AuditPayload(source_record_id='safe', source_record_version=0)
    with pytest.raises(ValidationError):
        AuditPayload(source_record_id='safe', source_record_version=1, capture='UNKNOWN_NOT_CAPTURED')

def test_no_implicit_publication_and_environment_topics():
    value=HybridConfig()
    assert not any((value.audit_publication_enabled,value.operational_pubsub_enabled,value.projector_enabled))
    with pytest.raises(ValidationError): HybridConfig(audit_publication_enabled=True)
    with pytest.raises(ValidationError):
        HybridConfig(project='local-test-project', environment='test',
            audit_topic='projects/local-test-project/topics/slopanoc-production-audit-events-v1')
    with pytest.raises(ValidationError): HybridConfig(publisher_batch_size=100000)
    bound=HybridConfig(environment='test',project='local-test-project',
        audit_topic='projects/local-test-project/topics/slopanoc-test-audit-events-v1',
        telemetry_topic='projects/local-test-project/topics/slopanoc-test-telemetry-events-v1')
    assert not bound.projector_enabled

def test_outbox_leases_and_publish_confirmation():
    base={'event':audit(),'aggregate_type':'usage','aggregate_id':'usage-1'}
    record=OutboxRecord(**base)
    assert record.publication_state=='PENDING' and record.attempt_count==0
    for change in ({'publication_state':'PUBLISHED'}, {'published_at':NOW},
        {'publication_state':'CLAIMED'}, {'lease_token':uuid4()}):
        with pytest.raises(ValidationError): OutboxRecord(**base,**change)
    assert OutboxRecord(**base,publication_state='PUBLISHED',published_at=NOW)


def test_disposable_sql_critical_record_and_outbox_roll_back_together():
    # Demonstrates the interface contract; NOT the future Cloud SQL adapter.
    db=sqlite3.connect(':memory:')
    db.execute('CREATE TABLE critical (id TEXT PRIMARY KEY)')
    db.execute('CREATE TABLE outbox (id TEXT PRIMARY KEY, digest TEXT NOT NULL)')
    event=audit(); key=str(event.event_id); digest=event_digest(event)
    def transaction(fail=False):
        with db:
            db.execute('INSERT INTO critical VALUES (?)',(key,))
            db.execute('INSERT INTO outbox VALUES (?, ?)',(key,digest))
            if fail: raise RuntimeError('injected commit failure')
    with pytest.raises(RuntimeError): transaction(True)
    assert db.execute('SELECT COUNT(*) FROM critical').fetchone()[0]==0
    assert db.execute('SELECT COUNT(*) FROM outbox').fetchone()[0]==0
    transaction()
    assert db.execute('SELECT id FROM critical').fetchone()[0]==key
    assert db.execute('SELECT id FROM outbox').fetchone()[0]==key
    db.close()

class FakeProjectionStore:
    """Isolated reference adapter for receipt+projection atomicity/ack semantics."""
    def __init__(self): self.receipts={}; self.views={}
    @asynccontextmanager
    async def transaction(self):
        receipts,views=deepcopy(self.receipts),deepcopy(self.views)
        class Tx:
            async def apply_once(_,event,*,digest):
                key=(event.environment,event.event_id,1)
                if key in receipts:
                    return 'DUPLICATE' if receipts[key]==digest else 'CONFLICT'
                receipts[key]=digest
                version=event.payload_safe.source_record_version
                source=event.payload_safe.source_record_id
                if version>views.get(source,0): views[source]=version
                return 'APPLIED'
        yield Tx()
        self.receipts,self.views=receipts,views

@pytest.mark.asyncio
async def test_publish_confirm_crash_redelivery_conflict_stale_and_rollback():
    store=FakeProjectionStore(); event=audit()
    async def consume(item, fail=False):
        # Revalidation before admission, ACK would only happen after context commit.
        item=decode_event(wire(item),environment='test')
        async with store.transaction() as tx:
            result=await tx.apply_once(item,digest=event_digest(item))
            if fail: raise RuntimeError('projection rollback')
        return result
    with pytest.raises(RuntimeError): await consume(event,True)
    assert not store.receipts and not store.views
    assert await consume(event)=='APPLIED'
    # Publisher crash before confirm: same event bytes delivered twice.
    assert await consume(event)=='DUPLICATE'
    assert len(store.receipts)==len(store.views)==1
    conflict=event.model_copy(update={'payload_safe':event.payload_safe.model_copy(update={'source_record_version':2})})
    assert await consume(conflict)=='CONFLICT'
    later=event.model_copy(update={'event_id':uuid4(),'payload_safe':conflict.payload_safe})
    assert await consume(later)=='APPLIED'
    assert await consume(event.model_copy(update={'event_id':uuid4()}))=='APPLIED'
    assert store.views[event.payload_safe.source_record_id]==2


def test_collector_branch_common_gate_before_fanout_and_existing_tails_unchanged(tmp_path):
    original=yaml.safe_load((COLLECTOR/'config.local.yaml').read_text())
    configured=compose_local(original)
    assert 'forward/operational' not in original['connectors']
    for name in ('traces/cloud','traces/archive'):
        assert configured['service']['pipelines'][name]==original['service']['pipelines'][name]
    assert configured['processors']==original['processors']
    assert configured['service']['pipelines']['traces']['processors']==original['service']['pipelines']['traces']['processors']
    assert configured['service']['pipelines']['traces/operational']['exporters']==['nop/operational-disabled']
    assert not any(name.startswith('googlecloud') for name in configured['exporters'])
    production=yaml.safe_load((COLLECTOR/'config.yaml').read_text())
    with pytest.raises(ValueError): compose_local(production)
    path=tmp_path/'hybrid.local.yaml'; path.write_text(yaml.safe_dump(configured,sort_keys=False))
    binary=Path('/private/tmp/m1-collector/otelcol-contrib')
    if not binary.exists(): pytest.skip('Native pinned binary unavailable')
    report=subprocess.run([str(binary),'--version'],capture_output=True,text=True,timeout=5)
    assert '0.160.0' in report.stdout+report.stderr
    # validate only; nop/local endpoints and no credentials/cloud env supplied.
    import os
    env={'PATH':os.environ.get('PATH',''),'OTLP_PORT':'4318','HEALTH_PORT':'13133',
        'SELF_METRICS_PORT':'8888','SLOPANOC_ENVIRONMENT':'test',
        'LOCAL_SINK_ENDPOINT':'http://127.0.0.1:9999'}
    result=subprocess.run([str(binary),'validate','--config='+str(path)],env=env,
        capture_output=True,text=True,timeout=15)
    assert result.returncode==0,result.stderr
    # Inspect/validate the pinned future exporter schema only. Never start it.
    template=yaml.safe_load((COLLECTOR/'hybrid-branch.yaml.example').read_text())
    configured['exporters']['googlecloudpubsub/operational-future']=template['exporters']['googlecloudpubsub/operational-future']
    configured['service']['pipelines']['traces/operational']['exporters']=['googlecloudpubsub/operational-future']
    path.write_text(yaml.safe_dump(configured,sort_keys=False))
    env.update(HYBRID_TELEMETRY_PROJECT='slopanoc-local-test',
        HYBRID_TELEMETRY_TOPIC='projects/slopanoc-local-test/topics/slopanoc-test-telemetry-events-v1')
    result=subprocess.run([str(binary),'validate','--config='+str(path)],env=env,
        capture_output=True,text=True,timeout=15)
    assert result.returncode==0,result.stderr


def test_scaffolding_imports_no_cloud_clients_or_runtime_and_migrations_unchanged():
    for path in (ROOT/'backend/observability/hybrid').glob('*.py'):
        tree=ast.parse(path.read_text())
        imports=[node.module or '' for node in ast.walk(tree) if isinstance(node,ast.ImportFrom)]
        assert not any(name.startswith(('google.cloud','backend.api','backend.agents')) for name in imports)
    revisions={}
    for path in (ROOT/'alembic/versions').glob('*.py'):
        tree=ast.parse(path.read_text()); values={}
        for node in tree.body:
            if isinstance(node,ast.Assign):
                for target in node.targets:
                    if isinstance(target,ast.Name) and target.id in ('revision','down_revision'):
                        values[target.id]=ast.literal_eval(node.value)
            if isinstance(node,ast.AnnAssign) and isinstance(node.target,ast.Name) and node.target.id in ('revision','down_revision'):
                values[node.target.id]=ast.literal_eval(node.value)
        if 'revision' in values: revisions[values['revision']]=values['down_revision']
    parents={p for parent in revisions.values() for p in
        (parent if isinstance(parent,tuple) else (parent,)) if p}
    assert set(revisions)-parents=={'e27b2c8d0f51'}
    assert len(revisions)==19


def test_authoritative_docs_all_in_one_and_manifest_consistent():
    root=ROOT/'docs/Telemetry'; pack=(root/'ALL_IN_ONE.md').read_text()
    import re
    names=re.findall(r'^# FILE: (.+)$',pack,re.M)
    for name in names:
        assert (root/name).read_text().rstrip() in pack
    doc=(root/'26_HYBRID_EVENT_DRIVEN_OBSERVABILITY.md').read_text()
    for term in ('C41/C42','M16','INCOMPLETE','M17','UNSTARTED','Class A',
        'UNKNOWN / NOT CAPTURED','pgAudit','Cloud Audit Logs','c16f4a8b2d90'):
        assert term in doc
    assert '26_HYBRID_EVENT_DRIVEN_OBSERVABILITY.md' in (root/'README.md').read_text()
    manifest=(root/'GCP_HYBRID_OBSERVABILITY_RESOURCE_MANIFEST.md').read_text()
    assert 'PHASE 1 REQUIRED' in manifest and 'PHASE 2 REQUIRED' in manifest and 'OPTIONAL / SCALE-TRIGGERED' in manifest
    assert 'PHASE 1 MINIMUM' in manifest
    assert 'full unsampled OTLP replication requirement' in manifest
    assert 'slopanoc-development-audit-events-v1' in manifest
    assert 'Ready for Phase-1 GCP provisioning: NO' in manifest
    for link in re.findall(r'\]\(([^)]+\.md)\)',doc):
        if not link.startswith('http'): assert (root/link).exists()


def test_actual_hybrid_operational_branch_is_sanitized_before_tail_decision(tmp_path):
    # Test-only OTLP sink replaces nop. No Pub/Sub/cloud exporter instantiated.
    import socket
    import time
    import httpx
    from backend.tests._m1_otel import receiver
    from backend.tests.test_observability_collector import binary, free_port, collector_env
    from backend.tests.test_m15_archive import request, MARKERS
    from opentelemetry.proto.collector.trace.v1.trace_service_pb2 import ExportTraceServiceRequest
    config=compose_local(yaml.safe_load((COLLECTOR/'config.local.yaml').read_text()))
    with receiver() as sink:
        config['exporters']['otlphttp/operational-test']={'endpoint':sink.endpoint}
        config['service']['pipelines']['traces/operational']['exporters']=['otlphttp/operational-test']
        # Prove the new operational path does not depend on a 240s tail decision.
        port,health,metrics=free_port(),free_port(),free_port()
        path=tmp_path/'hybrid-operational-test.yaml';path.write_text(yaml.safe_dump(config))
        with (tmp_path/'collector-errors.log').open('w+') as errors:
            proc=subprocess.Popen([binary(),'--config='+str(path)],stdout=subprocess.DEVNULL,stderr=errors,
                env=collector_env(OTLP_PORT=str(port),HEALTH_PORT=str(health),
                    SELF_METRICS_PORT=str(metrics),LOCAL_SINK_ENDPOINT=sink.endpoint))
            try:
                deadline=time.monotonic()+5
                while time.monotonic()<deadline:
                    with socket.socket() as sock:
                        if sock.connect_ex(('127.0.0.1',port))==0: break
                    assert proc.poll() is None
                    time.sleep(.02)
                else: pytest.fail('Local Collector failed to listen')
                value=request('FAILED')
                # Existing scope gate rejects entire spans containing links. Keep
                # the accepted fixture's poisoned raw attributes/status/events.
                for resource in value.resource_spans:
                    for scope in resource.scope_spans:
                        for span in scope.spans: del span.links[:]
                response=httpx.post(f'http://127.0.0.1:{port}/v1/traces',content=value.SerializeToString(),
                    headers={'Content-Type':'application/x-protobuf'},timeout=2)
                assert response.status_code==200
                deadline=time.monotonic()+5; records=[]
                while time.monotonic()<deadline:
                    records=[body for route,body,_ in sink.snapshot() if route=='/v1/traces']
                    if records: break
                    time.sleep(.05)
                assert records and all(marker.encode() not in body for body in records for marker in MARKERS)
                spans=[s for body in records for r in ExportTraceServiceRequest.FromString(body).resource_spans
                    for scope in r.scope_spans for s in scope.spans]
                assert len(spans)==2
                assert {s.name for s in spans}=={'slopanoc.turn','db.client'}
            finally:
                proc.terminate();proc.wait(5)
