"""Robust daily detectors with distinct eligible-interval persistence and quality precedence."""
from datetime import timedelta
from decimal import Decimal,localcontext
from statistics import median
from .control_contracts import Policy,quotient,month_bounds
from .billing_contracts import decimal,money
from .anomaly_registry import features,DETECTORS,VERSION
from .cost_drivers import explain

def _evaluate(inputs,policy=None):
    policy=policy or Policy();days=sorted(inputs.days,key=lambda d:d.day);results=[]
    mature=[d for d in days if d.day<inputs.cutoff.date()-timedelta(days=policy.maturity_hours/24)]
    # Evaluate recent three intervals plus previous history for persistence; bounded daily grain.
    start,end=month_bounds(inputs.month)
    candidates=[d for d in mature if start<=d.day<end];evaluated=0
    for current in candidates:
        past=[d for d in days if d.day<current.day];previous=past[-1] if past else None
        degradation=(inputs.current_health!='FRESH' or current.state in ('STALE','MISSING','PARTIAL')
            or previous and bool(set(previous.units)-set(current.units))
            or previous and previous.coverage is not None and current.coverage is not None and decimal(current.coverage)<decimal(previous.coverage))
        # Corrections and unallocated exposure remain first-class QUALITY signals.
        if current.corrections:
            evaluated+=1
            results.append(dict(type='BILLING_CORRECTION',dimension='CORRECTION',day=current.day.isoformat(),currency=inputs.currency,
                observed=str(current.corrections),baseline=None,deviation=None,absolute_deviation=None,relative_deviation=None,
                severity='WATCH',confidence='QUALITY',state='OBSERVED',reasons=['BILLING_CORRECTION'],persistence=0,baseline_days=0,label='QUALITY'))
        ua_history=[d for d in past if d.state in ('ELIGIBLE_COMPLETE','PARTIAL') and inputs.currency in d.unallocated and inputs.currency in d.unallocated_ratio]
        if current.state in ('ELIGIBLE_COMPLETE','PARTIAL') and inputs.currency=='EUR' and inputs.currency in current.unallocated and len(ua_history)>=policy.baseline_days:
            training=ua_history[-policy.baseline_days:];baseline=median(decimal(d.unallocated[inputs.currency]) for d in training)
            ratio_baseline=median(decimal(d.unallocated_ratio[inputs.currency]) for d in training)
            observed=decimal(current.unallocated[inputs.currency]);delta=observed-baseline
            ratio_delta=decimal(current.unallocated_ratio.get(inputs.currency,'0'))-ratio_baseline
            evaluated+=1
            if delta>=decimal(policy.watch_absolute_eur) and ratio_delta>=decimal(policy.unallocated_ratio_floor):
                persistence=0
                for d in reversed([*past,current]):
                    if d.day!=current.day-timedelta(days=persistence) or d.state not in ('ELIGIBLE_COMPLETE','PARTIAL') or inputs.currency not in d.unallocated:break
                    if decimal(d.unallocated[inputs.currency])-baseline<decimal(policy.watch_absolute_eur):break
                    persistence+=1
                    if persistence==3:break
                results.append(dict(type='UNALLOCATED_EXPOSURE',dimension='UNALLOCATED',day=current.day.isoformat(),currency=inputs.currency,
                    observed=money(observed),baseline=money(baseline),deviation=money(delta),absolute_deviation=money(abs(delta)),relative_deviation=quotient(delta,abs(baseline)),
                    severity='HIGH' if persistence>=2 else 'WATCH',confidence='QUALITY',state='ACTIVE' if persistence>=2 else 'OBSERVED',
                    reasons=['UNALLOCATED_INCREASE'],persistence=persistence,baseline_days=len(training),label='QUALITY'))
        if degradation:
            results.append(dict(type='RECONCILIATION_QUALITY',dimension='COVERAGE',day=current.day.isoformat(),currency=inputs.currency,
                observed=current.coverage,baseline=previous.coverage if previous else None,deviation=None,absolute_deviation=None,relative_deviation=None,
                severity='WATCH',confidence='QUALITY',state='OBSERVED',reasons=['SOURCE_QUALITY_DEGRADATION'],persistence=0,baseline_days=0,label='QUALITY'))
            continue
        if not current.eligible:continue
        results.append(dict(type='RECONCILIATION_QUALITY',dimension='COVERAGE',day=current.day.isoformat(),currency=inputs.currency,
            observed=current.coverage,baseline=previous.coverage if previous else None,deviation=None,absolute_deviation=None,relative_deviation=None,
            severity='NONE',confidence='HIGH',state='NORMAL',reasons=[],persistence=0,baseline_days=0,label='QUALITY'))
        for (kind,dimension),value in features(current,inputs.currency,inputs.scope).items():
            if value is None or kind in ('RECONCILIATION_QUALITY','UNALLOCATED_EXPOSURE'):continue
            eligible=[d for d in past if d.eligible and features(d,inputs.currency,inputs.scope).get((kind,dimension)) is not None and d.mapping_version==current.mapping_version]
            if len(eligible)<policy.baseline_days:continue
            training=eligible[-policy.baseline_days:];season=[d for d in eligible[-policy.seasonal_days:] if d.day.weekday()==current.day.weekday()]
            if len(eligible)>=policy.seasonal_days and len(season)>=8:training=season
            values=[decimal(features(d,inputs.currency,inputs.scope)[kind,dimension]) for d in training]
            b=median(values);mad=median([abs(v-b) for v in values]);observed=decimal(value);delta=observed-b;absolute=abs(delta)
            relative=Decimal(quotient(absolute,abs(b))) if b else None
            label=DETECTORS[kind][1];floor=decimal((policy.call_floor if dimension.endswith(':calls') else policy.usage_floor) if label=='USAGE' else policy.watch_absolute_eur)
            if label!='USAGE' and inputs.currency!='EUR':continue
            population=current.unit_counts.get(dimension+':'+inputs.currency,0) if kind=='UNIT_COST' else 0
            impact=absolute*population if kind=='UNIT_COST' else absolute
            noise_absolute=Decimal(policy.noise_absolute_eur)/(population or 1)
            noise=max(noise_absolute,abs(b)*decimal(policy.noise_relative))
            with localcontext() as ctx:
                ctx.prec=100
                score=absolute/(Decimal('1.4826')*mad if mad else noise)
            evaluated+=1
            significant=impact>=floor and (relative is None and b==0 or relative is not None and relative>=decimal(policy.watch_relative)) and score>=decimal(policy.watch_score)
            # Count consecutive distinct eligible dates only, duplicates cannot add days; gaps break.
            persistence=0
            if significant:
                for d in reversed([*past,current]):
                    expected=current.day-timedelta(days=persistence)
                    v=features(d,inputs.currency,inputs.scope).get((kind,dimension))
                    if d.day!=expected or not d.eligible or v is None or d.mapping_version!=current.mapping_version:break
                    dv=abs(decimal(v)-b)
                    day_impact=dv*d.unit_counts.get(dimension+':'+inputs.currency,0) if kind=='UNIT_COST' else dv
                    if day_impact<floor or b and dv/abs(b)<decimal(policy.watch_relative):break
                    persistence+=1
                    if persistence==3:break
            severity='NONE';state='NORMAL';reasons=[];drivers=[];residual=None
            if significant:
                severity='WATCH';state='OBSERVED'
                if persistence>=2 and impact>=decimal(policy.high_absolute_eur) and (relative is None or relative>=decimal(policy.high_relative)) and score>=decimal(policy.high_score):severity='HIGH';state='ACTIVE'
                # Critical budget impact is not proven in standalone detector; no invented critical.
                explanation=explain(previous or current,current,inputs.currency,delta)
                reasons=explanation['reasons'];drivers=explanation['contributors'];residual=explanation['residual']
                if current.corrections:state='OBSERVED';reasons=['BILLING_CORRECTION'];severity='WATCH'
            if kind=='UNALLOCATED_EXPOSURE' and significant:reasons=['UNALLOCATED_INCREASE']
            confidence='LOW' if any(not d.eligible for d in past if training[0].day<=d.day<current.day) else 'HIGH'
            if confidence=='LOW' and state=='ACTIVE':state='OBSERVED';severity='WATCH'
            results.append(dict(type=kind,dimension=dimension,day=current.day.isoformat(),currency=inputs.currency,
                observed=money(observed),baseline=money(b),deviation=money(delta),absolute_deviation=money(absolute),relative_deviation=format(relative,'f') if relative is not None else None,
                severity=severity,confidence=confidence,state=state,reasons=reasons,impact=format(impact,'f'),impact_type='FIXED_POPULATION_UNIT_EXPOSURE' if kind=='UNIT_COST' else 'ACTUAL_DEVIATION',unit_population=population,persistence=persistence,baseline_days=len(training),label=label,drivers=drivers,residual=residual,robust_score=quotient(absolute,Decimal('1.4826')*mad if mad else noise),baseline_method='SAME_WEEKDAY' if training is season else 'MEDIAN_MAD',model=current.models[dimension].model if dimension in current.models else None,provider=current.models[dimension].provider if dimension in current.models else None,quantity_unit=('calls' if dimension.endswith(':calls') else 'characters' if dimension.endswith(':billable_characters') else 'tokens') if label=='USAGE' else None))
    if len(results)>policy.max_outputs:raise ValueError('Detector limit')
    return dict(state='EVALUATED' if evaluated else 'INSUFFICIENT_DATA',evaluated=evaluated,results=results,algorithm='robust-daily-v1',detector_version=VERSION)

def evaluate(inputs,policy=None):
    with localcontext() as context:
        context.prec=200
        return _evaluate(inputs,policy)
