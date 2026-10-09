"""A + forecast of dates not already represented in A; no missing-as-zero math."""
from datetime import timedelta
from decimal import Decimal,localcontext
from statistics import median
from .control_contracts import Policy,month_bounds,total,quotient
from .billing_contracts import decimal,money

def precise(value):
    with localcontext() as c:
        c.prec=100
        return money(value.quantize(Decimal('1e-38')))
from .forecast_registry import ALGORITHMS,qualify

def forecast(inputs,budget=None,policy=None,quality=None,candidate_tier=None):
    policy=policy or Policy();start,end=month_bounds(inputs.month);key=inputs.scope+':'+inputs.currency
    eligible=[d for d in inputs.days if d.eligible and key in d.values]
    current=[d for d in eligible if start<=d.day<end]
    mapping=current[-1].mapping_version if current else None
    prior=[d for d in eligible if d.day<start and d.mapping_version==mapping]
    current_dates={d.day for d in current}
    remaining=[start+timedelta(days=i) for i in range((end-start).days) if start+timedelta(days=i) not in current_dates]
    a=total(d.values[key] for d in current)
    result=dict(month=inputs.month,currency=inputs.currency,scope=inputs.scope,issue=inputs.issue.isoformat(),cutoff=inputs.cutoff.isoformat(),
        actual_to_cutoff=a if current else None,remaining=None,forecast=None,lower=None,upper=None,range_type='NONE',tier=0,
        algorithm=ALGORITHMS[0],confidence='UNAVAILABLE',state='INSUFFICIENT_DATA',risk='INSUFFICIENT_DATA',
        eligible_days=len(current),period_days=(end-start).days,prior_eligible_days=len(prior),freshness=inputs.current_health,
        backtest_quality=quality if quality and __import__('datetime').datetime.fromisoformat(quality['known_at'])<=inputs.issue else None,
        day_states={s:sum(d.state==s for d in inputs.days if start<=d.day<end) for s in ('ELIGIBLE_COMPLETE','OPEN','TOO_RECENT','STALE','MISSING','PARTIAL','EXCLUDED')},provisional=True)
    if inputs.current_health!='FRESH':result.update(state='SOURCE_STALE',risk='SOURCE_STALE');return result
    if policy.recorded_at>inputs.issue:result['state']='POLICY_UNAVAILABLE';return result
    if len(current)<policy.min_runrate_days:return result
    # Older unknown financial days cannot authorize an apparently healthy projection.
    if any(d.state in ('STALE','PARTIAL','EXCLUDED') and start<=d.day<inputs.cutoff.date()-timedelta(hours=policy.maturity_hours/24) for d in inputs.days):
        result.update(state='SOURCE_QUALITY_DEGRADATION',risk='UNCERTAIN');return result
    gaps=any((d.state=='MISSING' or d.eligible and key not in d.values) and start<=d.day<end for d in inputs.days) or len({d.mapping_version for d in current})>1
    with localcontext() as c:
        c.prec=100
        rate=decimal(a)/len(current);r=rate*len(remaining);tier=1
        if not gaps and len(prior)>=policy.seasonal_days and (candidate_tier==2 or qualify(quality,inputs.issue,policy)):
            train=prior[-policy.seasonal_days:];groups={w:[decimal(d.values[key]) for d in train if d.day.weekday()==w] for w in range(7)}
            if all(len(v)>=8 for v in groups.values()):r=sum((median(groups[d.weekday()]) for d in remaining),Decimal(0));tier=2
        point=decimal(a)+r
        if rate<0 or point<0:result['state']='UNSUPPORTED_NET_CREDIT_REGIME';return result
        spread=abs(r)*decimal(policy.scenario_fraction)
        result.update(forecast=precise(point),remaining=precise(r),lower=precise(point-spread),upper=precise(point+spread),
            range_type='SCENARIO_RANGE',tier=tier,algorithm=ALGORITHMS[tier],confidence='LOW' if tier==1 else 'QUALIFIED',state='AVAILABLE',risk='UNCERTAIN')
    if budget:
        result.update(budget_id=budget.budget_id,budget_version=budget.version,amount=budget.amount)
        amount=decimal(budget.amount)
        if budget.currency!=inputs.currency or budget.financial_binding!=inputs.binding or budget.scope!=inputs.scope:result['risk']='UNCERTAIN'
        elif decimal(a)>amount:result['risk']='ALREADY_EXCEEDED'
        elif decimal(result['lower'])>amount:result['risk']='PROJECTED_OVER' if tier==2 else 'WATCH'
        elif decimal(result['upper'])>amount:result['risk']='WATCH'
        else:result['risk']='ON_TRACK' if tier==2 else 'UNCERTAIN'
    return result
