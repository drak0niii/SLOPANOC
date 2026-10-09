from decimal import Decimal
import pytest
from backend.tests._m14_controls import *
from backend.observability.finops.forecast_backtesting import metrics,walk_forward

@pytest.mark.asyncio
async def test_walk_forward_separate_corrected_truth_and_quality(controls):
    for i in range(31):await financial(controls,date(2026,10,1)+timedelta(days=i))
    origins=[ISSUE,ISSUE+timedelta(days=7),ISSUE+timedelta(days=14)]
    truth=datetime(2026,11,5,tzinfo=timezone.utc)
    a=await walk_forward(controls.adapter,origins,truth,'development',BINDING,'EUR')
    assert a['quality']['origins']==3 and Decimal(a['quality']['wape'])==0 and Decimal(a['quality']['mae'])==0
    for score in a['scores']:await controls.repo.save_backtest('development',BINDING,score,truth)
    await financial(controls,date(2026,10,5),'300',known=truth+timedelta(days=1),correction=1)
    b=await walk_forward(controls.adapter,origins,truth+timedelta(days=2),'development',BINDING,'EUR')
    assert [s['forecast_generation'] for s in a['scores']]==[s['forecast_generation'] for s in b['scores']]
    assert a['scores'][0]['truth_vector']!=b['scores'][0]['truth_vector']
    assert Decimal(b['quality']['mae'])==200
    for score in b['scores']:await controls.repo.save_backtest('development',BINDING,score,truth+timedelta(days=2))
    from backend.observability.finops.control_models import BacktestRow
    async with controls.db.sessions() as s:assert len((await s.scalars(select(BacktestRow))).all())==6

def test_zero_truth_metrics_are_not_division_or_fake_quality():
    m=metrics([dict(prediction='10',actual='0',month='2026-10')])
    assert m['wape'] is None and m['bias'] is None and Decimal(m['mae'])==10
