"""Grouped Decimal analytical averages; never per-attempt billed lineage."""
from collections import defaultdict
from datetime import timedelta
from decimal import Decimal, localcontext
from .billing_contracts import decimal, money, instant, SourceError
from .reconciliation_contracts import controls, ratio
from .reconciliation_mapping import Registry as FinancialRegistry
from .allocation import validate
from .unit_registry import Registry as UnitRegistry
from .unit_drivers import Registry as DriverRegistry, matches, distribute
from .unit_economics_contracts import Policy, Result, Coverage, Snapshot, State, divide, bounded, ALGORITHM

def compatible(unit, component, bucket, mapping, scope_rule, financial, snapshots, runtime, billing, start, end, dimension):
    """Explicit numerator proof, independent of the existence of a denominator."""
    if component['classification']!='DIRECT' or component['confidence']!='HIGH' or mapping is None:
        return False
    if component['kind'] in ('tax','rounding') or bucket['cost_type'] in ('tax','rounding_error'):
        return False
    if not start<=instant(bucket['usage_start'])<end or instant(bucket['usage_end'])>end:
        return False
    if unit.denominator_source=='M10':
        snap=snapshots.get('M10')
        return bool(financial.runtime_scope==billing['scope'] and mapping.category=='MODEL_AI' and
            mapping.evidence=='DEDICATED_WORKLOAD' and mapping.field and snap and snap.window_aligned and
            snap.runtime_digest==runtime['ledger_digest'] and (dimension=='ALL' or dimension==mapping.model) and
            any((p.provider,p.model,p.region,p.operation_type)==(mapping.provider,mapping.model,mapping.region,mapping.operation_type)
                for p in snap.buckets))
    snap=snapshots.get('M9')
    if not snap or not snap.business_population_proven or not scope_rule or scope_rule.financial_scope!='USER_TURN_WORKLOAD':
        return False
    if unit.operational_scope=='REQUEST_CLASS':
        return scope_rule.request_class==unit.unit_id.removesuffix('_TURN')
    return True

def calculate(financial_result, billing, runtime, financial_registry, snapshots, drivers, definitions=None, policy=None):
    policy=policy or Policy();definitions=definitions or UnitRegistry()
    definitions=UnitRegistry.model_validate(definitions.model_dump())
    drivers=DriverRegistry.model_validate(drivers.model_dump())
    financial=FinancialRegistry.model_validate(financial_registry.model_dump())
    snaps={s.source:Snapshot.model_validate(s.model_dump()) for s in snapshots}
    env=billing['environment'];period=billing['period'];start=instant(period+'T00:00:00+00:00');end=start+timedelta(days=1)
    if env!=financial.environment or env!=drivers.environment or drivers.financial_scope not in (None,billing['scope']) or runtime['period']!=period or runtime['environment']!=env:
        raise SourceError('SCOPE_UNPROVEN')
    if any(s.environment!=env or s.period!=period for s in snaps.values()):
        raise SourceError('SCOPE_UNPROVEN')
    components=financial_result['classified']
    if max(len(components),len(billing['buckets']))>policy.max_buckets:
        raise SourceError('BATCH_LIMIT')
    validate(billing['components'],components)
    if controls(billing['components'])!=financial_result['source_controls']:
        raise SourceError('FINANCIAL_IMBALANCE')
    buckets={b['id']:b for b in billing['buckets']}
    enriched=[]
    for c in components:
        b=buckets[c['bucket']];m=financial.lookup(b,instant(b['usage_start']),instant(b['usage_end']))
        scopes=[r for r in drivers.scope_rules if matches(r,b,start,end)]
        if len(scopes)>1:
            raise SourceError('MAPPING_INVALID')
        enriched.append((c,b,m,scopes[0] if scopes else None))
    currencies=sorted({c['currency'] for c in components}) or ['EUR']
    model_snap=snaps.get('M10');models=sorted({p.model for p in model_snap.buckets}) if model_snap else []
    if len(models)>100:
        raise SourceError('BATCH_LIMIT')
    if sum(1+len(models) if u.denominator_source=='M10' else 1 for u in definitions.units)*len(currencies)>10000:
        raise SourceError('BATCH_LIMIT')
    result=[];distribution_controls=[]
    with localcontext() as ctx:
        ctx.prec=100
        for unit in definitions.units:
            dimensions=['ALL']+(models if unit.denominator_source=='M10' else [])
            for cur in currencies:
                source=[x for x in enriched if x[0]['currency']==cur]
                actual=sum((decimal(c['amount']) for c,_,_,_ in source),Decimal(0))
                unallocated=sum((decimal(c['amount']) for c,_,_,_ in source if c['classification']=='UNALLOCATED'),Decimal(0))
                absolute=sum((decimal(c['absolute']) for c,_,_,_ in source),Decimal(0))
                direct_input=sum((decimal(c['amount']) for c,_,_,_ in source if c['classification']=='DIRECT'),Decimal(0))
                direct_abs=sum((decimal(c['absolute']) for c,_,_,_ in source if c['classification']=='DIRECT'),Decimal(0))
                shared=sum((decimal(c['amount']) for c,_,_,_ in source if c['classification']=='SHARED'),Decimal(0))
                shared_abs=sum((decimal(c['absolute']) for c,_,_,_ in source if c['classification']=='SHARED'),Decimal(0))
                ua_abs=sum((decimal(c['absolute']) for c,_,_,_ in source if c['classification']=='UNALLOCATED'),Decimal(0))
                snap=snaps.get(unit.denominator_source)
                counts={d:None for d in dimensions};direct={d:Decimal(0) for d in dimensions};matched_abs=dict(direct);proof={d:False for d in dimensions}
                for d in dimensions:
                    if snap:
                        counts[d]=snap.counts.get(unit.unit_id) if d=='ALL' else sum(getattr(p,'attempts' if unit.unit_id=='PROVIDER_ATTEMPT' else 'operations') for p in snap.buckets if p.model==d)
                    for c,b,m,r in source:
                        scoped_rule=r if drivers.financial_scope==billing['scope'] else None
                        if compatible(unit,c,b,m,scoped_rule,financial,snaps,runtime,billing,start,end,d):
                            direct[d]+=decimal(c['amount']);matched_abs[d]+=decimal(c['absolute']);proof[d]=True
                shares={d:Decimal(0) for d in dimensions};distributed_abs=Decimal(0)
                for c,b,m,_ in source:
                    if c['classification']!='SHARED':
                        continue
                    eligible=[r for r in drivers.drivers if unit.unit_id in r.units and r.category==c['category'] and
                        matches(r.ownership,b,start,end) and r.effective_from<=start and end<=r.effective_to and
                        start<=instant(b['usage_start'])<end and instant(b['usage_end'])<=end]
                    if len(eligible)>1:
                        raise SourceError('MAPPING_INVALID')
                    weights={}
                    if eligible and snap and snap.complete and snap.freshness=='FRESH' and snap.window_aligned:
                        rule=eligible[0]
                        if rule.driver=='ACCEPTED_TURNS' and snap.business_population_proven and drivers.financial_scope==billing['scope']:
                            # Class-specific views need class-specific pool ownership.
                            valid=unit.operational_scope!='REQUEST_CLASS' or rule.ownership.request_class==unit.unit_id.removesuffix('_TURN')
                            # Each view is a separate analytical allocation, never additive.
                            if valid and counts['ALL']:
                                weights={'ALL':counts['ALL']}
                        elif rule.driver=='PROVIDER_ATTEMPTS' and snap.runtime_digest==runtime['ledger_digest']:
                            weights={d:sum(p.attempts for p in snap.buckets if p.model==d) for d in models if counts.get(d)}
                    allocations,remainder=distribute(c['amount'],weights)
                    distributed=sum(map(decimal,allocations.values()),Decimal(0))
                    if distributed+decimal(remainder)!=decimal(c['amount']):
                        raise SourceError('FINANCIAL_IMBALANCE')
                    shares['ALL']+=distributed
                    for d,value in allocations.items():
                        if d!='ALL':shares[d]+=decimal(value)
                    if remainder=='0' and weights:
                        distributed_abs+=decimal(c['absolute'])
                    distribution_controls.append(dict(unit_id=unit.unit_id,currency=cur,component=c['id'],source=c['amount'],distributed=money(distributed),undistributed=remainder))
                for d in dimensions:
                    count=counts[d];state=State.AVAILABLE;reasons=[]
                    quality=financial_result['quality'];rq=quality['runtime']
                    if unit.availability=='DEFINED_NOT_AVAILABLE':state=State.DEFINED_NOT_AVAILABLE
                    elif not snap or count is None or not snap.complete:state=State.DENOMINATOR_UNAVAILABLE
                    elif unit.operational_scope=='REQUEST_CLASS' and snap.counts.get('UNKNOWN_TURN',0):state=State.DENOMINATOR_UNAVAILABLE
                    elif count==0:state=State.INSUFFICIENT_DATA
                    elif snap.freshness!='FRESH' or quality.get('billing')!='FRESH' or rq.get('freshness')!='FRESH':state=State.SOURCE_STALE
                    elif not proof[d] or not snap.window_aligned:state=State.FINANCIAL_SCOPE_UNAVAILABLE
                    elif financial_result['status'] not in ('RECONCILED','CORRECTED','PARTIALLY_RECONCILED') or not rq.get('coverage_complete') or rq.get('debt') or rq.get('global_debt') or rq.get('pending'):
                        state=State.RECONCILIATION_INCOMPLETE
                    direct_state=state
                    remaining=shared-shares['ALL']
                    fully_state=state
                    if state==State.AVAILABLE and shared_abs>distributed_abs:
                        fully_state=State.PARTIAL;reasons.append(State.DRIVER_UNAVAILABLE)
                    if state==State.AVAILABLE and ua_abs:
                        state=State.PARTIAL
                    if state==State.AVAILABLE and fully_state==State.PARTIAL:state=State.PARTIAL
                    # Money is exposed even if scope cannot prove a price. It never disappears.
                    direct_price=divide(direct[d],count) if direct_state==State.AVAILABLE else None
                    fully_price=divide(direct[d]+shares[d],count) if fully_state in (State.AVAILABLE,State.PARTIAL) else None
                    if direct[d]+(direct_input-direct[d])!=direct_input:
                        raise SourceError('FINANCIAL_IMBALANCE')
                    result.append(Result(unit_id=unit.unit_id,name=unit.name,definition=unit.eligibility,dimension=d,currency=cur,
                        state=state,direct_state=direct_state,fully_loaded_state=fully_state,unit_count=count,
                        direct_amount=money(direct[d]),direct_without_unit_scope=money(direct_input-direct[d]),direct_cost_per_unit=direct_price,
                        distributed_shared=money(shares[d]),undistributed_shared=money(remaining),
                        fully_loaded_amount=money(direct[d]+shares[d]),fully_loaded_cost_per_unit=fully_price,
                        unallocated_amount=money(unallocated),actual_net=money(actual),analytical_view=unit.unit_id,
                        operational_context=snap.operational_contexts.get(unit.unit_id) if snap and snap.complete and snap.window_aligned else None,
                        price_semantics='YIELD_ADJUSTED_AVERAGE' if unit.yield_adjusted else 'AGGREGATE_AVERAGE',outcome_source=unit.denominator_source,
                        financial_freshness='FRESH' if quality.get('billing')=='FRESH' else 'STALE' if quality.get('billing') in ('STALE','DELAYED') else 'UNAVAILABLE',
                        denominator_freshness=snap.freshness if snap else 'UNAVAILABLE',reasons=tuple(reasons),
                        coverage=Coverage(reconciliation=financial_result['coverage']['monetary_accounting'],
                            numerator_scope=ratio(matched_abs[d],direct_abs),denominator='1' if snap and snap.complete else None,
                            shared_driver=ratio(distributed_abs,shared_abs),currency='1',mapping=financial_result['coverage']['mapping_amount'],
                            unallocated_share=ratio(ua_abs,absolute))).model_dump(mode='json'))
    # Entire source and control vectors are retained internally; public reads project strict DTOs.
    if len(result)>10000:
        raise SourceError('BATCH_LIMIT')
    return bounded(dict(results=result,distribution_controls=distribution_controls,
        versions=dict(units=definitions.version,drivers=drivers.version,algorithm=ALGORITHM,policy=policy.version),
        controls='PASS',period=period,environment=env),policy)
