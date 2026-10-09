from copy import deepcopy
from decimal import Decimal
from datetime import timedelta
import pytest
from backend.tests._m12_reconciliation import *
from backend.observability.finops.reconciliation import reconcile
from backend.observability.finops.allocation import validate
from backend.observability.finops.billing_contracts import SourceError


def test_exact_aggregate_does_not_invent_attempt_lineage():
    r,b,p=inputs();result=reconcile(r,b,p,registry())
    assert result['status']=='RECONCILED'
    assert result['amounts'][0]['direct']=='10' and result['amounts'][0]['net']=='10'
    assert result['quantities'][0]['difference']=='0' and result['quantities'][0]['confidence']=='AGGREGATED'
    assert result['amounts'][0]['catalog_reference'] is None
    assert Decimal(result['coverage']['runtime_quantity_known'])==1
    assert Decimal(result['coverage']['billing_quantity_known'])==1
    assert Decimal(result['coverage']['compatible_quantity'])==1
    assert Decimal(result['coverage']['price_reference'])==0
    assert result['coverage']['price_eligible_groups']==1
    assert result['controls']==dict(signed_net='PASS',components='PASS',absolute='PASS',count='PASS')

@pytest.mark.parametrize('quantity,billed,reason,difference',[(1000,'1100','QUANTITY_VARIANCE','100'),(None,'1000','RUNTIME_USAGE_UNKNOWN',None),(1000,None,'BILLING_QUANTITY_UNKNOWN',None),(0,'0',None,'0')])
def test_quantities_are_independent_and_unknown_not_zero(quantity,billed,reason,difference):
    r,b,p=inputs([charge(quantity=billed)],quantity=quantity);result=reconcile(r,b,p,registry())
    q=result['quantities'][0];assert q['reason']==reason and q['difference']==difference
    assert q['runtime_quantity']==(str(quantity) if quantity is not None else None)
    assert q['billing_quantity']==billed
    if quantity in (0,None):assert q['variance_pct'] is None
    assert result['amounts'][0]['net']=='10'
    if reason:assert result['status']!='RECONCILED'
    if quantity is None:assert Decimal(result['coverage']['runtime_quantity_known'])==0

@pytest.mark.parametrize('kind', ['tax','rounding_error'])
def test_nonusage_policy(kind):
    r,b,p=inputs([charge(cost_type=kind)]);out=reconcile(r,b,p,registry())
    assert out['amounts'][0]['unallocated']=='10' and out['amounts'][0]['direct']=='0'
    assert out['allocation'][0]['classification']=='UNALLOCATED'


def test_component_absolute_count_conservation_detects_cancellation_and_overlap():
    r,b,p=inputs([charge('100',credits=[dict(amount='-100',type='DISCOUNT',id='credit')])]);out=reconcile(r,b,p,registry())
    assert out['amounts'][0]['net']=='0' and out['amounts'][0]['absolute']=='200' and out['amounts'][0]['count']==2
    for bad in ([],out['classified']+[out['classified'][0]],out['classified'][:1]):
        with pytest.raises(SourceError,match='FINANCIAL_IMBALANCE'):validate(b['components'],bad)
    altered=deepcopy(out['classified']);altered[0]['amount']='0'
    with pytest.raises(SourceError):validate(b['components'],altered)


def test_corrections_multiplicity_native_currency_fractional_controls():
    rows=[charge('10'),charge('-10',cost_type='adjustment'),charge('5'),charge('0.1',currency='USD'),charge('0.2',currency='USD')]
    r,b,p=inputs(rows);out=reconcile(r,b,p,registry())
    amounts={a['currency']:a for a in out['amounts']}
    assert amounts['EUR']['net']=='5' and amounts['USD']['net']=='0.3' and amounts['USD']['eur_net'] is None
    assert sum(c['count'] for c in out['classified'])==5
    r,b,p=inputs([charge(),charge()]);out=reconcile(r,b,p,registry())
    assert out['amounts'][0]['net']=='20' and out['amounts'][0]['count']==2

@pytest.mark.parametrize('change,reason', [({'sku':{'id':'unknown'}},'SKU_UNMAPPED'),({'usage':{'amount':'1000','unit':'characters','amount_in_pricing_units':'1000','pricing_unit':'characters'}},'UNIT_MISMATCH'),({'usage_end_time':(JAN+timedelta(days=2)).isoformat()},'TEMPORAL_GRAIN_MISMATCH')])
def test_unknown_mapping_unit_and_period_do_not_disappear(change,reason):
    r,b,p=inputs([charge(**change)]);out=reconcile(r,b,p,registry())
    assert out['amounts'][0]['net']=='10' and reason in out['reasons'] and out['status']!='RECONCILED'
    if reason=='SKU_UNMAPPED':assert out['amounts'][0]['unallocated']=='10' and out['coverage']['mapping_amount']=='0.000000000000'


def test_missing_billing_and_unproven_scope():
    r,b,p=inputs([]);out=reconcile(r,b,p,registry())
    assert out['amounts']==[] and out['quantities'][0]['runtime_quantity']=='1000' and out['status']!='RECONCILED'
    r,b,p=inputs();out=reconcile(r,b,p,registry().model_copy(update={'runtime_scope':None}))
    assert out['amounts'][0]['unallocated']=='10' and 'SCOPE_UNPROVEN' in out['reasons']

@pytest.mark.parametrize('quality',[dict(debt=1),dict(global_debt=1),dict(pending=1),dict(coverage_complete=False),dict(available=False)])
def test_accounting_debt_prevents_full_reconciliation(quality):
    r,b,p=inputs(**quality);out=reconcile(r,b,p,registry())
    assert out['status']!='RECONCILED'


def test_stale_billing_and_independent_catalog_reference():
    r,b,p=inputs();assert reconcile(r,b,p,registry(),dict(billing='STALE',pricing='FRESH'))['status']!='RECONCILED'
    r,b,p=inputs([charge(consumption_model={'id':'DEFAULT'})]);reg=registry().model_copy(update={'mappings':(registry().mappings[0].model_copy(update={'consumption_model':digest('DEFAULT')}),)})
    from backend.observability.finops.pricing import normalize as prices
    from backend.tests._m11_financial import catalog
    row=catalog(pricing_unit='tokens');row['list_price']['tiered_rates']=row['list_price']['tiered_rates'][:1]
    p['prices']=list(prices(extraction([row])).prices)
    out=reconcile(r,b,p,reg,dict(billing='FRESH',pricing='FRESH'))
    assert Decimal(out['amounts'][0]['catalog_reference'])==100000 and out['amounts'][0]['net']=='10'
    assert Decimal(out['amounts'][0]['monetary_variance'])==-99990
    p['prices']=[];assert reconcile(r,b,p,registry())['status']=='RECONCILED'


def test_direct_shared_unallocated_and_detached_credits():
    rows=[charge('60'),charge('30',sku={'id':'shared'}),charge('10',sku={'id':'unknown'},credits=[dict(amount='-1',type='DISCOUNT')])]
    r,b,p=inputs(rows)
    direct=registry().mappings[0];shared=direct.model_copy(update=dict(sku=digest('shared'),field=None,classification='SHARED',evidence='SUPPORTING_POOL',category='CLOUD_SQL'))
    out=reconcile(r,b,p,registry().model_copy(update={'mappings':(direct,shared)}))
    a=out['amounts'][0];assert (a['direct'],a['shared'],a['unallocated'],a['net'])==('60','30','9','99')
    assert out['classified'][0]['classification'] in ('DIRECT','SHARED','UNALLOCATED')
    assert any(c['classification']=='SHARED' and c['driver']=='VERIFIED_POOL_MEMBERSHIP' for c in out['classified'])
    rows[-1]=charge('10',sku={'id':'unknown'})
    r,b,p=inputs(rows);out=reconcile(r,b,p,registry().model_copy(update={'mappings':(direct,shared)}))
    a=out['amounts'][0]
    assert (a['direct'],a['shared'],a['unallocated'],a['net'])==('60','30','10','100')
    assert out['controls']==dict(signed_net='PASS',components='PASS',absolute='PASS',count='PASS')


def test_permutation_determinism_and_control_failure():
    rows=[charge('10'),charge('-10',cost_type='adjustment'),charge('0.1'),charge('0.1')]
    r,b,p=inputs(rows);r2,b2,p2=inputs(list(reversed(rows)))
    assert reconcile(r,b,p,registry())==reconcile(r2,b2,p2,registry())
    b['controls'][0]['count']+=1
    with pytest.raises(SourceError):reconcile(r,b,p,registry())


def test_catalog_requires_complete_unique_mode_and_time_coverage():
    from backend.observability.finops.pricing import normalize as prices
    r,b,p=inputs([charge(consumption_model={'id':'DEFAULT'})]);m=registry().mappings[0].model_copy(update={'consumption_model':digest('DEFAULT')})
    reg=registry().model_copy(update={'mappings':(m,)})
    row=catalog(pricing_unit='tokens');row['list_price']['tiered_rates']=row['list_price']['tiered_rates'][:1]
    p['prices']=list(prices(extraction([row])).prices)
    quality=dict(billing='FRESH',pricing='FRESH')
    assert reconcile(r,b,p,reg,quality)['status']=='VARIANCE_REVIEW'
    p['prices'][0]['observed_to']=(JAN+timedelta(hours=6)).isoformat()
    assert reconcile(r,b,p,reg,quality)['amounts'][0]['catalog_reference'] is None
    p['prices']=list(prices(extraction([row,row])).prices)
    assert reconcile(r,b,p,reg,quality)['amounts'][0]['catalog_reference'] is None
    p['prices']=list(prices(extraction([row])).prices)
    assert reconcile(r,b,p,registry(),quality)['amounts'][0]['catalog_reference'] is None


def test_reference_rounding_and_true_percentages_are_server_owned():
    from backend.observability.finops.reconciliation_contracts import percentage
    assert percentage('100','1000')=='10.000000000000' and percentage('1','0') is None
    r,b,p=inputs([charge('0.1',quantity='1100')]);out=reconcile(r,b,p,registry())
    assert out['quantities'][0]['variance_pct']=='10.000000000000'
    assert out['amounts'][0]['percentages']['direct']=='100.000000000000'
    assert out['amounts'][0]['net']=='0.1'
    from backend.observability.finops.reconciliation import reference
    price=dict(sku=registry().mappings[0].sku,service=registry().mappings[0].service,unit='tokens',currency='EUR',kind='CATALOG_LIST_PRICE',consumption_model='DEFAULT',observed_from=JAN.isoformat(),observed_to=(JAN+timedelta(days=1)).isoformat(),tiers=[dict(lower='0',upper=None,account_price='1',unit_quantity='3')])
    assert reference([price],registry().mappings[0],'1',JAN,'EUR')=='0.33333333333333333333333333333333333333'
