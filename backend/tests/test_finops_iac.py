import pytest
from decimal import Decimal
from pathlib import Path
from backend.tests._m14_controls import *
from backend.observability.finops.finops_iac import validate,definitions,dry_run,RULES
from backend.observability.finops.forecasting import forecast
from backend.observability.finops.budgets import evaluate

def test_disabled_definitions_runbooks_static_terraform():
    assert validate('.') and len(definitions())==6
    tf=Path('infra/observability/terraform/monitoring/finops.tf').read_text()
    assert 'enabled               = false' in tf and 'notification_channels = []' in tf
    assert 'google_monitoring_notification_channel' not in tf

def test_proactive_local_matcher_before_budget_exhaustion_and_no_control_hooks():
    b=budget('2000');inp=inputs(prior=56)
    q=dict(known_at=ISSUE.isoformat(),origins=6,months=2,wape='.1',bias='0',relative_improvement='.2',same_folds=True)
    f=forecast(inp,b,quality=q);actual=evaluate(b,inp)
    assert Decimal(actual['actual_eligible'])<Decimal(b.amount) and f['risk']=='PROJECTED_OVER'
    assert dry_run([actual],[f],{'results':[]})==['projected-overrun']
    assert dry_run([dict(actual,state='EXCEEDED')],[f],{'results':[{'type':'RECONCILIATION_QUALITY'}]})==['stale-source']
    import ast
    for file in ['budgets.py','forecasting.py','anomalies.py','cost_drivers.py','control_worker.py']:
        source=Path('backend/observability/finops/'+file).read_text()
        imports=[n for n in ast.walk(ast.parse(source)) if isinstance(n,(ast.Import,ast.ImportFrom))]
        assert not any(any(x in ast.unparse(n) for x in ('google.cloud','backend.tools','backend.agents','subprocess','requests','httpx')) for n in imports)

@pytest.mark.asyncio
async def test_full_history_to_backtest_qualification_publication_and_proactive_alert(controls):
    from backend.observability.finops.control_worker import Worker
    from backend.observability.finops.billing_service import FinancialSources
    from backend.observability.finops.billing_sources import BillingSourceAdapter
    from backend.observability.finops.control_repository import calculate
    from backend.observability.finops.forecast_backtesting import walk_forward
    start=date(2026,3,1)
    for i in range((date(2026,10,8)-start).days):
        d=start+timedelta(days=i);await financial(controls,d,'50' if d.weekday()>=5 else '100')
    origins=[datetime(2026,m,d,6,tzinfo=timezone.utc) for m in (8,9) for d in (10,17,24)]
    scores=await walk_forward(controls.adapter,origins,ISSUE,'development',BINDING,'EUR')
    for score in scores['scores']:await controls.repo.save_backtest('development',BINDING,score,ISSUE)
    quality=await controls.repo.quality('development',BINDING,'TOTAL','EUR',ISSUE,POLICY)
    assert quality and quality['origins']==6 and quality['months']==2
    reg=Registry(budgets=(budget('2000'),))
    worker=Worker(controls.repo,FinancialSources(adapters=[BillingSourceAdapter(scope='approved-synthetic-scope')]),reg,environment='development')
    assert await worker.evaluate('development',BINDING,'2026-10','EUR',ISSUE,kind='FORECAST')=='PUBLISHED'
    assert await worker.evaluate('development',BINDING,'2026-10','EUR',ISSUE,kind='BUDGET')=='PUBLISHED'
    f=(await controls.repo.current('development',BINDING,'FORECAST'))[0][1].body['result']['items'][0]
    b=(await controls.repo.current('development',BINDING,'BUDGET'))[0][1].body['result']['items'][0]
    assert f['tier']==2 and f['risk']=='PROJECTED_OVER' and Decimal(b['actual_eligible'])<2000
    assert dry_run([b],[f],{'results':[]})==['projected-overrun']
    assert 'projected-overrun' in RULES and validate('.')
