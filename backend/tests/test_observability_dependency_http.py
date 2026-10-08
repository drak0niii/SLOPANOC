import pytest
import requests
from types import SimpleNamespace
from backend.config.settings import Settings
from backend.gateway.power_automate_client import PowerAutomateClient
from backend.gateway.safe_error import SafeErrorException
from backend.observability.dependency_http import route_projection
from backend.observability.tool_instrumentation import tool_scope
from backend.tests._m5_dependencies import environment, spans, capture, points

SECRET = 'M5_GATEWAY_SENTINEL'

@pytest.mark.parametrize('operation', ['teams.getMessages', 'teams.listChats', 'teams.getMembers', 'teams.getHostedContent', 'teams.createChat', 'teams.sendMessage'])
@pytest.mark.parametrize('code,kind', [(200,None),(401,'auth'),(403,'auth'),(404,'not_found'),(429,'rate_limit'),(500,'provider'),(503,'provider')])
def test_gateway_actual_boundary_truthfulness_and_counts(operation, code, kind, monkeypatch):
    calls=[]
    def post(url, **kwargs):
        calls.append(kwargs)
        return SimpleNamespace(status_code=code, headers={'Retry-After':'2', 'Authorization':SECRET}, json=lambda:[{'body':SECRET}])
    monkeypatch.setattr('backend.gateway.power_automate_client.requests.post', post)
    client=PowerAutomateClient(Settings(env={'SLOPANOC_POWER_AUTOMATE_GATEWAY_URL':'https://secret-host/invoke?sig='+SECRET}))
    with environment() as (r,turn):
        with tool_scope('teams_get_messages'):
            if code >= 400:
                with pytest.raises(SafeErrorException):client._call(operation,{'chatId':SECRET,'message':SECRET})
            else:assert client._call(operation, {'chatId':SECRET})==[{'body':SECRET}]
        dep=spans(r,'http.client');tool=spans(r,'slopanoc.tool')
        assert len(dep)==len(calls)==1
        assert dep[0].parent.span_id==tool[0].context.span_id
        attrs=dep[0].attributes
        assert attrs['slopanoc.dependency']=='power_automate_gateway'
        assert attrs['http.response.status_code']==code
        assert attrs['http.route']==route_projection(operation)
        assert not any(str(v).startswith('GRAPH_') for v in attrs.values())
        assert 'graph' not in attrs.get('slopanoc.error_origin','')
        assert attrs['slopanoc.retry_visibility']=='unknown'
        if kind:
            assert attrs['slopanoc.failure_kind']==kind
            assert attrs['slopanoc.error_code']=='TOOL_ERROR'
        if code==429:
            assert attrs['slopanoc.retry_after_seconds']==2
            assert points(r,'slopanoc.dependency.rate_limits')
        assert SECRET not in capture(r)
        assert turn.snapshot().dependencies==()

@pytest.mark.parametrize('exc,kind', [(requests.ConnectTimeout('M5_SECRET_URL'),'connect_timeout'),(requests.ReadTimeout('M5_SECRET_URL'),'read_timeout'),(requests.ConnectionError('M5_SECRET_URL'),'connection')])
def test_gateway_typed_timeout_before_safeerror_conversion(exc,kind,monkeypatch):
    calls=[]
    def post(*args,**kwargs):calls.append(1);raise exc
    monkeypatch.setattr('backend.gateway.power_automate_client.requests.post',post)
    client=PowerAutomateClient(Settings(env={'SLOPANOC_POWER_AUTOMATE_GATEWAY_URL':'https://fake.invalid'}))
    with environment() as (r,t):
        with pytest.raises(SafeErrorException):client.list_chats()
        attrs=spans(r,'http.client')[0].attributes
        assert attrs['slopanoc.failure_kind']==kind
        assert attrs['slopanoc.error_code']==('TOOL_ERROR' if kind=='connection' else 'TOOL_TIMEOUT')
        assert len(calls)==1 and 'M5_SECRET_URL' not in capture(r)

@pytest.mark.parametrize('url',['https://user:pass@host/chats/19:secret/messages?token=SENTINEL#private','https://host/%2F%252F?sig=SENTINEL','\r\nSENTINEL','SENTINEL','https://host/customer/1234',''])
@pytest.mark.parametrize('operation',['not_registered','teams.getMessages'])
def test_route_projection_never_falls_back_to_url(url,operation):
    result=route_projection(operation,url)
    assert result==('unknown' if operation=='not_registered' else 'power_automate.teams.getMessages')
    assert 'SENTINEL' not in result

@pytest.mark.parametrize('retry_after',['secret', '-1', 'nan', 'inf', '3601', None])
def test_retry_after_fail_closed(retry_after,monkeypatch):
    from backend.observability.dependency_http import response_metadata
    from backend.observability.dependency_instrumentation import dependency_scope
    with environment() as (r,t):
        with dependency_scope('power_automate_gateway','teams.listChats','http.client') as scope:
            response_metadata(scope,SimpleNamespace(status_code=429,headers={'Retry-After':retry_after}))
        assert 'slopanoc.retry_after_seconds' not in spans(r,'http.client')[0].attributes

def test_actual_pagination_counts_calls_without_inventing_retries(monkeypatch):
    calls=[]
    def post(*args,**kwargs):
        calls.append(kwargs)
        return SimpleNamespace(status_code=200,headers={},json=lambda:[])
    monkeypatch.setattr('backend.gateway.power_automate_client.requests.post',post)
    client=PowerAutomateClient(Settings(env={'SLOPANOC_POWER_AUTOMATE_GATEWAY_URL':'https://fake.invalid'}))
    with environment() as (r,t):
        client.get_messages(SECRET)
        client.get_messages(SECRET,before=SECRET)
        assert len(calls)==len(spans(r,'http.client'))==2
        assert points(r,'slopanoc.dependency.pages')[-1].value==2
        assert not points(r,'slopanoc.dependency.retries')
        assert SECRET not in capture(r)
