from datetime import timedelta
from types import SimpleNamespace
import pytest
from backend.tests.test_observability_api import api,authorize
from backend.observability.authorization import Principal,verified_principal_provider
from backend.observability.schemas import Role,ROLE_PERMISSIONS,Permission
from backend.observability.slo_sources import FixtureProvider,subject_digest
from backend.observability.slo_evaluator import Counts
from backend.observability.slo_rollups import Rollups
from backend.tests.test_observability_slo_evaluator import source,NOW
from backend.tests.test_observability_slo_rollups import receipt

@pytest.mark.asyncio
@pytest.mark.parametrize('role',list(Role))
@pytest.mark.parametrize('path',['/api/observability/slos','/api/observability/slos/availability'])
async def test_slo_capability_gate(api,role,path):
    app,client,repo,s,backend=api;authorize(app,role)
    response=await client.get(path)
    assert response.status_code==(200 if Permission.OPERATIONAL_METADATA in ROLE_PERMISSIONS[role] else 403)

@pytest.mark.asyncio
async def test_default_denied_dev_headers_and_environment(api):
    app,client,repo,s,backend=api
    for path in ['/api/observability/slos','/api/observability/slos/availability']:
        assert (await client.get(path,headers={'X-SLOPANOC-DEV-USER':'admin','X-Role':'Admin'})).status_code==401
    authorize(app)
    assert (await client.get('/api/observability/slos?environment=production')).status_code==403
    assert (await client.get('/api/observability/slos/unknown')).status_code==404

@pytest.mark.asyncio
async def test_api_formula_states_safe_bounded_dto(api,monkeypatch):
    app,client,repo,s,backend=api;authorize(app)
    monkeypatch.setattr('backend.observability.slo_service.now_utc',lambda:NOW)
    # Explicit clock through service wrapper avoids default argument binding.
    from backend.observability.slo_service import slos
    principal=Principal('trusted',Role.OPERATOR,ROLE_PERMISSIONS[Role.OPERATOR],frozenset({'development'}),True)
    for counts,expected in [(Counts(good=1000),'HEALTHY'),(Counts(bad=1000),'BREACHED'),(Counts(),'INSUFFICIENT_DATA')]:
        backend.slo_provider=FixtureProvider({('development','availability'):source(counts)})
        result=await slos(backend,principal,slo_id='availability',clock=lambda:NOW)
        assert result.state.value==expected
        assert 'run_id' not in result.model_dump() and 'prompt' not in result.model_dump()
    backend.slo_provider=FixtureProvider({('development','availability'):source(Counts(good=1000),updated_at=NOW-timedelta(seconds=301))})
    assert (await slos(backend,principal,slo_id='availability',clock=lambda:NOW)).state.value=='STALE_DATA'
    response=await client.get('/api/observability/slos')
    assert response.status_code==200 and len(response.json()['items'])<=32
    cost=next(x for x in response.json()['items'] if x['slo_id']=='cost_ledger_completeness')
    assert cost['current_value'] is None and cost['state']=='DEFINED_NOT_EVALUATED'

@pytest.mark.asyncio
async def test_receipt_verified_owner_idempotent_failure_is_diagnostic(api):
    app,client,repo,s,backend=api
    roll=Rollups(repo.sessions);r=receipt(emitted_at=__import__('backend.observability.slo_sources',fromlist=['now_utc']).now_utc(),sse_expected=True,transport='relay_succeeded',subject_digest=subject_digest('trusted'))
    await roll.save(r,NOW)
    backend.runtime=SimpleNamespace(sre=SimpleNamespace(rollups=roll))
    authorize(app,Role.USER)
    for _ in range(2):
        response=await client.post('/api/observability/sse-receipts',json={'run_id':r.run_id,'event':'message.completed'})
        assert response.status_code==204,response.text
    original=r.terminal_status
    backend.runtime.sre=None
    assert (await client.post('/api/observability/sse-receipts',json={'run_id':r.run_id,'event':'message.completed'})).status_code==503
    assert r.terminal_status==original
    assert (await client.post('/api/observability/sse-receipts',json={'run_id':r.run_id,'event':'message.completed','role':'Admin'})).status_code==400
