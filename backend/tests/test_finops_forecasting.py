from decimal import Decimal
import pytest
from backend.tests._m14_controls import *
from backend.observability.finops.forecasting import forecast

def test_seven_days_not_ten_exact_decomposition_and_low_scenario():
    f=forecast(inputs(),budget('4000'))
    assert Decimal(f['actual_to_cutoff'])==700 and Decimal(f['remaining'])==2400 and Decimal(f['forecast'])==3100
    assert f['eligible_days']==7 and f['tier']==1 and f['confidence']=='LOW' and f['range_type']=='SCENARIO_RANGE' and f['risk']=='UNCERTAIN'

def test_missing_open_are_unknown_not_zero():
    ds=[day(date(2026,10,i),'100') for i in (1,2,3,4,5,6,8)]
    ds.extend([Day(day=date(2026,10,7),state='MISSING'),Day(day=date(2026,10,9),state='TOO_RECENT'),Day(day=date(2026,10,10),state='OPEN')])
    f=forecast(inputs(days=tuple(ds)))
    assert Decimal(f['forecast'])==3100 and f['day_states']['MISSING']==1 and f['eligible_days']==7
    assert forecast(inputs(6))['forecast'] is None

def test_qualified_seasonality_and_future_rank_leakage():
    data=inputs(prior=56)
    quality=dict(known_at=ISSUE.isoformat(),origins=6,months=2,wape='0.1',bias='0',relative_improvement='.2',same_folds=True)
    assert forecast(data,quality=quality)['tier']==2
    assert forecast(data,quality=dict(quality,known_at=(ISSUE+timedelta(days=1)).isoformat()))['tier']==1
    assert forecast(inputs(prior=55),quality=quality)['tier']==1
    missing=data.days[0].model_copy(update={'state':'MISSING'})
    assert forecast(data.model_copy(update={'days':(missing,*data.days[1:])}),quality=quality)['tier']==1

def test_actual_breach_and_negative_rate():
    assert forecast(inputs(),budget('100'))['risk']=='ALREADY_EXCEEDED'
    assert forecast(inputs(value='-10'))['forecast'] is None
    assert forecast(inputs(current_health='STALE'))['state']=='SOURCE_STALE'
    assert forecast(inputs())==forecast(inputs())
