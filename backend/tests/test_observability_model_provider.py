"""Actual installed GenAI/ADK provider execution using fake HTTP transports."""
import asyncio
import json
from types import SimpleNamespace
from contextlib import asynccontextmanager
import httpx
import pytest
from google.genai import Client, types
from google.adk.models.google_llm import Gemini
from google.adk.models.llm_request import LlmRequest
from backend.observability.model_adapter import instrument_model
from backend.observability.model_context import observation_sink
from backend.observability.model_provider import compatible, instrument_client, embedding_request
from backend.observability.model_instrumentation import _active
from backend.observability.runtime import Runtime
from backend.tests.test_observability_runtime import config

SENTINELS=['M3_USER_SECRET','M3_SYSTEM_SECRET','M3_HISTORY_SECRET','M3_COMPLETION_SECRET',
    'M3_THOUGHT_SECRET','M3_TEAMS_SECRET','M3_KNOWLEDGE_SECRET','M3_ATTACHMENT_SECRET',
    'M3_TOOL_SECRET','M3_BEARER_SECRET','M3_AUTHORIZATION_SECRET']


def payload(tokens=10, text=SENTINELS[3]):
    return {'candidates':[{'content':{'role':'model','parts':[{'text':text}]},'finishReason':'STOP'}],
        'modelVersion':'gemini-2.5-flash-001','usageMetadata':{'promptTokenCount':tokens,
        'candidatesTokenCount':4,'thoughtsTokenCount':2,'cachedContentTokenCount':3,'totalTokenCount':tokens+6}}


def request():
    return LlmRequest(model='gemini-2.5-flash',contents=[types.Content(role='user',
        parts=[types.Part(text=' '.join(SENTINELS))])],config=types.GenerateContentConfig(
        system_instruction=SENTINELS[1],temperature=.3))


class Bytes(httpx.AsyncByteStream):
    def __init__(self,items):self.items=items;self.closed=False
    async def __aiter__(self):
        for item in self.items:
            if isinstance(item,BaseException):raise item
            await asyncio.sleep(0)
            yield ('data: '+json.dumps(item)+'\n\n').encode()
    async def aclose(self):self.closed=True


@asynccontextmanager
async def fake_model(handler, *, retry_options=None):
    transport=httpx.MockTransport(handler)
    http=httpx.AsyncClient(transport=transport)
    client=Client(api_key='M3_FAKE_KEY',http_options=types.HttpOptions(httpx_async_client=http,
        retry_options=retry_options))
    base=Gemini(model='gemini-2.5-flash')
    base.__dict__['api_client']=client
    try:yield base,client
    finally:
        await http.aclose()
        await client.aio.aclose()
        client.close()


def spans(runtime):
    assert runtime.flush(1)
    return runtime.exporters['trace'].snapshot()


def test_pinned_compatibility():
    assert compatible()


def test_partial_installation_rolls_back_transport_adapter():
    async def send(*args,**kwargs):return 'unchanged'
    async def once(*args,**kwargs):pass
    async def session(*args,**kwargs):pass
    class ReadOnlyApi:
        def __init__(self):
            self._async_httpx_client=SimpleNamespace(send=send)
            self._async_request_once=once;self._async_request=once
        @property
        def _get_aiohttp_session(self):return session
    api=ReadOnlyApi();client=SimpleNamespace(_api_client=api)
    assert instrument_client(client) is client
    assert api._async_httpx_client.send is send and api._async_request_once is once
    assert not getattr(api,'_slopanoc_model_observed',False)


@pytest.mark.asyncio
@pytest.mark.parametrize('stream',[False,True])
async def test_real_sdk_usage_ttft_response_and_privacy(stream,monkeypatch):
    import os
    monkeypatch.setenv('ADK_CAPTURE_MESSAGE_CONTENT_IN_SPANS','true')
    monkeypatch.setenv('OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT','true')
    seen=[]; raw=[]
    chunks=[{'usageMetadata':{'promptTokenCount':2}},
        {'candidates':[{'content':{'parts':[{'text':SENTINELS[4],'thought':True}]}}]},
        payload(5),payload(10)]
    def handler(req):
        raw.append(json.loads(req.content))
        return httpx.Response(200,stream=Bytes(chunks)) if stream else httpx.Response(200,json=payload())
    r=Runtime(config())
    try:
        async with fake_model(handler) as (base,client):
            model=instrument_model(base,'team_manager','orchestration')
            with observation_sink(seen.append,runtime=r):
                responses=[x async for x in model.generate_content_async(request(),stream=stream)]
            assert responses and any(SENTINELS[3] in (p.text or '') for x in responses if x.content for p in x.content.parts)
            assert raw[0]['generationConfig']['temperature']==.3
            assert raw[0]['contents'][0]['parts'][0]['text']==' '.join(SENTINELS)
            assert len(seen)==1 and seen[0].usage.input_tokens==10 and seen[0].usage.output_tokens==6
            assert seen[0].usage.cached_tokens==3 and seen[0].usage.total_tokens==16
            assert (seen[0].ttft_ms is not None)==stream
            assert seen[0].ttft_boundary==('first_provider_output' if stream else None)
            exported=spans(r)
            assert {s.name for s in exported}=={'gen_ai.request','slopanoc.model.operation'}
            attempt=next(s for s in exported if s.name=='gen_ai.request')
            assert attempt.attributes['gen_ai.usage.input_tokens']==10
            assert len([e for e in attempt.events if e.name=='model.first_token'])==int(stream)
            data=repr([(s.attributes,s.events,s.status.description) for s in exported])+str(seen)
            assert not any(secret in data for secret in SENTINELS)
            assert _active.get() is None
            assert os.environ['ADK_CAPTURE_MESSAGE_CONTENT_IN_SPANS']=='false'
            assert os.environ['OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT']=='false'
    finally:r.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('code,expected',[(429,'MODEL_RATE_LIMIT'),(403,'MODEL_AUTH_ERROR'),(500,'MODEL_PROVIDER_ERROR'),(504,'MODEL_TIMEOUT')])
async def test_http_errors(code,expected):
    def handler(req):return httpx.Response(code,json={'error':{'code':code,'message':' '.join(SENTINELS)}})
    observations=[]
    async with fake_model(handler) as (base,_):
        with observation_sink(observations.append):
            with pytest.raises(Exception):
                _=[x async for x in instrument_model(base,'incident_manager').generate_content_async(request())]
    assert len(observations)==1 and observations[0].error_code.value==expected
    assert observations[0].usage.availability=='UNKNOWN' and observations[0].usage.input_tokens is None
    assert not any(s in str(observations) for s in SENTINELS)


@pytest.mark.asyncio
async def test_sdk_retry_attempts_not_logical_duplicates():
    calls=[]; observations=[]
    def handler(req):
        calls.append(req)
        return httpx.Response(429,json={'error':{'code':429,'message':'secret'}}) if len(calls)==1 else httpx.Response(200,json=payload())
    options=types.HttpRetryOptions(attempts=2,initial_delay=.001,max_delay=.001,jitter=.001)
    async with fake_model(handler,retry_options=options) as (base,_):
        with observation_sink(observations.append):
            _=[x async for x in instrument_model(base,'technical_authority_engineer').generate_content_async(request())]
    assert len(calls)==len(observations)==2
    assert len({o.logical_call_id for o in observations})==1
    assert len({o.observation_id for o in observations})==2
    assert [o.attempt for o in observations]==[1,2]
    assert observations[0].usage.availability=='UNKNOWN' and observations[1].usage.input_tokens==10


@pytest.mark.asyncio
async def test_sdk_retry_exhaustion_keeps_all_unknown_attempts():
    seen=[];calls=[]
    def handler(req):
        calls.append(req)
        return httpx.Response(500,json={'error':{'code':500,'message':'private'}})
    options=types.HttpRetryOptions(attempts=3,initial_delay=.001,max_delay=.001,jitter=.001)
    async with fake_model(handler,retry_options=options) as (base,_):
        with observation_sink(seen.append):
            with pytest.raises(Exception):
                _=[x async for x in instrument_model(base,'incident_manager').generate_content_async(request())]
    assert len(calls)==len(seen)==3 and [o.attempt for o in seen]==[1,2,3]
    assert len({o.logical_call_id for o in seen})==1 and len({o.observation_id for o in seen})==3
    assert all(o.usage.input_tokens is None and o.usage.availability=='UNKNOWN' for o in seen)


@pytest.mark.asyncio
async def test_sdk_shape_mismatch_preserves_provider_execution(monkeypatch):
    import backend.observability.model_provider as provider
    monkeypatch.setattr(provider,'compatible',lambda:False)
    async with fake_model(lambda req:httpx.Response(200,json=payload())) as (base,client):
        observations=[]
        with observation_sink(observations.append):
            result=[x async for x in instrument_model(base,'team_manager','orchestration').generate_content_async(request())]
        assert result and observations==[] and not getattr(client._api_client,'_slopanoc_model_observed',False)


@pytest.mark.asyncio
async def test_embedding_split_requests_and_no_vectors():
    observations=[]; calls=[]
    def handler(req):
        calls.append(req)
        return httpx.Response(200,json={'embeddings':[{'values':[.1,.2]},{'values':[.3,.4]}]})
    async with fake_model(handler) as (_,client):
        with observation_sink(observations.append):
            result=await embedding_request(client,agent='knowledge_embedding',operation='embedding',
                model='text-embedding-005',contents=['document one','document two'])
    assert len(result.embeddings)==2 and len(calls)==len(observations)==1
    assert all(o.operation.value=='embedding' and o.usage.availability=='UNKNOWN' for o in observations)
    assert len({o.observation_id for o in observations})==1
    assert '.1' not in str([o.usage for o in observations])


@pytest.mark.asyncio
async def test_aiohttp_internal_retry_is_two_submissions(monkeypatch):
    import aiohttp
    import google.genai._api_client as api_module
    class Response(aiohttp.ClientResponse):
        status=200
        def __init__(self):self._closed=True;self._connection=None
        @property
        def headers(self):return {}
        async def text(self):return json.dumps(payload())
    class Session:
        calls=0
        async def close(self):pass
        async def request(self,*args,**kwargs):
            self.calls+=1
            if self.calls==1:raise aiohttp.ServerDisconnectedError('private transport detail')
            return Response()
    session=Session();delays=[]
    async def sleep(seconds):delays.append(seconds)
    async def get_session():return session
    monkeypatch.setattr(api_module.asyncio,'sleep',sleep)
    async with fake_model(lambda req: (_ for _ in ()).throw(AssertionError('httpx unused'))) as (base,client):
        monkeypatch.setattr(client._api_client,'_async_client_session_request_args',{},raising=False)
        monkeypatch.setattr(client._api_client,'_use_aiohttp',lambda:True)
        monkeypatch.setattr(client._api_client,'_get_aiohttp_session',get_session)
        observations=[]
        with observation_sink(observations.append):
            _=[x async for x in instrument_model(base,'incident_manager').generate_content_async(request())]
    assert session.calls==len(observations)==2 and len(delays)==1
    assert observations[0].usage.availability=='UNKNOWN' and observations[1].usage.input_tokens==10
