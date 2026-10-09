"""Exact utilization from compatible completed actuals; no financial control hooks."""
from decimal import Decimal,localcontext
from .control_contracts import total, quotient, month_bounds, Policy,ratio_value
from .billing_contracts import decimal,money

def evaluate(budget,inputs,policy=None):
    policy=policy or Policy();start,end=month_bounds(inputs.month)
    days=[d for d in inputs.days if start<=d.day<end and d.day<inputs.cutoff.date()]
    key=budget.scope+':'+budget.currency
    observed=[d.values[key] for d in days if key in d.values]
    eligible=[d.values[key] for d in days if d.eligible and key in d.values]
    actual=total(eligible) if eligible else None
    result=dict(budget_id=budget.budget_id,budget_version=budget.version,scope=budget.scope,currency=budget.currency,
        amount=budget.amount,month=inputs.month,basis=budget.basis,actual_observed=total(observed) if observed else None,
        actual_eligible=actual,utilization=None,utilization_pct=None,variance=None,state='INSUFFICIENT_DATA',
        freshness=inputs.current_health,coverage=quotient(len(eligible),len(days)),positive_zero_budget_breach=False,
        provisional=True,read_only=True)
    if policy.recorded_at>inputs.issue:
        result['state']='POLICY_UNAVAILABLE';return result
    if budget.environment!=inputs.environment or budget.financial_binding!=inputs.binding or budget.scope!=inputs.scope:
        result['state']='SCOPE_UNAVAILABLE';return result
    if not budget.effective_from<=start<end<=budget.effective_to or budget.recorded_at>inputs.issue or budget.policy_version!=policy.version:
        result['state']='SCOPE_UNAVAILABLE';return result
    if not observed:
        result['state']='SCOPE_UNAVAILABLE' if any('TOTAL:'+budget.currency in d.values for d in days) and budget.scope!='TOTAL' else 'CURRENCY_UNAVAILABLE' if any(d.values for d in days) else 'INSUFFICIENT_DATA';return result
    if inputs.current_health!='FRESH' or any(d.state=='STALE' for d in days):result['state']='SOURCE_STALE';return result
    # Recent/open financial days are disclosed and excluded; older gaps block healthy.
    if any(d.state in ('MISSING','PARTIAL','EXCLUDED') or d.eligible and key not in d.values for d in days):result['state']='RECONCILIATION_INCOMPLETE';return result
    if actual is None:return result
    amount=decimal(budget.amount)
    if amount==0:
        result.update(state='ZERO_BUDGET',positive_zero_budget_breach=decimal(actual)>0);return result
    with localcontext() as context:
        context.prec=200
        n=decimal(actual);u=Decimal(quotient(actual,amount));t=[decimal(x)*amount for x in policy.thresholds]
        # Classify by exact cross multiplication, even below display-ratio precision.
        state='EXCEEDED' if n>t[3] else 'AT_LIMIT' if n==t[3] else 'AT_RISK' if n>=t[2] else 'WATCH' if n>=t[1] else 'INFORMATIONAL' if n>=t[0] else 'HEALTHY'
        result.update(state=state,utilization=format(u,'f'),utilization_pct=ratio_value(format((n/amount*100).quantize(Decimal('1e-38')),'f')),variance=money(n-amount))
    return result
