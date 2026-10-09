"""Deterministic daily reconciliation. Actual charges never come from catalog math."""
from collections import defaultdict
from decimal import Decimal,localcontext,ROUND_HALF_EVEN
from .billing_contracts import decimal,money,instant,digest,SourceError
from .reconciliation_contracts import Policy,Status,Confidence,Reason,ratio,percentage,controls,bounded,ALGORITHM
from .reconciliation_mapping import Registry
from .allocation import classify,totals,validate


def reference(prices,mapping,quantity,at,currency):
    if quantity is None:return None
    from datetime import timedelta
    period_end=at+timedelta(days=1)
    matches=[p for p in prices if p['sku']==mapping.sku and p['service']==mapping.service and
        p['unit']==mapping.unit and p['currency']==currency and p['kind']=='CATALOG_LIST_PRICE' and
        p['consumption_model']=='DEFAULT' and instant(p['observed_from'])<period_end and at<instant(p['observed_to'])]
    if len(matches)!=1:return None
    p=matches[0]
    if instant(p['observed_from'])>at or instant(p['observed_to'])<period_end:return None
    # Multi-tier/account-month pricing needs a complete account population, absent
    # here. Never reconstruct it from a scoped day. Flat proven tariffs only.
    if len(p['tiers'])!=1:return None
    t=p['tiers'][0]
    if decimal(t['lower'])!=0 or t['upper'] is not None:return None
    with localcontext() as ctx:
        ctx.prec=100
        return money((decimal(quantity)*decimal(t['account_price'])/decimal(t['unit_quantity'])).quantize(Decimal('1e-38'),rounding=ROUND_HALF_EVEN))


def reconcile(runtime,billing,pricing,registry,quality=None,policy=None):
    policy=policy or Policy();registry=Registry.model_validate(registry.model_dump())
    quality=quality or dict(billing='FRESH',pricing='UNAVAILABLE')
    if runtime['environment']!=billing['environment'] or registry.environment!=billing['environment'] or runtime['period']!=billing['period']:
        raise SourceError('SCOPE_UNPROVEN')
    if not billing.get('detail_available'):raise SourceError('INPUT_DETAIL_UNAVAILABLE')
    source=billing['components']
    if controls(source)!=billing['controls']:raise SourceError('FINANCIAL_IMBALANCE')
    if max(len(source),len(runtime['buckets']),len(billing['buckets']))>policy.max_buckets:raise SourceError('BATCH_LIMIT')
    period=billing['period'];start=instant(period+'T00:00:00+00:00')
    from datetime import timedelta
    end=start+timedelta(days=1)
    rt={(b['provider'],b['model'],b['region'],b['operation_type']):b for b in runtime['buckets']}
    decisions={};groups={};reasons=set();mapped_count=0;mapped_absolute=Decimal(0)
    used=set();quantities=[];references={};quantity_review=False;partial=False
    component_absolute=defaultdict(lambda:Decimal(0))
    with localcontext() as ctx:
        ctx.prec=100
        for c in source:component_absolute[c['bucket']]+=decimal(c['absolute'])
        for b in billing['buckets']:
            mapping=registry.lookup(b,instant(b['usage_start']),instant(b['usage_end']))
            choice=('UNALLOCATED','NONE','UNMAPPED')
            if mapping is None:reasons.add(Reason.SKU_UNMAPPED.value)
            else:
                mapped_count+=b['rows'];mapped_absolute+=component_absolute[b['id']]
                scope_proven=registry.runtime_scope==billing['scope']
                if mapping.evidence!='DEDICATED_WORKLOAD' or scope_proven:
                    choice=(mapping.classification,mapping.evidence,mapping.category)
                else:reasons.add(Reason.SCOPE_UNPROVEN.value)
                if b['cost_type'] in ('tax','rounding_error'):choice=('UNALLOCATED','NONE',mapping.category)
                if mapping.classification=='SHARED':reasons.add(Reason.SHARED_INFRASTRUCTURE.value)
                if mapping.field and b['cost_type'] not in ('tax','rounding_error'):
                    key=(mapping.provider,mapping.model,mapping.region,mapping.operation_type,mapping.field,b['usage_unit'],b['currency'])
                    g=groups.setdefault(key,dict(mapping=mapping,buckets=[],scope_proven=scope_proven))
                    g['buckets'].append(b)
            decisions[b['id']]=choice
        populations=defaultdict(int)
        for key in groups:populations[key[:5]]+=1
        for key,g in sorted(groups.items()):
            mapping=g['mapping'];bs=g['buckets'];rkey=key[:4];field=key[4];unit=key[5];cur=key[6]
            ambiguous=populations[key[:5]]>1
            compatible=unit==mapping.unit and unit!='UNKNOWN'
            temporal=all(start<=instant(b['usage_start'])<end and instant(b['usage_end'])<=end for b in bs) and mapping.effective_from<=start and end<=mapping.effective_to
            r=rt.get(rkey);value=r['quantities'][field] if r else dict(observed=None,unknown=0,known=0)
            runtime_quantity=value['observed'] if g['scope_proven'] and compatible and temporal and not ambiguous else None
            billing_quantity=sum((decimal(b['quantity']) for b in bs if b['quantity'] is not None),Decimal(0)) if any(b['quantity'] is not None for b in bs) else None
            unknown=sum(b['unknown'] for b in bs)
            reason=None;confidence=Confidence.AGGREGATED.value
            if ambiguous:reason=Reason.AMBIGUOUS_MATCH.value;confidence=Confidence.AMBIGUOUS.value
            elif not compatible:reason=Reason.UNIT_MISMATCH.value;confidence=Confidence.PARTIAL.value
            elif not temporal:reason=Reason.TEMPORAL_GRAIN_MISMATCH.value;confidence=Confidence.PARTIAL.value
            elif not g['scope_proven']:reason=Reason.SCOPE_UNPROVEN.value;confidence=Confidence.UNMATCHED.value
            elif runtime_quantity is None or value['unknown']:reason=Reason.RUNTIME_USAGE_UNKNOWN.value;confidence=Confidence.PARTIAL.value
            elif billing_quantity is None or unknown:reason=Reason.BILLING_QUANTITY_UNKNOWN.value;confidence=Confidence.PARTIAL.value
            difference=money(billing_quantity-decimal(runtime_quantity)) if billing_quantity is not None and runtime_quantity is not None else None
            if reason is None and difference is not None and decimal(difference)!=0:reason=Reason.QUANTITY_VARIANCE.value
            if reason:
                reasons.add(reason)
                quantity_review|=reason in ('UNIT_MISMATCH','AMBIGUOUS_MATCH','QUANTITY_VARIANCE','TEMPORAL_GRAIN_MISMATCH')
                partial=True
            if runtime_quantity is not None:used.add((rkey,field))
            quantities.append(dict(model=mapping.model,component=field,unit=mapping.unit,
                currency=cur,runtime_quantity=runtime_quantity,runtime_unknown=value['unknown'] if r else 1,
                billing_quantity=money(billing_quantity) if billing_quantity is not None else None,billing_unknown=unknown,
                difference=difference,variance_pct=percentage(difference,runtime_quantity) if difference is not None and runtime_quantity is not None else None,
                confidence=confidence,reason=reason))
            amount=reference(pricing.get('prices',()),mapping,runtime_quantity,start,cur) if quality.get('pricing')=='FRESH' and not reason and mapping.consumption_model==digest('DEFAULT') and all(b['consumption_model']==digest('DEFAULT') for b in bs) else None
            if amount is None:reasons.add(Reason.PRICING_UNAVAILABLE.value)
            references[key]=amount
        # Runtime without billing remains visible with no invented price or money.
        for rkey,r in sorted(rt.items()):
            fields={m.field for m in registry.mappings if m.field and (m.provider,m.model,m.region,m.operation_type)==rkey and m.effective_from<=start< m.effective_to}
            if not fields:fields={'input_tokens'} if r['operation_type']=='EMBEDDING' else {'input_tokens','output_tokens'}
            for field in sorted(fields):
                if (rkey,field) in used:continue
                quantities.append(dict(model=r['model'],component=field,unit='characters' if field=='billable_characters' else 'tokens',currency=None,
                    runtime_quantity=r['quantities'][field]['observed'],runtime_unknown=r['quantities'][field]['unknown'],
                    billing_quantity=None,billing_unknown=0,difference=None,variance_pct=None,
                    confidence=Confidence.UNMATCHED.value,reason=Reason.LATE_BILLING.value))
                partial=True;reasons.add(Reason.LATE_BILLING.value)
        classified=classify(source,decisions);validate(source,classified)
        allocation=totals(classified);source_controls=controls(source)
        currencies=sorted({c['currency'] for c in source});amounts=[]
        for cur in currencies:
            cs=[c for c in source if c['currency']==cur];alloc={a['classification']:a for a in allocation if a['currency']==cur}
            actual=sum((decimal(c['amount']) for c in cs),Decimal(0));absolute=sum((decimal(c['absolute']) for c in cs),Decimal(0))
            kind={k:sum((decimal(c['amount']) for c in cs if c['kind']==k),Decimal(0)) for k in ('gross','credit','adjustment','tax','rounding')}
            refvalues=[v for k,v in references.items() if k[-1]==cur]
            compared_buckets={b['id'] for k,g in groups.items() if k[-1]==cur and references.get(k) is not None for b in g['buckets']}
            catalog=sum((decimal(v) for v in refvalues),Decimal(0)) if refvalues and all(v is not None for v in refvalues) and all(c['bucket'] in compared_buckets for c in cs if c['kind']=='gross') else None
            # Only regular gross is comparable to the catalog; tax/credits/adjustment
            # are not falsely attributed to a runtime tariff.
            if catalog is not None and kind['gross']!=catalog:
                quantity_review=True;reasons.add(Reason.MONETARY_VARIANCE.value)
            amounts.append(dict(currency=cur,**{k:money(v) for k,v in kind.items()},net=money(actual),absolute=money(absolute),
                **{cl.lower():alloc.get(cl,{}).get('net','0') for cl in ('DIRECT','SHARED','UNALLOCATED')},excluded='0',
                ratios={cl.lower():ratio(alloc.get(cl,{}).get('absolute','0'),absolute) for cl in ('DIRECT','SHARED','UNALLOCATED')},
                percentages={cl.lower():percentage(alloc.get(cl,{}).get('absolute','0'),absolute) for cl in ('DIRECT','SHARED','UNALLOCATED')},
                catalog_reference=money(catalog) if catalog is not None else None,reference_rounding='ROUND_HALF_EVEN_38',
                monetary_variance=money(kind['gross']-catalog) if catalog is not None else None,
                monetary_variance_pct=percentage(kind['gross']-catalog,catalog) if catalog is not None else None,
                eur_net=money(actual) if cur=='EUR' else None,eur_state='NATIVE_EUR' if cur=='EUR' else 'EUR_UNAVAILABLE',
                count=sum(c['count'] for c in cs),controls='PASS'))
        if len(amounts)>100 or len(allocation)>100 or len({b['invoice_month'] for b in billing['buckets']})>100:raise SourceError('BATCH_LIMIT')
        rq=runtime['quality']
        debt=bool(rq.get('freshness','FRESH')!='FRESH' or rq['debt'] or rq.get('global_debt') or rq.get('pending') or not rq.get('coverage_complete'))
        if debt:partial=True;reasons.add(Reason.ACCOUNTING_DEBT.value)
        if quality.get('billing')!='FRESH':partial=True;reasons.add(Reason.SOURCE_STALE.value)
        if any(c['classification']=='UNALLOCATED' for c in classified):partial=True
        if not source:partial=True;reasons.add(Reason.NO_DATA.value)
        status=Status.VARIANCE_REVIEW.value if quantity_review else Status.PARTIALLY_RECONCILED.value if partial else Status.RECONCILED.value
        if not rq.get('available') or quality.get('billing') in ('UNAVAILABLE','NOT_CONFIGURED'):
            status=Status.UNRECONCILED.value;reasons.add(Reason.SOURCE_UNAVAILABLE.value)
        abs_all=sum((decimal(c['absolute']) for c in source),Decimal(0));source_rows=sum(b['rows'] for b in billing['buckets'])
        runtime_known=sum(q['runtime_quantity'] is not None and q['runtime_unknown']==0 for q in quantities)
        billing_known=sum(q['billing_quantity'] is not None and q['billing_unknown']==0 for q in quantities)
        compatible=sum(q['confidence']=='AGGREGATED' for q in quantities)
        priced=sum(v is not None for v in references.values())
        result=dict(period=period,invoice_months=sorted({b['invoice_month'] for b in billing['buckets']}),status=status,quantities=quantities,amounts=amounts,allocation=allocation,
            source_controls=source_controls,classified_controls=controls(classified),classified=classified,
            reasons=sorted(reasons),quality=dict(runtime=rq,billing=quality.get('billing'),pricing=quality.get('pricing')),
            coverage=dict(runtime_representation=ratio(sum(b['events'] for b in runtime['buckets']),rq['eligible']),ledger_completeness=rq['completeness'],
                quantity_groups=len(quantities),runtime_known_groups=runtime_known,billing_known_groups=billing_known,compatible_groups=compatible,
                price_eligible_groups=len(references),priced_groups=priced,
                runtime_quantity_known=ratio(runtime_known,len(quantities)),billing_quantity_known=ratio(billing_known,len(quantities)),
                compatible_quantity=ratio(compatible,len(quantities)),price_reference=ratio(priced,len(references)),
                eligible_attempts=rq['eligible'],represented_attempts=sum(b['events'] for b in runtime['buckets']),
                mapped_rows=mapped_count,source_rows=source_rows,source_components=sum(c['count'] for c in source),classified_components=sum(c['count'] for c in classified),
                mapping_rows=ratio(mapped_count,source_rows),mapping_amount=ratio(mapped_absolute,abs_all),
                billing_classification=ratio(sum(c['count'] for c in classified),sum(c['count'] for c in source)),
                monetary_accounting=ratio(abs_all,abs_all),
                allocation=ratio(sum((decimal(c['absolute']) for c in classified if c['classification']!='UNALLOCATED'),Decimal(0)),abs_all)),
            versions=dict(mapping=registry.version,ownership=registry.ownership_version,registry=registry.identity,
                policy=policy.version,algorithm=ALGORITHM,schema_version=1),controls=dict(signed_net='PASS',components='PASS',absolute='PASS',count='PASS'))
        return bounded(result,policy)
