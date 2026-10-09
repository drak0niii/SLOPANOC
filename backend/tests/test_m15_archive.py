from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import pytest
from opentelemetry.proto.collector.trace.v1.trace_service_pb2 import ExportTraceServiceRequest
from backend.observability.archive import FilesystemArchive, ArchiveError, set_attrs, attrs, sanitize_request
from backend.observability.authorization import Principal
from backend.observability.schemas import Role, Permission, ROLE_PERMISSIONS
from backend.observability.sampling_policy import TraceClass, decide, classify
from backend.observability.privacy_policy import exception as safe_exception

TID='1'*32
ROOT='2'*16
CHILD='3'*16
MARKERS=['PRIVATE_PROMPT','PRIVATE_RESPONSE','PRIVATE_TEAMS','PRIVATE_COMMAND','PRIVATE_SQL',
    'PRIVATE_BEARER','PRIVATE_PASSWORD','PRIVATE_SECRET','PRIVATE_REASONING','PRIVATE_URL']

def request(cls='FAILED', *, children=1, closed=True, environment='test', expected=None):
    value=ExportTraceServiceRequest();resource=value.resource_spans.add()
    set_attrs(resource.resource.attributes,{'service.name':'slopanoc','deployment.environment.name':environment,
        'slopanoc.telemetry_schema_version':'1','secret':MARKERS[7]})
    scope=resource.scope_spans.add();scope.scope.name='slopanoc.observability';scope.scope.version='1'
    ids=[ROOT]+[format(n+3,'016x') for n in range(children)]
    for i,sid in enumerate(ids):
        span=scope.spans.add();span.trace_id=bytes.fromhex(TID);span.span_id=bytes.fromhex(sid)
        span.name='slopanoc.turn' if i==0 else 'db.client';span.kind=1 if i==0 else 3
        span.start_time_unix_nano=100+i;span.end_time_unix_nano=max(200,100+children*2)-i
        span.status.code=2 if i==0 else 1;span.status.message=' '.join(MARKERS)
        span.trace_state='secret='+MARKERS[5]
        if i: span.parent_span_id=bytes.fromhex(ROOT)
        fields={'prompt':MARKERS[0],'response':MARKERS[1],'reasoning':MARKERS[8],'command':MARKERS[3],
            'db.query.text':MARKERS[4],'url.full':MARKERS[9],'slopanoc.status':'FAILED' if i==0 else 'COMPLETED'}
        if i==0: fields.update({'slopanoc.trace_class':cls,'slopanoc.archive.population_closed':closed,
            'slopanoc.archive.expected_span_ids':expected or ids})
        set_attrs(span.attributes,fields)
        e=span.events.add();e.name='turn.failed';e.time_unix_nano=150
        set_attrs(e.attributes,{'slopanoc.error_code':'DATABASE_QUERY_ERROR','prompt':MARKERS[0]})
        span.links.add().trace_id=bytes.fromhex('4'*32)
    return value

def principal(role=Role.DEVELOPER_SRE, env='test'):
    return Principal('server-opaque',role,ROLE_PERMISSIONS[role],frozenset({env}),True)

@pytest.mark.parametrize('cls',[v.value for v in TraceClass if v not in {TraceClass.HEALTHY,TraceClass.UNKNOWN}])
def test_safe_full_reconstruction_and_idempotency(tmp_path,cls):
    clock=lambda:datetime(2026,1,1,tzinfo=timezone.utc)
    archive=FilesystemArchive(tmp_path,'test',clock=clock)
    raw=request(cls).SerializeToString();first,=archive.submit(raw)
    assert first.state=='COMPLETE'
    before=list(tmp_path.rglob('*.manifest.json'))[0].read_bytes()
    second,=archive.submit(raw)
    assert second.digest==first.digest and second.duplicate
    assert list(tmp_path.rglob('*.manifest.json'))[0].read_bytes()==before
    output=archive.reconstruct(principal(),TID,first.digest)
    decoded=ExportTraceServiceRequest();decoded.ParseFromString(output)
    spans=[s for r in decoded.resource_spans for scope in r.scope_spans for s in scope.spans]
    assert len(spans)==2
    root=next(s for s in spans if not s.parent_span_id)
    child=next(s for s in spans if s.parent_span_id)
    assert child.parent_span_id==root.span_id and child.kind==3 and root.kind==1
    assert root.start_time_unix_nano==100 and root.end_time_unix_nano==200
    assert root.status.code==2 and root.events[0].name=='turn.failed'
    assert not root.links and not root.trace_state and not root.status.message
    all_bytes=b''.join(p.read_bytes() for p in tmp_path.rglob('*') if p.is_file())+output
    assert all(marker.encode() not in all_bytes for marker in MARKERS)

def test_partial_unknown_and_ordinary_cannot_confirm(tmp_path):
    archive=FilesystemArchive(tmp_path,'test')
    assert archive.submit(request(children=0,expected=[ROOT,CHILD]).SerializeToString())[0].state=='PARTIAL'
    other=FilesystemArchive(tmp_path/'other','test')
    assert other.submit(request(closed=False).SerializeToString())[0].state=='UNKNOWN'
    healthy=FilesystemArchive(tmp_path/'healthy','test')
    assert healthy.submit(request('HEALTHY').SerializeToString())[0].state=='INELIGIBLE'
    assert not list((tmp_path/'healthy').rglob('*.manifest.json'))

@pytest.mark.parametrize('role',[Role.USER,Role.FINOPS,Role.ADMIN,Role.AUDITOR,Role.OPERATOR])
def test_archive_capability_not_role_assumption(tmp_path,role):
    a=FilesystemArchive(tmp_path,'test');commit,=a.submit(request().SerializeToString())
    with pytest.raises(Exception): a.reconstruct(principal(role),TID,commit.digest)

def test_environment_retention_path_and_corruption(tmp_path):
    now=[datetime(2026,1,1,tzinfo=timezone.utc)]
    a=FilesystemArchive(tmp_path,'test',clock=lambda:now[0]);commit,=a.submit(request().SerializeToString())
    with pytest.raises(Exception): a.reconstruct(principal(env='production'),TID,commit.digest)
    with pytest.raises(ArchiveError): a.reconstruct(principal(),'../../escape',commit.digest)
    with pytest.raises(ArchiveError): a.submit(request(environment='production').SerializeToString())
    now[0]+=timedelta(days=90)-timedelta(microseconds=1)
    assert a.reconstruct(principal(),TID,commit.digest)
    now[0]+=timedelta(microseconds=1)
    with pytest.raises(ArchiveError): a.reconstruct(principal(),TID,commit.digest)
    assert list(tmp_path.rglob('*.otlp')) # eligible != exact physical erasure

def test_exception_never_stringifies_payload():
    class Unsafe(ValueError):
        def __str__(self): raise AssertionError('stringified')
        def __repr__(self): raise AssertionError('repr')
    result=safe_exception(Unsafe(*MARKERS),code='DATABASE_QUERY_ERROR',subsystem='database_query',operation='db.client')
    assert result['exception_family']=='validation'
    assert all(marker not in json.dumps(result) for marker in MARKERS)
    assert safe_exception(Unsafe(),code='raw',subsystem='raw',operation='raw')=={}

def test_sampling_precedence_and_independence():
    assert classify(status='COMPLETED',ever_stalled=True)==TraceClass.EVER_STALLED
    assert classify(status='FAILED',governed=True,safety=True)==TraceClass.SAFETY
    for cls in TraceClass:
        if cls not in {TraceClass.HEALTHY,TraceClass.UNKNOWN}:
            assert decide(TID,cls,0).cloud_trace and decide(TID,cls,0).archive
    assert decide(TID,'HEALTHY',1).cloud_trace and not decide(TID,'HEALTHY',1).archive
    assert decide(TID,'FAILED','bad').archive and decide(TID,'FAILED','bad').degraded
    assert decide(TID,'unknown','bad').cloud_trace and decide(TID,'unknown','bad').degraded
