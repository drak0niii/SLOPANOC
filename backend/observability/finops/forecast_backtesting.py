"""Walk-forward predictions and separately versioned scoring truth, no future ranking."""
from datetime import timedelta
from decimal import Decimal
from .control_contracts import Policy,month_bounds,total,quotient
from .billing_contracts import decimal,digest,instant
from .forecasting import forecast

def metrics(scores):
    valid=[s for s in scores if s.get('prediction') is not None and s.get('actual') is not None]
    errors=[decimal(s['prediction'])-decimal(s['actual']) for s in valid]
    denominator=sum((abs(decimal(s['actual'])) for s in valid),Decimal(0))
    return dict(origins=len(valid),months=len({s['month'] for s in valid}),mae=quotient(sum(map(abs,errors)),len(valid)),
        wape=quotient(sum(map(abs,errors)),denominator),bias=quotient(sum(errors),denominator))

async def walk_forward(adapter,origins,truth_cutoff,environment,binding,currency,scope='TOTAL',policy=None,quality_records=()):
    policy=policy or Policy()
    if len(origins)>policy.max_backtest_origins:raise ValueError('Backtest limit')
    scores=[]
    truth_cutoff=instant(truth_cutoff)
    for issue in sorted({instant(t) for t in origins}):
        month=issue.strftime('%Y-%m');start,end=month_bounds(month)
        inputs=await adapter.load(environment,binding,month,currency,issue,scope=scope,policy=policy)
        # Selection records themselves must have been known at origin; never latest quality.
        known=[q for q in quality_records if __import__('datetime').datetime.fromisoformat(q['known_at'])<=issue]
        quality=max(known,key=lambda q:q['known_at']) if known else None
        predicted=forecast(inputs,policy=policy,quality=quality)
        seasonal=forecast(inputs,policy=policy,candidate_tier=2)
        runrate=forecast(inputs,policy=policy,candidate_tier=1)
        prediction_id=digest([inputs.identity,predicted])
        truth=await adapter.load(environment,binding,month,currency,truth_cutoff,scope=scope,policy=policy)
        days=[d for d in truth.days if start<=d.day<end];key=scope+':'+currency
        complete=truth_cutoff.date()>=end and len(days)==(end-start).days and all(d.eligible and key in d.values for d in days)
        actual=total(d.values[key] for d in days) if complete else None
        score=dict(kind='RETROSPECTIVE_WALK_FORWARD',month=month,issue=issue.isoformat(),forecast_generation=prediction_id,
            prediction=predicted['forecast'],actual=actual,truth_vector=[d.generation for d in days],truth_cutoff=truth_cutoff.isoformat(),
            scoring_version='walk-forward-score-v1',model=predicted['algorithm'],input_generation=inputs.identity,
            tier1=runrate['forecast'],tier2=seasonal['forecast'] if seasonal['tier']==2 else None,scope=scope,currency=currency,
            policy_id=policy.identity,policy=policy.model_dump(mode='json'),algorithm_version='forecast-candidates-v1',
            prediction_result=predicted,candidate_results={'tier1':runrate,'tier2':seasonal},
            input_vector=[d.generation for d in inputs.days if d.generation],unit_vector=[d.unit_generation for d in inputs.days if d.unit_generation])
        score['identity']=digest(score);scores.append(score)
    return dict(scores=scores,quality=metrics(scores))
