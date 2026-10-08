from datetime import timedelta
from decimal import Decimal
import pytest
from backend.tests.test_finops_billing_ingestion import financial
from backend.tests.test_observability_api import api,authorize
from backend.tests._m11_financial import *
from backend.observability.schemas import Role,ROLE_PERMISSIONS
from backend.observability.authorization import Principal,verified_principal_provider

PATHS=['/api/observability/finops/billing-health','/api/observability/finops/billing-summary','/api/observability/finops/pricing-status']
@pytest.mark.asyncio
@pytest.mark.parametrize('role',list(Role))
@pytest.mark.parametrize('path',PATHS)
async def test_financial_capability_only(api,role,path):
    app,client,_,_,backend=api;authorize(app,role)
    r=await client.get(path)
    assert r.status_code==(200 if role==Role.FINOPS else 403),r.text

@pytest.mark.asyncio
async def test_authentication_scope_and_unconfigured_is_not_zero(api):
    app,client,_,_,backend=api
    for path in PATHS:assert (await client.get(path,headers={'X-Role':'FinOps'})).status_code==401
    app.dependency_overrides[verified_principal_provider]=lambda:Principal('fake',Role.FINOPS,ROLE_PERMISSIONS[Role.FINOPS],frozenset({'production'}),False)
    for path in PATHS:assert (await client.get(path)).status_code==401
    authorize(app,Role.FINOPS)
    for path in PATHS:assert (await client.get(path,params={'environment':'production'})).status_code==403
    r=await client.get(PATHS[1]);assert r.json()['source']['state']=='NOT_CONFIGURED' and r.json()['amounts'] is None
    for params in [{'period_basis':'sql'},{'start':'2025-01-01','end':'2026-01-01'},{'period_basis':'invoice','invoice_month':'202613'},{'limit':101},{'invoice_month':'202601'}]:
        assert (await client.get(PATHS[1],params=params)).status_code in (400,422)

@pytest.mark.asyncio
async def test_actual_decimals_invoice_usage_multicurrency_privacy_and_pricing(api,financial):
    app,client,_,_,backend=api;db,repo,a,p,w,s=financial;backend.financial_sources=s;authorize(app,Role.FINOPS)
    a.rows=(billing('10'),billing('-10','202602',cost_type='adjustment'),billing('5','202602'),billing('0.1',currency='USD'),billing('0.2',currency='USD'))
    await w.ingest(a.source,WINDOW);await w.ingest(p.source,WINDOW)
    usage=await client.get(PATHS[1],params={'start':'2026-01-10','end':'2026-01-11'})
    assert usage.status_code==200,usage.text
    amounts={x['currency']:x for x in usage.json()['amounts']}
    assert amounts['EUR']['net']=='5' and amounts['USD']['net']=='0.3' and amounts['USD']['eur_net'] is None
    assert usage.json()['coverage_complete'] and usage.json()['source']['mode']=='TEST_FIXTURE'
    invoice=await client.get(PATHS[1],params={'period_basis':'invoice','invoice_month':'202602'})
    assert invoice.json()['amounts'][0]['net']=='-5' and not invoice.json()['coverage_complete']
    price=(await client.get(PATHS[2])).json();assert price['amount_basis']=='PRICING_EXPORT_CATALOG' and price['earliest_pricing_as_of']
    for text in (usage.text,invoice.text,str(price)):
        assert not any(v in text for v in ('ACCOUNT_SENTINEL','PROJECT_SENTINEL','RESOURCE_SENTINEL','SECRET_SENTINEL','TAG_SENTINEL','SELECT','sku-input'))
    assert (await client.get('/api/observability/runs')).status_code==403

@pytest.mark.asyncio
async def test_source_outage_last_data_and_price_outage_independent(api,financial):
    app,client,_,_,backend=api;db,repo,a,p,w,s=financial;backend.financial_sources=s;authorize(app,Role.FINOPS)
    await w.ingest(a.source,WINDOW);await w.ingest(p.source,WINDOW)
    p.fail=True;await w.ingest(p.source,WINDOW)
    response=await client.get(PATHS[1],params={'start':'2026-01-10','end':'2026-01-11'})
    assert response.json()['amounts'][0]['net']=='10'
    assert (await client.get(PATHS[2])).json()['source']['state']=='STALE'
    a.fail=True;await w.ingest(a.source,WINDOW)
    response=await client.get(PATHS[1],params={'start':'2026-01-10','end':'2026-01-11'})
    assert response.json()['source']['state']=='STALE' and response.json()['amounts'][0]['net']=='10'

@pytest.mark.asyncio
async def test_invoice_no_coverage_returns_null_not_zero_and_labels_correct_period(api,financial):
    app,client,_,_,backend=api;db,repo,a,p,w,s=financial;backend.financial_sources=s;authorize(app,Role.FINOPS)
    await w.ingest(a.source,WINDOW)
    data=(await client.get(PATHS[1],params={'period_basis':'invoice','invoice_month':'202602'})).json()
    assert data['amounts'] is None and data['start']=='2026-02-01' and data['end']=='2026-03-01'
