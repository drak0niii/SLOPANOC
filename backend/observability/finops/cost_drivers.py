"""Deterministic numeric explanations; correlations never claim causality."""
from decimal import Decimal,localcontext
from .billing_contracts import decimal,money

def explain(previous,current,currency,observed_delta):
    reasons=[];contributors=[];explained=Decimal(0)
    if current.corrections:reasons.append('BILLING_CORRECTION')
    if current.state in ('STALE','MISSING','PARTIAL') or previous.coverage is not None and current.coverage is not None and decimal(current.coverage)<decimal(previous.coverage):
        return dict(reasons=['SOURCE_QUALITY_DEGRADATION'],contributors=[],residual=money(observed_delta))
    with localcontext() as ctx:
        ctx.prec=100
        if previous.mix_proven and current.mix_proven and set(previous.mix)==set(current.mix):
            n0=sum(decimal(v[0]) for v in previous.mix.values());n1=sum(decimal(v[0]) for v in current.mix.values())
            if n0>0 and n1>0 and all(decimal(previous.mix[k][0])>0 and decimal(current.mix[k][0])>0 for k in current.mix):
                volume=mix=price=Decimal(0)
                for k in sorted(current.mix):
                    q0,a0=map(decimal,previous.mix[k]);q1,a1=map(decimal,current.mix[k]);s0,s1=q0/n0,q1/n1;p0,p1=a0/q0,a1/q1
                    volume+=(n1-n0)*((s0*p0+s1*p1)/3+(s1*p0+s0*p1)/6)
                    mix+=(s1-s0)*((n0*p0+n1*p1)/3+(n1*p0+n0*p1)/6)
                    price+=(p1-p0)*((n0*s0+n1*s1)/3+(n1*s0+n0*s1)/6)
                for name,value in [('USAGE_VOLUME',volume),('WORKLOAD_MIX',mix),('UNIT_PRICE',price)]:
                    amount=value.quantize(Decimal('1e-38'));contributors.append(dict(driver=name,amount=money(amount)));explained+=amount
                if mix!=0:reasons.append('WORKLOAD_MIX_SHIFT')
                if volume>0:reasons.append('USAGE_VOLUME_INCREASE')
                if price!=0:reasons.append('PRICE_CHANGE')
        for key in sorted(set(previous.factors)&set(current.factors)) if not previous.mix_proven or not current.mix_proven else []:
            if not key.endswith(':'+currency):continue
            q0,p0=map(decimal,previous.factors[key]);q1,p1=map(decimal,current.factors[key])
            volume=(q1-q0)*(p1+p0)/2;price=(p1-p0)*(q1+q0)/2
            if volume>0:reasons.append('USAGE_VOLUME_INCREASE')
            if price!=0:reasons.append('PRICE_CHANGE')
            contributors.extend([dict(driver='USAGE_VOLUME',amount=money(volume)),dict(driver='UNIT_PRICE',amount=money(price))]);explained+=volume+price
        for key,reason in [('CLASS_SHARED:'+currency,'SHARED_INFRASTRUCTURE_CHANGE')]:
            if key in previous.values and key in current.values:
                delta=decimal(current.values[key])-decimal(previous.values[key])
                if delta!=0:reasons.append(reason);contributors.append(dict(driver='SHARED_INFRASTRUCTURE',amount=money(delta)));explained+=delta
        if currency in previous.unallocated and currency in current.unallocated:
            delta=decimal(current.unallocated[currency])-decimal(previous.unallocated[currency])
            if delta!=0:reasons.append('UNALLOCATED_MOVEMENT');contributors.append(dict(driver='UNALLOCATED',amount=money(delta)));explained+=delta
    residual=decimal(observed_delta)-explained
    if residual!=0 or not reasons:reasons.append('UNEXPLAINED')
    return dict(reasons=sorted(set(reasons)),contributors=contributors,residual=money(residual))
