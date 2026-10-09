from copy import deepcopy
from decimal import Decimal, localcontext
import pytest
from backend.tests._m13_unit_economics import *
from backend.observability.finops.unit_economics import calculate
from backend.observability.finops.unit_drivers import distribute
from backend.observability.finops.billing_contracts import SourceError

def test_valid_accepted_direct_fully_loaded_exposure_and_source_immutability():
    data=economics_inputs();before=deepcopy(data)
    r=find(calculate(**data))
    assert Decimal(r['direct_cost_per_unit'])==6
    assert Decimal(r['fully_loaded_cost_per_unit'])==9
    assert r['unallocated_amount']=='10' and r['undistributed_shared']=='0'
    assert r['additive'] is False and r['state']=='PARTIAL'
    assert data==before

def test_denominator_does_not_authorize_numerator_and_classes_are_not_prorated():
    data=economics_inputs();data['drivers']=DriverRegistry(environment='development')
    r=find(calculate(**data))
    assert r['state']=='FINANCIAL_SCOPE_UNAVAILABLE' and r['direct_cost_per_unit'] is None
    assert r['direct_without_unit_scope']=='60'
    data=economics_inputs()
    for unit in ('GENERAL_TURN','TEAMS_LOOKUP_TURN'):
        r=find(calculate(**data),unit)
        assert r['state']=='FINANCIAL_SCOPE_UNAVAILABLE' and r['direct_cost_per_unit'] is None

def test_warmup_not_absorbed_into_turn_direct_numerator():
    data=economics_inputs(warmup='10');r=find(calculate(**data))
    assert Decimal(r['direct_cost_per_unit'])==6 and r['direct_without_unit_scope']=='10'

@pytest.mark.parametrize('cls',['GENERAL','TEAMS_LOOKUP','GOVERNED_TROUBLESHOOTING','COMPLEX_MULTI_AGENT'])
def test_explicit_class_scope_is_required_and_supported(cls):
    data=economics_inputs();turn=data['snapshots'][0]
    data['snapshots'][0]=turn.model_copy(update={'counts':dict(turn.counts,**{cls+'_TURN':50})})
    data['drivers']=DriverRegistry(environment='development',financial_scope=data['billing']['scope'],scope_rules=(ownership(request_class=cls),))
    r=find(calculate(**data),cls+'_TURN')
    assert Decimal(r['direct_cost_per_unit'])==Decimal('1.2')
    assert r['fully_loaded_state']=='PARTIAL'

def test_provider_attempt_and_logical_operation_average_are_distinct():
    result=calculate(**economics_inputs(direct='20'))
    a=find(result,'PROVIDER_ATTEMPT');o=find(result,'MODEL_OPERATION')
    assert Decimal(a['direct_cost_per_unit'])==Decimal('.2')
    assert Decimal(o['direct_cost_per_unit'])==Decimal('.25')
    assert a['price_semantics']=='AGGREGATE_AVERAGE'
    assert find(result,'PROVIDER_ATTEMPT','gemini-2.5-flash')['unit_count']==100

@pytest.mark.parametrize('count,state',[(0,'INSUFFICIENT_DATA'),(None,'DENOMINATOR_UNAVAILABLE')])
def test_zero_unknown_denominator(count,state):
    data=economics_inputs();snap=data['snapshots'][0]
    data['snapshots'][0]=snap.model_copy(update={'counts':dict(snap.counts,ACCEPTED_TURN=count)})
    r=find(calculate(**data));assert r['state']==state
    assert r['direct_cost_per_unit'] is None and r['fully_loaded_cost_per_unit'] is None

@pytest.mark.parametrize('source',['M9','M10'])
def test_stale_denominator_prevents_new_prices(source):
    data=economics_inputs();data['snapshots']=[s.model_copy(update={'freshness':'STALE'}) if s.source==source else s for s in data['snapshots']]
    r=find(calculate(**data),'ACCEPTED_TURN' if source=='M9' else 'PROVIDER_ATTEMPT')
    assert r['state']=='SOURCE_STALE' and r['direct_cost_per_unit'] is None

def test_stale_or_unreconciled_financial_source_cannot_be_available():
    data=economics_inputs();data['financial_result']['quality']['billing']='STALE'
    assert find(calculate(**data))['state']=='SOURCE_STALE'
    data=economics_inputs();data['financial_result']['status']='VARIANCE_REVIEW'
    assert find(calculate(**data))['state']=='RECONCILIATION_INCOMPLETE'

def test_missing_driver_keeps_pool_visible_and_partial():
    data=economics_inputs();data['drivers']=data['drivers'].model_copy(update={'drivers':()})
    r=find(calculate(**data));assert r['distributed_shared']=='0' and r['undistributed_shared']=='30'
    assert r['fully_loaded_state']=='PARTIAL' and Decimal(r['fully_loaded_cost_per_unit'])==6

def test_two_views_are_alternatives_not_accounting_totals():
    data=economics_inputs();data['drivers']=data['drivers'].model_copy(update={'drivers':(
        data['drivers'].drivers[0],Driver(category='CLOUD_SQL',ownership=ownership('shared','PROVIDER_POPULATION'),units=('PROVIDER_ATTEMPT',),driver='PROVIDER_ATTEMPTS',version='provider-fixture-v1'))})
    result=calculate(**data)
    for unit in ('ACCEPTED_TURN','PROVIDER_ATTEMPT'):
        assert Decimal(find(result,unit)['distributed_shared'])==30
        assert not find(result,unit)['additive']

@pytest.mark.parametrize('amount',['1','-1','0','0.00000000000000000000000000000000000001'])
def test_residual_exact_and_order_independent(amount):
    values,remainder=distribute(amount,{'c':1,'a':1,'b':1})
    with localcontext() as ctx:
        ctx.prec=100
        assert sum(map(Decimal,values.values()))+Decimal(remainder)==Decimal(amount)
    assert distribute(amount,{'b':1,'c':1,'a':1})==(values,remainder)
    if amount=='1':assert Decimal(values['a'])>Decimal(values['b'])

def test_conflicting_driver_fails_and_unapproved_categories_have_no_fallback():
    data=economics_inputs();driver=data['drivers'].drivers[0]
    with pytest.raises(ValueError):DriverRegistry(environment='development',drivers=(driver,driver))
    with pytest.raises(ValueError):Driver(category='CLOUD_SQL',ownership=ownership('shared'),units=('PROVIDER_ATTEMPT',),driver='ACCEPTED_TURNS',version='bad')

@pytest.mark.parametrize('unit',['RESOLVED_INCIDENT','FIRST_TIME_CORRECT','GOVERNED_ANSWER','AUTOMATION_ARTIFACT','SUCCESSFUL_REMEDIATION','TICKET'])
def test_outcomes_remain_unavailable(unit):
    r=find(calculate(**economics_inputs()),unit)
    assert r['state']=='DEFINED_NOT_AVAILABLE' and r['unit_count'] is None and r['direct_cost_per_unit'] is None

def test_currencies_credits_and_tax_preserve_classification():
    data=economics_inputs(direct='60',credits=[{'amount':'-10','type':'DISCOUNT'}],currency='USD')
    r=find(calculate(**data),currency='USD');assert Decimal(r['direct_cost_per_unit'])==5
    assert all(v['currency']=='USD' for v in calculate(**data)['results'])
    data=economics_inputs()
    # Explicitly sourced tax is still UNALLOCATED, never direct/fully-loaded.
    r,b,p=inputs([charge('60'),charge('10',cost_type='tax')]);reg=registry()
    data.update(runtime=r,billing=b,financial_registry=reg,financial_result=reconcile(r,b,p,reg))
    v=find(calculate(**data));assert v['unallocated_amount']=='10' and Decimal(v['direct_cost_per_unit'])==6

def test_cross_window_scope_digest_and_component_tamper_fail_closed():
    data=economics_inputs();data['snapshots'][1]=data['snapshots'][1].model_copy(update={'window_aligned':False})
    assert find(calculate(**data),'PROVIDER_ATTEMPT')['state']=='FINANCIAL_SCOPE_UNAVAILABLE'
    data=economics_inputs();data['billing']['scope']='f'*64
    with pytest.raises(SourceError):calculate(**data)
    data=economics_inputs();data['financial_result']['classified'][0]['amount']='999'
    with pytest.raises(SourceError):calculate(**data)

def test_available_success_and_terminal_counts_are_not_interchanged():
    result=calculate(**economics_inputs())
    assert find(result,'AVAILABLE_TURN')['unit_count']==8
    assert find(result,'TIMELY_TERMINAL_TURN')['unit_count']==9
    assert find(result,'AVAILABLE_TURN')['price_semantics']=='YIELD_ADJUSTED_AVERAGE'

def test_exact_replay_and_zero_net_negative_credit_controls():
    data=economics_inputs(direct='-60',shared='-30')
    a=calculate(**data);assert a==calculate(**deepcopy(data))
    assert Decimal(find(a)['direct_cost_per_unit'])==-6
    for c in a['distribution_controls']:
        assert Decimal(c['source'])==Decimal(c['distributed'])+Decimal(c['undistributed'])

def test_partial_shared_pool_is_twenty_distributed_ten_undistributed():
    data=economics_inputs(shared='20')
    r,b,p=inputs([charge('60'),charge('20',sku={'id':'shared'}),
        charge('10',sku={'id':'other-pool'}),charge('10',sku={'id':'unmapped'})])
    maps=list(data['financial_registry'].mappings)
    maps.append(maps[-1].model_copy(update={'sku':digest('other-pool')}))
    reg=FinancialRegistry(environment='development',runtime_scope=SCOPE,mappings=tuple(maps))
    data.update(runtime=r,billing=b,financial_registry=reg,financial_result=reconcile(r,b,p,reg))
    v=find(calculate(**data))
    assert v['distributed_shared']=='20' and v['undistributed_shared']=='10'
    assert v['fully_loaded_state']=='PARTIAL' and Decimal(v['fully_loaded_cost_per_unit'])==8
    assert Decimal(v['coverage']['shared_driver'])<1

def test_unknown_class_and_cross_midnight_prevent_exact_class_or_turn_price():
    data=economics_inputs()
    snap=data['snapshots'][0]
    data['drivers']=data['drivers'].model_copy(update={'scope_rules':(ownership(request_class='GENERAL'),)})
    data['snapshots'][0]=snap.model_copy(update={'counts':dict(snap.counts,UNKNOWN_TURN=1)})
    assert find(calculate(**data),'GENERAL_TURN')['direct_cost_per_unit'] is None
    data['snapshots'][0]=snap.model_copy(update={'window_aligned':False})
    assert find(calculate(**data))['direct_cost_per_unit'] is None

@pytest.mark.parametrize('override',[{'max_buckets':1},{'max_bytes':1024}])
def test_oversize_grouped_inputs_fail_without_truncation(override):
    from backend.observability.finops.unit_economics_contracts import Policy
    with pytest.raises(SourceError) as error:calculate(**economics_inputs(),policy=Policy(**override))
    assert error.value.code=='BATCH_LIMIT'

def test_cross_day_shared_pool_cannot_use_daily_turn_driver():
    data=economics_inputs()
    for b in data['billing']['buckets']:
        if b['sku']==digest('shared'):b['usage_end']=(WINDOW.end+timedelta(hours=1)).isoformat()
    v=find(calculate(**data))
    assert v['distributed_shared']=='0' and v['undistributed_shared']=='30'
    assert v['fully_loaded_state']=='PARTIAL'
