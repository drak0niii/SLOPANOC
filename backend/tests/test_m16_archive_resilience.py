"""Archive pressure, durable crash seams and slow-header admission."""
import concurrent.futures
import json
import socket
import threading
import time
import pytest
from backend.observability.archive import FilesystemArchive,ArchiveError
from backend.observability.archive_bridge import local_bridge
from backend.tests.test_m15_archive import request,principal,TID,MARKERS
from backend.tests._m16_harness import envelope,resources

@pytest.mark.parametrize('workers',[1,2,4,6])
def test_concurrent_commits_and_protected_reconstruction(tmp_path,workers):
    archive=FilesystemArchive(tmp_path,'test');raw=request(children=99).SerializeToString();times=[]
    def commit():
        began=time.monotonic();result,=archive.submit(raw);times.append(time.monotonic()-began)
        return result
    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as pool:
        results=list(pool.map(lambda _:commit(),range(12)))
        restored=list(pool.map(lambda _:archive.reconstruct(principal(),TID,results[0].digest),range(12)))
    assert all(r.state=='COMPLETE' for r in results) and len({r.digest for r in results})==1
    assert results.count(next(r for r in results if not r.duplicate))==1
    assert len(set(restored))==1 and not list(tmp_path.rglob('.commit-*'))
    assert all(marker.encode() not in restored[0] for marker in MARKERS)
    envelope('archive',times,concurrency=workers,spans_per_trace=100,complete_operations=12,
             unique_traces=1,duplicate_attempts=11,rss=resources()['rss'])

@pytest.mark.parametrize('phase',['chunk','object','manifest'])
def test_interrupted_immutable_create_retry_and_reopen(tmp_path,monkeypatch,phase):
    archive=FilesystemArchive(tmp_path,'test');original=archive._create;hit=False
    def fail(path,data):
        nonlocal hit
        matches=path.name.endswith('.chunk') if phase=='chunk' else path.name.endswith('.otlp') if phase=='object' else path.name.endswith('.manifest.json')
        if matches and not hit:
            hit=True;original(path,data);raise OSError('PRIVATE_FAILURE_TOKEN')
        return original(path,data)
    monkeypatch.setattr(archive,'_create',fail)
    with pytest.raises(ArchiveError):archive.submit(request().SerializeToString())
    assert hit
    reopened=FilesystemArchive(tmp_path,'test');result,=reopened.submit(request().SerializeToString())
    assert result.state=='COMPLETE'
    assert reopened.submit(request().SerializeToString())[0].duplicate
    assert reopened.reconstruct(principal(),TID,result.digest)
    assert not list(tmp_path.rglob('.commit-*'))

def test_slow_headers_do_not_create_unbounded_bridge_threads(tmp_path):
    archive=FilesystemArchive(tmp_path,'test');clients=[]
    with local_bridge(archive) as endpoint:
        port=int(endpoint.rsplit(':',1)[1]);baseline=len(threading.enumerate())
        try:
            for _ in range(20):
                client=socket.create_connection(('127.0.0.1',port),timeout=.5)
                client.sendall(b'POST /v1/traces HTTP/1.1\r\n')
                clients.append(client)
            time.sleep(.1)
            assert len(threading.enumerate())-baseline<=4, 'bridge admission occurs after unbounded header-reader threads'
        finally:
            for client in clients:client.close()
    assert not list(tmp_path.rglob('*.manifest.json'))

@pytest.mark.parametrize('phase',['chunk','object','manifest'])
def test_hard_process_exit_reopens_same_archive(tmp_path,phase):
    import subprocess,sys
    code='''
import os,sys
from pathlib import Path
from backend.observability.archive import FilesystemArchive
from backend.tests.test_m15_archive import request
archive=FilesystemArchive(Path(sys.argv[1]),'test');original=archive._create
suffix={'chunk':'.chunk','object':'.otlp','manifest':'.manifest.json'}[sys.argv[2]]
def crash(path,data):
 result=original(path,data)
 if path.name.endswith(suffix):os._exit(73)
 return result
archive._create=crash
archive.submit(request().SerializeToString())
'''
    child=subprocess.run([sys.executable,'-c',code,str(tmp_path),phase],capture_output=True,timeout=10)
    assert child.returncode==73,child.stderr.decode()
    reopened=FilesystemArchive(tmp_path,'test');result,=reopened.submit(request().SerializeToString())
    assert result.state=='COMPLETE'
    assert reopened.submit(request().SerializeToString())[0].duplicate
    assert reopened.reconstruct(principal(),TID,result.digest)
    assert len(list(tmp_path.rglob('*.manifest.json')))==1
