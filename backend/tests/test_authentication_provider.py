"""Shared server-owned LOCAL/DEV identity and deny-only future IAP boundary."""
import json
from unittest.mock import Mock
import pytest
from fastapi import FastAPI, Request
from backend.authentication import identity_provider, resolve_application_identity, validate_identity_configuration
from backend.observability.authorization import verified_principal, Principal
from backend.observability.schemas import Permission


def request(user=None, **headers):
    if user is not None: headers['X-SLOPANOC-DEV-USER'] = user
    return Request({'type':'http','app':FastAPI(),
        'headers':[(k.lower().encode(),v.encode()) for k,v in headers.items()]})


def policy(tmp_path, role='Operator', capabilities=('own_progress','operational_metadata'), environments=('development',)):
    file=tmp_path/'auth.json'
    file.write_text(json.dumps({'default_subject':'api-user','users':{
        'api-user':{'role':role,'capabilities':capabilities,'environments':environments}}}))
    return file


def configured(file):
    return {'SLOPANOC_AUTH_MODE':'development','SLOPANOC_DEV_AUTH_POLICY_FILE':str(file)}


@pytest.mark.asyncio
async def test_explicit_provider_same_subject_and_no_browser_authority(tmp_path):
    provider=identity_provider('development',configured(policy(tmp_path)))
    normal=await provider.authenticate(request())
    forged=await provider.authenticate(request('api-user', **{'X-Role':'Admin',
        'X-Capabilities':'financial_data,technical_diagnostics','X-Environment':'production'}))
    assert normal==forged and normal.subject=='api-user' and normal.valid()
    assert normal.capabilities==frozenset({Permission.OWN_PROGRESS,Permission.OPERATIONAL_METADATA})
    assert await provider.authenticate(request('unknown-user')) is None
    assert (await provider.authenticate(request('   ')))==normal


@pytest.mark.asyncio
@pytest.mark.parametrize('environment',['local','development','test','staging','production'])
async def test_unconfigured_provider_denies_all_headers(environment):
    principal=await identity_provider(environment,{}).authenticate(request('admin',
        **{'X-Goog-IAP-JWT-Assertion':'forged','X-Goog-Authenticated-User-Email':'accounts.google.com:admin@example.com'}))
    assert principal is None


@pytest.mark.parametrize('environment',['test','staging','production','invalid'])
def test_development_auth_never_allowed_in_unsafe_process(environment,tmp_path):
    with pytest.raises(ValueError): identity_provider(environment,configured(policy(tmp_path)))


@pytest.mark.asyncio
@pytest.mark.parametrize('environment',['local','development','production'])
async def test_iap_is_inert_even_with_assertion_or_email(environment):
    assert await identity_provider(environment,{'SLOPANOC_AUTH_MODE':'iap'}).authenticate(
        request(**{'X-Goog-IAP-JWT-Assertion':'forged','X-Goog-Authenticated-User-Email':'admin@example.com'})) is None


@pytest.mark.parametrize('change',[
    {'role':'User','capabilities':['financial_data']},
    {'role':'Developer / SRE','capabilities':['technical_diagnostics','financial_data']},
    {'environments':['production']},{'environments':[]},
    {'capabilities':['arbitrary']},{'role':'root'},
])
def test_invalid_grants_fail_closed(tmp_path,change):
    file=policy(tmp_path);value=json.loads(file.read_text());value['users']['api-user'].update(change)
    file.write_text(json.dumps(value))
    with pytest.raises(ValueError,match='Invalid development authentication policy'):
        identity_provider('development',configured(file))


@pytest.mark.parametrize('payload',['{}','not-json','{"users":{},"users":{}}','x'*65537])
def test_malformed_or_oversize_policy_rejected(tmp_path,payload):
    file=tmp_path/'bad.json';file.write_text(payload)
    with pytest.raises(ValueError):identity_provider('development',configured(file))


def test_missing_symlink_and_unknown_mode_rejected(tmp_path):
    missing=tmp_path/'missing';link=tmp_path/'link';link.symlink_to(policy(tmp_path))
    for file in (missing,link):
        with pytest.raises(ValueError):identity_provider('development',configured(file))
    with pytest.raises(ValueError):identity_provider('development',{'SLOPANOC_AUTH_MODE':'unknown'})


@pytest.mark.asyncio
async def test_policy_revocation_is_revalidated_on_next_request(tmp_path,monkeypatch):
    file=policy(tmp_path)
    for key,value in configured(file).items():monkeypatch.setenv(key,value)
    monkeypatch.setenv('SLOPANOC_OTEL_ENVIRONMENT','development')
    assert Permission.OPERATIONAL_METADATA in (await resolve_application_identity(request())).capabilities
    policy(tmp_path,capabilities=('own_progress',))
    assert Permission.OPERATIONAL_METADATA not in (await resolve_application_identity(request())).capabilities
    file.write_text('invalid')
    with pytest.raises(Exception):await resolve_application_identity(request())


@pytest.mark.asyncio
async def test_grant_must_authorize_actual_process_environment(tmp_path):
    provider=identity_provider('local',configured(policy(tmp_path)))
    with pytest.raises(Exception):await provider.authenticate(request())


@pytest.mark.asyncio
async def test_anoc_and_reader_share_same_provider_and_subject(tmp_path,monkeypatch):
    from backend.api.identity import resolve_user_context
    from backend.observability.authorization import verified_principal_provider
    file=policy(tmp_path)
    for key,value in configured(file).items():monkeypatch.setenv(key,value)
    monkeypatch.setenv('SLOPANOC_OTEL_ENVIRONMENT','development')
    principal=await verified_principal(await verified_principal_provider(request()))
    user=await resolve_user_context(principal)
    assert user.user_id==principal.subject=='api-user'


@pytest.mark.parametrize('marker',['K_SERVICE','GAE_ENV'])
def test_development_auth_rejected_on_hosted_runtime_even_if_env_mislabelled(tmp_path,marker):
    values=configured(policy(tmp_path));values[marker]='hosted-runtime'
    with pytest.raises(ValueError):identity_provider('development',values)


@pytest.mark.asyncio
@pytest.mark.parametrize('factory',['anoc','reader'])
async def test_startup_rejects_production_development_auth_before_lifecycle_work(monkeypatch,tmp_path,factory):
    from backend.tests._m7_storage import config
    for key,value in configured(policy(tmp_path)).items():monkeypatch.setenv(key,value)
    if factory=='reader':
        from backend.api.observability_app import create_app
        from backend.observability.read_config import ReadConfiguration
        app=create_app(config=config(otel_environment='production'),settings=ReadConfiguration())
        target='backend.observability.logging.ProductionLogging'
    else:
        from backend.api.app import create_app
        from backend.config.settings import Settings
        monkeypatch.setattr('backend.api.app.get_settings',lambda:Settings({'SLOPANOC_OTEL_ENVIRONMENT':'production'}))
        app=create_app();target='backend.api.app.warmup_shared_model'
    forbidden=Mock(side_effect=AssertionError('Lifecycle before auth guard'))
    monkeypatch.setattr(target,forbidden)
    with pytest.raises(ValueError,match='Invalid authentication mode'):
        async with app.router.lifespan_context(app):pass
    forbidden.assert_not_called()
