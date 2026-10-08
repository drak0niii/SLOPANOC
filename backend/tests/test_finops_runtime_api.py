"""Protected M10 reads through real routing and existing verified-principal gate."""
from datetime import timedelta
import pytest
from backend.tests.test_finops_runtime_ledger import accounting,identity,payload,started,NOW
from backend.tests.test_observability_api import api,authorize
from backend.observability.authorization import Principal, verified_principal_provider
from backend.observability.schemas import Role, ROLE_PERMISSIONS
from backend.observability.finops.slo_source import CompositeSource,AccountingSource

PATHS=['/api/observability/finops/runtime-usage','/api/observability/finops/ledger-health','/api/observability/finops/ledger-completeness']
@pytest.mark.asyncio
@pytest.mark.parametrize('role',list(Role))
@pytest.mark.parametrize('path',PATHS)
async def test_financial_capability_only(api,accounting,role,path):
    app,client,_,_,backend=api;db,repo,ledger=accounting
    backend.accounting=ledger;authorize(app,role)
    response=await client.get(path)
    assert response.status_code==(200 if role==Role.FINOPS else 403),response.text

@pytest.mark.asyncio
async def test_identity_environment_query_and_no_raw_content(api,accounting):
    app,client,_,_,backend=api;db,repo,ledger=accounting;backend.accounting=ledger
    for path in PATHS:
        assert (await client.get(path,headers={'X-Role':'FinOps','X-SLOPANOC-DEV-USER':'FinOps'})).status_code==401
    authorize(app,Role.FINOPS)
    for path in PATHS:assert (await client.get(path,params={'environment':'production'})).status_code==403
    for params in ({'group':'prompt'},{'limit':101},{'cursor':'malformed'},{'start':'2020-01-01T00:00:00Z','end':'2026-01-01T00:00:00Z'},{'start':'2026-01-01T00:00:00','end':'2026-01-02T00:00:00'}):
        response=await client.get(PATHS[0],params=params);assert response.status_code==400 or response.status_code==422,response.text
    # Financial identity cannot read technical diagnostics.
    assert (await client.get('/api/observability/runs')).status_code==403
    i=identity();await started(repo,i);await repo.capture(payload(i,None));await repo.materialize(i.event_id)
    response=await client.get(PATHS[0],params={'start':(NOW-timedelta(hours=1)).isoformat(),'end':NOW.isoformat()})
    assert response.status_code==200,response.text
    data=response.json();assert data['items'][0]['quantities']['input_tokens']['observed'] is None
    assert data['items'][0]['quantity_coverage']==0
    assert not any(key in response.text for key in ('attempt_id','run_id','prompt','session_id','currency','estimated_cost'))

@pytest.mark.asyncio
async def test_operational_m9_routes_receive_actual_accounting_source(api,accounting):
    app,client,_,_,backend=api;db,repo,ledger=accounting
    backend.accounting=ledger;backend.slo_provider=CompositeSource(None,AccountingSource(repo))
    await repo.checkpoint('development')
    authorize(app,Role.OPERATOR)
    response=await client.get('/api/observability/slos/cost_ledger_completeness')
    assert response.status_code==200,response.text
    assert response.json()['state']=='INSUFFICIENT_DATA' and response.json()['source_kind']=='DURABLE_ACCOUNTING'

@pytest.mark.asyncio
async def test_default_deny_unverified_finops_in_production(api,accounting):
    app,client,_,_,backend=api;backend.accounting=accounting[2]
    app.dependency_overrides[verified_principal_provider]=lambda:Principal('trusted',Role.FINOPS,ROLE_PERMISSIONS[Role.FINOPS],frozenset({'production'}),False)
    for path in PATHS:assert (await client.get(path)).status_code==401

@pytest.mark.asyncio
async def test_financial_and_operational_complete_card_share_backend_evaluation(api,accounting):
    from backend.observability.finops.repository import utcnow,minute
    app,client,_,_,backend=api;db,repo,ledger=accounting;backend.accounting=ledger
    backend.slo_provider=CompositeSource(None,AccountingSource(repo))
    now=utcnow();i=identity(started_at=minute(now)-timedelta(minutes=1))
    await repo.admit(i,now-timedelta(days=29));await repo.start(i,i.started_at)
    await repo.capture(payload(i,None));await repo.materialize(i.event_id);await repo.checkpoint('development')
    authorize(app,Role.FINOPS)
    financial=(await client.get(PATHS[2])).json()
    assert financial['state']=='HEALTHY' and financial['current_value']==1 and financial['good']==1
    authorize(app,Role.OPERATOR)
    operational=(await client.get('/api/observability/slos/cost_ledger_completeness')).json()
    assert operational['state']==financial['state'] and operational['eligible']==financial['eligible']
