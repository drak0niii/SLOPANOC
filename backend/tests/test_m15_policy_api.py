import pytest
from backend.tests.test_observability_api import api,authorize
from backend.tests.test_m15_archive import request,MARKERS,TID
from backend.observability.archive import FilesystemArchive
from backend.observability.schemas import Role,Permission,ROLE_PERMISSIONS
from backend.observability.policy_views import policy_view
from backend.observability.config import ObservabilityConfig

@pytest.mark.asyncio
@pytest.mark.parametrize('role',list(Role))
async def test_policy_access_and_archive_capability_matrix(api,tmp_path,role):
    app,client,repo,state,backend=api
    archive=FilesystemArchive(tmp_path/'archive','development');app.state.observability_archive=archive
    commit,=archive.submit(request(environment='development').SerializeToString())
    authorize(app,role)
    policy=await client.get('/api/observability/policies')
    assert policy.status_code==(200 if Permission.EFFECTIVE_CONFIGURATION in ROLE_PERMISSIONS[role] else 403)
    access=await client.get('/api/observability/access')
    assert access.status_code==200
    assert set(access.json())=={'verified','capabilities','environments'}
    assert set(access.json()['capabilities'])=={v.value for v in ROLE_PERMISSIONS[role]}
    response=await client.get('/api/observability/archive/'+TID,params={'digest':commit.digest,'limit':1})
    assert response.status_code==(200 if Permission.TECHNICAL_DIAGNOSTICS in ROLE_PERMISSIONS[role] else 403)
    if response.status_code==200:
        value=response.json();assert len(value['spans'])==1 and value['next_offset']==1
        assert all(marker not in response.text for marker in MARKERS)

@pytest.mark.asyncio
async def test_new_routes_default_deny_environment_and_bounds(api,tmp_path):
    app,client,repo,state,backend=api
    for path in ('/policies','/access','/archive/'+TID+'?digest='+'a'*64):
        assert (await client.get('/api/observability'+path,headers={'X-SLOPANOC-DEV-USER':'admin','X-Role':'Admin'})).status_code==401
    authorize(app,Role.DEVELOPER_SRE,environments=frozenset({'production'}))
    assert (await client.get('/api/observability/archive/'+TID,params={'digest':'a'*64})).status_code==403
    authorize(app,Role.DEVELOPER_SRE)
    for params in ({'digest':'invalid'},{'digest':'a'*64,'limit':101},{'digest':'a'*64,'offset':10001}):
        assert (await client.get('/api/observability/archive/'+TID,params=params)).status_code==400

def test_effective_policy_and_config_never_reveal_secrets():
    config=ObservabilityConfig(otel_exporter_otlp_endpoint='https://private-endpoint.invalid',finops_billing_project='private-billing-project')
    output=policy_view(config).model_dump_json()+str(config.effective_configuration())
    assert 'private-endpoint' not in output and 'private-billing-project' not in output
    assert 'privacy-v1' in output and 'NOT_DEPLOYED' in output

@pytest.mark.asyncio
@pytest.mark.parametrize('environment',['staging','production'])
async def test_dev_identity_header_and_default_are_disabled_outside_local(monkeypatch,environment):
    from backend.api.identity import resolve_user_context
    from backend.config.settings import Settings
    monkeypatch.setattr('backend.config.settings.get_settings',lambda:Settings({'SLOPANOC_OTEL_ENVIRONMENT':environment}))
    for header in (None,'forged-admin'):
        with pytest.raises(Exception):await resolve_user_context(header)

@pytest.mark.asyncio
@pytest.mark.parametrize('role',list(Role))
async def test_run_retention_does_not_conflate_instrumentation_provider_or_archive(api,role):
    app,client,repo,state,backend=api
    authorize(app,role)
    response=await client.get(f'/api/observability/runs/{state.run_id}/retention')
    assert response.status_code==(200 if Permission.OPERATIONAL_METADATA in ROLE_PERMISSIONS[role] else 403)
    if response.status_code==200:
        assert response.json()=={'run_id':state.run_id,'instrumentation':'UNKNOWN','cloud_trace':'NOT_LIVE_CONFIRMED',
            'exceptional_archive':'UNAVAILABLE','production_binding':'NOT_DEPLOYED'}
    authorize(app,Role.DEVELOPER_SRE,environments=frozenset({'production'}))
    assert (await client.get(f'/api/observability/runs/{state.run_id}/retention')).status_code==404
