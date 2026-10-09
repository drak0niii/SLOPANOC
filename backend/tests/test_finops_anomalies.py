from decimal import Decimal
import pytest
from backend.tests._m14_controls import *
from backend.observability.finops.anomalies import evaluate

def anomaly_input(values,states=None):
    issue=datetime(2026,10,20,tzinfo=timezone.utc);last=date(2026,10,17)
    ds=tuple(day(last-timedelta(days=len(values)-i-1),v,(states or {}).get(i,'ELIGIBLE_COMPLETE')) for i,v in enumerate(values))
    return inputs(issue=issue,days=ds)

def total_results(result):return [r for r in result['results'] if r['type']=='TOTAL_SPEND']

def test_stable_and_zero_mad_spike_absolute_floor():
    assert all(r['severity']=='NONE' for r in total_results(evaluate(anomaly_input(['100']*32))))
    r=total_results(evaluate(anomaly_input(['100']*31+['300'])))[-1]
    assert r['state']=='OBSERVED' and r['severity']=='WATCH'
    assert all(r['severity']=='NONE' for r in total_results(evaluate(anomaly_input(['0.01']*31+['0.03']))))

def test_step_distinct_days_duplicate_and_gap_break_persistence():
    inp=anomaly_input(['100']*31+['160']*3);r=total_results(evaluate(inp))[-1]
    assert r['persistence']==3 and r['state']=='ACTIVE' and r['severity']=='HIGH'
    assert evaluate(inp)==evaluate(inp)
    gap=anomaly_input(['100']*31+['160']*3,{32:'MISSING'})
    r=total_results(evaluate(gap))[-1];assert r['persistence']==1 and r['state']=='OBSERVED'

def test_seasonal_weekends_are_not_outliers():
    inp=anomaly_input(['100']*90)
    ds=tuple(d.model_copy(update={'values':{'TOTAL:EUR':'50' if d.day.weekday()>=5 else '100'}}) for d in inp.days)
    assert all(r['severity']=='NONE' for r in total_results(evaluate(inp.model_copy(update={'days':ds}))))

def test_source_quality_drop_precedes_savings_and_partial_unit_improvement():
    inp=anomaly_input(['100']*31+['40'])
    ds=(*inp.days[:-1],inp.days[-1].model_copy(update={'state':'PARTIAL','coverage':'.4','units':{}}))
    result=evaluate(inp.model_copy(update={'days':ds}))
    assert any(r['reasons']==['SOURCE_QUALITY_DEGRADATION'] for r in result['results'])
    assert not any(r['day']==ds[-1].day.isoformat() and r['type']=='TOTAL_SPEND' for r in result['results'])
    assert 'SAVINGS' not in str(result)

def test_correction_reason_not_operational_and_insufficient_is_not_no_anomalies():
    inp=anomaly_input(['100']*31+['300']);ds=(*inp.days[:-1],inp.days[-1].model_copy(update={'corrections':1}))
    r=total_results(evaluate(inp.model_copy(update={'days':ds})))[-1]
    assert r['reasons']==['BILLING_CORRECTION'] and r['state']=='OBSERVED'
    assert evaluate(inputs())['state']=='INSUFFICIENT_DATA'

def test_unallocated_partial_quality_surge_and_ratio_not_savings():
    inp=anomaly_input(['100']*31+['40']*3)
    ds=tuple(d.model_copy(update={'state':'PARTIAL','unallocated':{'EUR':'80' if i>=31 else '1'},'unallocated_ratio':{'EUR':'.8' if i>=31 else '.01'}}) for i,d in enumerate(inp.days))
    r=evaluate(inp.model_copy(update={'days':ds}))
    ua=[a for a in r['results'] if a['type']=='UNALLOCATED_EXPOSURE']
    assert ua and ua[-1]['state']=='ACTIVE' and ua[-1]['reasons']==['UNALLOCATED_INCREASE']
    assert all(a['type'] in ('UNALLOCATED_EXPOSURE','RECONCILIATION_QUALITY') for a in r['results'])

def test_m13_denominator_disappearance_is_quality_degradation():
    inp=anomaly_input(['100']*32)
    ds=tuple(d.model_copy(update={'units':{'PROVIDER_ATTEMPT:ALL:EUR':'1'},'unit_counts':{'PROVIDER_ATTEMPT:ALL:EUR':100}}) for d in inp.days[:-1])+(inp.days[-1],)
    result=evaluate(inp.model_copy(update={'days':ds}))
    assert any(r['day']==ds[-1].day.isoformat() and r['reasons']==['SOURCE_QUALITY_DEGRADATION'] for r in result['results'])

def test_unit_cost_requires_material_fixed_population_exposure_not_recreated_bill():
    inp=anomaly_input(['100']*35)
    ds=tuple(d.model_copy(update={'units':{'PROVIDER_ATTEMPT:ALL:EUR':'.3' if i>=32 else '.1'},'unit_counts':{'PROVIDER_ATTEMPT:ALL:EUR':1000}}) for i,d in enumerate(inp.days))
    r=evaluate(inp.model_copy(update={'days':ds}))
    unit=[x for x in r['results'] if x['type']=='UNIT_COST'][-1]
    assert unit['state']=='ACTIVE' and unit['impact_type']=='FIXED_POPULATION_UNIT_EXPOSURE' and Decimal(unit['impact'])==200
    assert unit['unit_population']==1000 and Decimal(unit['observed'])==Decimal('.3')
    # M12 financial actual remains 100 per day; no new bill is constructed from this sensitivity.
    assert all(d.values['TOTAL:EUR']=='100' for d in ds)
