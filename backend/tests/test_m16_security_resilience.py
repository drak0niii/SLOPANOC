"""Bounded malformed corpus, failure privacy and local isolation assertions."""
import json
import socket
import pytest
from backend.observability.archive import FilesystemArchive,ArchiveError,MAX_REQUEST,set_attrs
from backend.tests.test_m15_archive import request,MARKERS,TID,principal
from backend.observability.privacy_policy import exception
from backend.observability.config import ObservabilityConfig
from backend.observability.service import Service
from backend.observability.sampling_policy import classify

@pytest.mark.parametrize('fault',['protobuf','identity','environment','oversized','timestamp','attributes'])
def test_malformed_archive_rejects_and_next_valid_commit_survives(tmp_path,fault):
    archive=FilesystemArchive(tmp_path,'test');value=request()
    root=value.resource_spans[0].scope_spans[0].spans[0]
    if fault=='identity':root.trace_id=b'bad'
    elif fault=='environment':set_attrs(value.resource_spans[0].resource.attributes,{'deployment.environment.name':'../../production'})
    elif fault=='timestamp':root.end_time_unix_nano=0
    elif fault=='attributes':
        for n in range(300):root.attributes.add().key='unknown'+str(n)
    raw=b'\xff' if fault=='protobuf' else b'x'*(MAX_REQUEST+1) if fault=='oversized' else value.SerializeToString()
    with pytest.raises(ArchiveError):archive.submit(raw)
    assert not list(tmp_path.rglob('*.manifest.json'))
    result,=archive.submit(request().SerializeToString());assert result.state=='COMPLETE'
    safe=archive.reconstruct(principal(),TID,result.digest)
    assert all(marker.encode() not in safe for marker in MARKERS)

@pytest.mark.parametrize('value',['../../production','x'*4096,'not-an-id','00000000-0000-0000-0000-000000000000/..'])
def test_bounded_diagnostic_identity_and_environment(value):
    service=Service(None,ObservabilityConfig())
    with pytest.raises(Exception):service.run_id(value)
    with pytest.raises(Exception):service.environment_scope(principal(),value)

def test_test_runner_guard_prevents_external_egress():
    import os
    assert os.environ.get('SLOPANOC_M16_LOCAL_TEST')=='1'
    with socket.socket() as sock:
        with pytest.raises(AssertionError):sock.connect(('203.0.113.1',443))
    with pytest.raises(AssertionError):socket.getaddrinfo('nonlocal.invalid',443)

def test_guard_missing_enablement_fails_closed_in_child():
    import os,subprocess,sys
    env=dict(os.environ);env.pop('SLOPANOC_M16_LOCAL_TEST',None)
    child=subprocess.run([sys.executable,'-c','raise AssertionError("must never run")'],env=env,capture_output=True,timeout=5)
    assert child.returncode==78
