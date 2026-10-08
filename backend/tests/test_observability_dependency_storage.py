from types import SimpleNamespace
import pytest
from google.api_core.exceptions import DeadlineExceeded
from backend.attachments.storage import ChatAttachmentStorage
from backend.knowledge_ingestion.artifact_storage import KnowledgeArtifactStorage
from backend.tests._m5_dependencies import environment, spans, capture

SECRET='M5_STORAGE_SENTINEL'
class Blob:
    def __init__(self,present=False,error=None):self.present=present;self.error=error;self.calls=[]
    def call(self,op):
        self.calls.append(op)
        if self.error:raise self.error
    def upload_from_string(self,data,content_type):self.call('upload')
    def download_as_bytes(self):self.call('download');return SECRET.encode()
    def delete(self):self.call('delete')
    def exists(self):self.call('exists');return self.present

def storage(kind,blob):
    obj=ChatAttachmentStorage(SECRET) if kind=='chat' else KnowledgeArtifactStorage(SECRET)
    obj._client=SimpleNamespace(bucket=lambda name:SimpleNamespace(blob=lambda key:blob))
    return obj

@pytest.mark.parametrize('kind',['chat','knowledge'])
@pytest.mark.parametrize('present',[True,False])
def test_actual_wrappers_counts_privacy_bytes(kind,present):
    blob=Blob(present);obj=storage(kind,blob)
    with environment() as (r,t):
        if kind=='chat':obj.put_bytes(SECRET,SECRET.encode(),'image/png')
        else:assert obj.put_bytes_if_absent(SECRET,SECRET.encode(),'image/png')== (not present)
        assert obj.get_bytes(SECRET)==SECRET.encode()
        assert obj.exists(SECRET)==present
        if kind=='chat':obj.delete(SECRET)
        obj.uri_for(SECRET)
        records=spans(r,'storage.client')
        assert len(records)==len(blob.calls)
        assert sum(s.attributes['slopanoc.dependency_operation']=='upload' for s in records)==(1 if kind=='chat' or not present else 0)
        assert all(s.attributes['slopanoc.retry_visibility']=='unknown' for s in records)
        assert next(s for s in records if s.attributes['slopanoc.dependency_operation']=='download').attributes['slopanoc.bytes']==len(SECRET)
        assert SECRET not in capture(r)

@pytest.mark.parametrize('error,code',[(DeadlineExceeded(SECRET),'STORAGE_TIMEOUT'),(ValueError(SECRET),'STORAGE_ERROR')])
def test_storage_error_preserved_once(error,code):
    blob=Blob(error=error);obj=storage('chat',blob)
    with environment() as (r,t):
        with pytest.raises(type(error)) as caught:obj.delete(SECRET)
        assert caught.value is error
        assert blob.calls==['delete']
        assert spans(r,'storage.client')[0].attributes['slopanoc.error_code']==code
        assert SECRET not in capture(r)

def test_actual_secret_cache_miss_only(monkeypatch):
    from backend.config.settings import _cached_secret_value
    calls=[]
    def access(**kwargs):calls.append(1);return SimpleNamespace(payload=SimpleNamespace(data=SECRET.encode()))
    monkeypatch.setattr('google.cloud.secretmanager.SecretManagerServiceClient',lambda:SimpleNamespace(access_secret_version=access))
    _cached_secret_value.cache_clear()
    try:
        with environment() as (r,t):
            assert _cached_secret_value(SECRET)==_cached_secret_value(SECRET)==SECRET
            assert calls==[1] and len(spans(r,'secretmanager.client'))==1
            assert SECRET not in capture(r)
    finally:_cached_secret_value.cache_clear()

@pytest.mark.asyncio
async def test_actual_storage_offload_cancellation_preserves_late_write_once():
    import asyncio
    import threading
    began=threading.Event();release=threading.Event();finished=threading.Event()
    class BlockedBlob(Blob):
        def upload_from_string(self,data,content_type):
            self.call('upload');began.set();release.wait(3);finished.set()
    blob=BlockedBlob();obj=storage('chat',blob)
    with environment() as (r,t):
        task=asyncio.create_task(asyncio.to_thread(obj.put_bytes,SECRET,b'fake','image/png'))
        try:
            while not began.is_set():await asyncio.sleep(.001)
            assert t.snapshot().current_dependency=='chat_attachments'
            task.cancel()
            with pytest.raises(asyncio.CancelledError):await task
            # Cancelling the await cannot abort a synchronous SDK call already running.
            assert t.snapshot().dependencies
            t.finish();assert not t.snapshot().dependencies
        finally:release.set()
        while not finished.is_set():await asyncio.sleep(.001)
        for _ in range(100):
            if spans(r,'storage.client'):break
            await asyncio.sleep(.001)
        assert len(spans(r,'storage.client'))==len(blob.calls)==1
        assert SECRET not in capture(r)
