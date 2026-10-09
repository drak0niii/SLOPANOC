from decimal import Decimal
import pytest
from backend.tests._m14_controls import *
from backend.observability.finops.budgets import evaluate

@pytest.mark.parametrize('amount,state',[('49.999999999','HEALTHY'),('50','INFORMATIONAL'),('50.000000001','INFORMATIONAL'),('79.999999999','INFORMATIONAL'),('80','WATCH'),('80.000000001','WATCH'),('89.999999999','WATCH'),('90','AT_RISK'),('90.000000001','AT_RISK'),('99.999999999','AT_RISK'),('100','AT_LIMIT'),('100.000000001','EXCEEDED'),('120','EXCEEDED')])
def test_threshold_boundaries_exact_uncapped(amount,state):
    result=evaluate(budget(),inputs(n=1,value=amount))
    assert result['state']==state
    assert Decimal(result['utilization_pct'])==Decimal(amount)

def test_zero_currency_binding_partial_and_stale():
    assert evaluate(budget('0'),inputs(1,'50'))['positive_zero_budget_breach']
    assert evaluate(budget(currency='USD'),inputs(1,'50'))['state']=='CURRENCY_UNAVAILABLE'
    assert evaluate(budget(financial_binding='f'*64),inputs(1,'50'))['state']=='SCOPE_UNAVAILABLE'
    assert evaluate(budget(),inputs(1,'50',days=(day(date(2026,10,1),'40','PARTIAL',coverage='.4'),)))['state']=='RECONCILIATION_INCOMPLETE'
    assert evaluate(budget(),inputs(1,'50',current_health='STALE'))['state']=='SOURCE_STALE'

def test_calendar_leap_and_signed_credit():
    from backend.observability.finops.control_contracts import month_bounds
    assert (month_bounds('2024-02')[1]-month_bounds('2024-02')[0]).days==29
    assert month_bounds('2026-12')[1]==date(2027,1,1)
    assert evaluate(budget(),inputs(1,'-10'))['actual_eligible']=='-10'

def test_smallest_precision_boundary_and_tiny_positive_budget_are_uncapped():
    below='49.'+'9'*38
    assert evaluate(budget(),inputs(1,below))['state']=='HEALTHY'
    huge=evaluate(budget('0.'+'0'*37+'1'),inputs(1,'100'))
    assert huge['state']=='EXCEEDED' and Decimal(huge['utilization'])==Decimal('1e40')

def test_one_day_loses_scope_cannot_be_healthy_partial_population():
    ds=tuple(day(date(2026,10,i),'10',values={'TOTAL:EUR':'10','REQUEST_GENERAL:EUR':'10'} if i!=3 else {'TOTAL:EUR':'10'}) for i in range(1,8))
    result=evaluate(budget('1000',scope='REQUEST_GENERAL'),inputs(scope='REQUEST_GENERAL',days=ds))
    assert result['state']=='RECONCILIATION_INCOMPLETE' and result['utilization'] is None
