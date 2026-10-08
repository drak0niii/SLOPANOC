from copy import deepcopy
from dataclasses import replace
from decimal import Decimal
import pytest
from backend.tests._m11_financial import *
from backend.observability.finops.billing_normalization import normalize,detailed
from backend.observability.finops.billing_contracts import decimal,SourceError,Policy
from backend.observability.finops.pricing import normalize as prices,lookup
from backend.observability.finops.billing_sources import normalize_focus

def test_multiplicity_permutation_and_exact_credit_adjustment_control():
    a=billing();b=billing('5')
    p=normalize(extraction([a,a,b]),mapping())
    assert p.rows==3 and p.groups[0]['net']=='25'
    assert p.fingerprint==normalize(extraction([b,a,a]),mapping()).fingerprint
    assert p.fingerprint!=normalize(extraction([a,b]),mapping()).fingerprint
    p=normalize(extraction([billing('100',credits=[{'amount':'-12','type':'DISCOUNT'},{'amount':'-8','type':'PROMOTION'}]),billing('-5',cost_type='adjustment')]),mapping())
    assert p.groups[0]['gross']=='100' and p.groups[0]['credits']=='-20'
    assert p.groups[0]['adjustments']=='-5' and p.groups[0]['net']=='75'

@pytest.mark.parametrize('value',[0.1,float('nan'),True,'NaN','Infinity','1e39','0.'+'0'*38+'1'])
def test_no_float_or_lossy_overflow(value):
    with pytest.raises(SourceError):decimal(value)

def test_exact_precision_units_unknown_currencies_and_price_evidence_separation():
    row=detailed(billing('0.1'),mapping());assert row['usage_amount']=='0.1' and row['usage_unit']=='seconds'
    assert row['price_evidence']['effective_price']=='80'
    catalog_snapshot=prices(extraction([catalog()]))
    assert catalog_snapshot.prices[0]['tiers'][0]['account_price']=='100'
    assert row['price_evidence']['effective_price']=='80' # no mutation by catalog
    p=normalize(extraction([billing('0.1'),billing('0.2'),billing('100',currency='USD',sku={'id':'unknown'})]),mapping())
    assert {g['currency']:g['net'] for g in p.groups}=={'EUR':'0.3','USD':'100'}
    assert p.unmapped_rows==1 and any(g['category']=='UNMAPPED' for g in p.groups)
    assert not any(v in str(p) for v in ('ACCOUNT_SENTINEL','PROJECT_SENTINEL','RESOURCE_SENTINEL','SECRET_SENTINEL','TAG_SENTINEL'))

@pytest.mark.parametrize('field',['cost','currency','credits','usage','cost_type','export_time','invoice','sku','service'])
def test_required_field_missing_rejects(field):
    row=billing();del row[field]
    with pytest.raises(SourceError):normalize(extraction([row]))

def test_unused_schema_fields_and_credit_array_order_are_harmless():
    a=billing(credits=[{'amount':'-1','id':'a'},{'amount':'-2','id':'b'}]);b=deepcopy(a)
    b['arbitrary_new_column']={'secret':'POISON'};b['sku']['description']='changed';b['credits'].reverse()
    assert normalize(extraction([a])).fingerprint==normalize(extraction([b])).fingerprint

@pytest.mark.parametrize('kind,cost',[('regular','1'),('tax','2'),('adjustment','-3'),('rounding_error','0.01')])
def test_distinct_source_cost_type(kind,cost):
    p=normalize(extraction([billing(cost,cost_type=kind)]))
    assert p.controls[0]['cost_type']==kind and p.controls[0]['net']==cost

@pytest.mark.parametrize('changes',[{'complete':False},{'schema_version':2}])
def test_incomplete_snapshot_rejected(changes):
    with pytest.raises(SourceError):normalize(extraction([billing()],**changes))

def test_corrective_lines_keep_both_periods_and_original():
    original=billing('10')
    negation=billing('-10','202602',cost_type='adjustment',adjustment_info={'id':'correction','type':'USAGE_CORRECTION','mode':'COMPLETE_NEGATION_WITH_REMONETIZATION'})
    replacement=billing('5','202602',export_time='2026-02-15T00:00:00Z')
    p=normalize(extraction([original,negation,replacement]),mapping())
    assert sum(Decimal(g['net']) for g in p.groups)==5
    assert {g['invoice_month']:g['net'] for g in p.groups}=={'202601':'10','202602':'-5'}
    assert p.rows==3 and p.correction_rows==1 and p.late_rows==1
    assert original['cost']=='10'

def test_tier_preservation_effective_history_and_no_extrapolation():
    p=prices(extraction([catalog()]))
    selected=lookup(p.prices,'sku-input',JAN+timedelta(hours=12))
    assert selected['state']=='AVAILABLE' and len(selected['price']['tiers'])==2
    assert selected['price']['tiers'][0]['upper']=='100'
    assert lookup(p.prices,'sku-input',JAN-timedelta(seconds=1))['state']=='PRICE_UNAVAILABLE'
    assert lookup(p.prices,'sku-input',JAN+timedelta(days=1))['state']=='PRICE_UNAVAILABLE'
    later=prices(extraction([catalog('200',JAN+timedelta(days=1))],Window(JAN+timedelta(days=1),JAN+timedelta(days=2))))
    combined=p.prices+later.prices
    assert lookup(combined,'sku-input',JAN+timedelta(hours=1))['price']['tiers'][0]['account_price']=='100'
    assert lookup(combined,'sku-input',JAN+timedelta(days=1,hours=1))['price']['tiers'][0]['account_price']=='200'
    assert lookup(combined,'unknown',JAN)['state']=='PRICE_UNAVAILABLE'

def test_account_and_consumption_model_prices_are_independent():
    row=catalog();row['billing_account_price']=deepcopy(row['list_price']);row['billing_account_price']['tiered_rates'][0]['account_currency_amount']='80'
    row['consumption_model_prices']=[dict(consumption_model_id='BATCH',list_price=deepcopy(row['list_price']))]
    p=prices(extraction([row]))
    assert len(p.prices)==3
    assert lookup(p.prices,'sku-input',JAN,kind='CATALOG_ACCOUNT_PRICE')['price']['tiers'][0]['account_price']=='80'

@pytest.mark.parametrize('field',['pricing_as_of_time','pricing_unit','list_price','sku','account_currency_code'])
def test_required_price_fields(field):
    row=catalog();del row[field]
    with pytest.raises(SourceError):prices(extraction([row]))

def test_optional_focus_does_not_become_actual_billing():
    focus=dict(BillingAccountId='ACCOUNT_SENTINEL',BillingCurrency='EUR',BillingPeriodStart=JAN.isoformat(),BillingPeriodEnd=(JAN+timedelta(days=30)).isoformat(),ChargePeriodStart=JAN.isoformat(),ChargePeriodEnd=(JAN+timedelta(hours=1)).isoformat(),x_ExportTime=NOW.isoformat(),BilledCost='999',EffectiveCost='998',ListCost='1000',ContractedCost='999',ServiceName='Google AI',SkuId='sku-input')
    p=normalize_focus(extraction([focus,focus]))
    assert p.controls[0]['billed']=='1998' and p.groups==()
    assert normalize(extraction([billing()])).groups[0]['net']=='10'

def test_null_tax_dimensions_and_high_precision_are_preserved():
    r=billing('1',cost_type='tax',service={'id':None},sku={'id':None})
    p=normalize(extraction([r]));assert p.groups[0]['tax']=='1' and p.groups[0]['category']=='UNMAPPED'
    maximum='9'*38
    assert format(decimal(maximum),'f')==maximum

def test_billing_line_list_cost_separate_from_actual():
    r=detailed(billing('80',cost_at_list='100'),mapping())
    assert r['cost']=='80' and r['line_reference_amounts']['cost_at_list']=='100'


def test_fingerprint_collision_rejects_without_merging(monkeypatch):
    import backend.observability.finops.billing_normalization as normalization
    monkeypatch.setattr(normalization,'digest',lambda value:'0'*64)
    with pytest.raises(SourceError,match='HASH_COLLISION'):normalize(extraction([billing('1'),billing('2')]))

def test_unknown_tax_usage_remains_null_without_invented_quantity():
    r=billing('1',cost_type='tax',usage={'amount':None,'unit':None,'amount_in_pricing_units':None,'pricing_unit':None})
    normalized=detailed(r,mapping())
    assert normalized['usage_amount'] is None and normalized['pricing_amount'] is None
    assert normalize(extraction([r])).groups[0]['tax']=='1'
