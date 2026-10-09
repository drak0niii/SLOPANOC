from backend.tests.test_observability_api import api
import time,json,tracemalloc
from decimal import Decimal
import pytest
from backend.tests._m14_controls import *
from backend.observability.finops.control_repository import calculate
from backend.observability.finops.forecasting import forecast
from backend.observability.finops.forecast_backtesting import walk_forward
from backend.observability.finops.anomalies import evaluate
from backend.observability.finops.budgets import evaluate as budget_eval
from backend.observability.finops.cost_drivers import explain

def test_twenty_four_month_grouped_math_latency_memory_and_caps():
    issue=datetime(2026,10,20,tzinfo=timezone.utc);last=date(2026,10,17)
    ds=tuple(day(last-timedelta(days=i),'100') for i in reversed(range(731)))
    data=inputs(issue=issue,days=ds);b=budget('4000');reg=Registry(budgets=(b,))
    quality=dict(known_at=issue.isoformat(),origins=6,months=2,wape='.1',bias='0',relative_improvement='.2',same_folds=True)
    tracemalloc.start();timings={}
    calls={'budget':lambda:budget_eval(b,data),'tier1':lambda:forecast(data,b),'tier2':lambda:forecast(data,b,quality=quality),
        'anomalies':lambda:evaluate(data),'decomposition':lambda:explain(ds[-2],ds[-1],'EUR',Decimal(0)),
        'generation':lambda:[calculate(data,reg,POLICY,k) for k in ('BUDGET','FORECAST','ANOMALY')]}
    for key,call in calls.items():
        begin=time.perf_counter();call();timings[key]=round((time.perf_counter()-begin)*1000,3)
    peak=tracemalloc.get_traced_memory()[1];tracemalloc.stop()
    print('M14 731-day grouped local math ms',json.dumps(timings),'peak_bytes',peak)
    assert max(timings.values())<1000 and peak<POLICY.max_bytes

@pytest.mark.asyncio
async def test_local_asof_publication_and_backtest_latency(controls):
    for i in range(1,8):await financial(controls,date(2026,10,i))
    start=time.perf_counter();data=await controls.adapter.load('development',BINDING,'2026-10','EUR',ISSUE);asof=time.perf_counter()-start
    reg=Registry(budgets=(budget(),));result=calculate(data,reg,POLICY,'FORECAST');k,r=await controls.repo.reserve('development',BINDING,'2026-10','FORECAST')
    start=time.perf_counter();assert await controls.repo.publish(k,r,data,reg,POLICY,'FORECAST',result)=='PUBLISHED';publication=time.perf_counter()-start
    start=time.perf_counter();await walk_forward(controls.adapter,[ISSUE],ISSUE+timedelta(days=30),'development',BINDING,'EUR');backtest=time.perf_counter()-start
    start=time.perf_counter();await controls.repo.current('development',BINDING,'FORECAST');cached=time.perf_counter()-start
    print('M14 isolated SQLite ms',json.dumps(dict(asof=asof*1000,publication=publication*1000,backtest=backtest*1000,cached=cached*1000)))
    assert asof<2 and publication<POLICY.job_seconds and cached<.25

@pytest.mark.asyncio
async def test_full_twenty_four_month_asof_publication_and_protected_api(controls,api,monkeypatch):
    from backend.tests.test_observability_api import authorize
    from backend.observability.schemas import Role
    from backend.observability.finops.billing_sources import BillingSourceAdapter
    from backend.observability.finops.billing_service import FinancialSources
    from backend.observability.finops import control_service
    issue=datetime(2026,10,20,tzinfo=timezone.utc);last=date(2026,10,17)
    for i in reversed(range(731)):await financial(controls,last-timedelta(days=i))
    start=time.perf_counter();data=await controls.adapter.load('development',BINDING,'2026-10','EUR',issue);asof=time.perf_counter()-start
    reg=Registry(budgets=(budget('4000'),));result=calculate(data,reg,POLICY,'FORECAST');k,r=await controls.repo.reserve('development',BINDING,'2026-10','FORECAST')
    start=time.perf_counter();assert await controls.repo.publish(k,r,data,reg,POLICY,'FORECAST',result)=='PUBLISHED';publication=time.perf_counter()-start
    app,client,_,_,backend=api;authorize(app,Role.FINOPS);backend.control_repository=controls.repo
    backend.financial_sources=FinancialSources(adapters=[BillingSourceAdapter(scope='approved-synthetic-scope')]);monkeypatch.setattr(control_service,'now',lambda:issue)
    start=time.perf_counter();response=await client.get('/api/observability/finops/forecasts');protected=time.perf_counter()-start
    assert response.status_code==200,response.text
    assert response.json()['items'][0]['freshness']=='FRESH'
    print('M14 731-day SQLite/protected API ms',json.dumps(dict(asof=asof*1000,publication=publication*1000,protected_api=protected*1000)))
    assert asof<POLICY.job_seconds and publication<POLICY.job_seconds and protected<.25
