"""Bounded as-of adapter over immutable M12/M13 links; never today's corrected heads."""
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from sqlalchemy import select,func,cast,Text
from .control_contracts import Inputs,Day,Policy,month_bounds,total,ModelDimension
from .billing_contracts import decimal,money,digest,SourceError
from .reconciliation_models import GenerationRow,InputRow,RegistryRow,StateRow
from .billing_models import PublicationRow,StateRow as BillingState
from .unit_economics_models import GenerationRow as UnitGeneration,DriverRow,PublicationRow as UnitState

class Adapter:
    def __init__(self,sessions):self.sessions=sessions
    async def load(self,environment,binding,month,currency,issue,scope='TOTAL',policy=None):
        from .billing_contracts import instant
        issue=instant(issue)
        policy=policy or Policy();start,end=month_bounds(month);first=end-timedelta(days=policy.max_days)
        async with self.sessions() as s:
            population=select(GenerationRow.identity).join(InputRow,GenerationRow.billing_input==InputRow.identity).join(RegistryRow,GenerationRow.registry==RegistryRow.identity).where(
                InputRow.environment==environment,InputRow.scope==binding,InputRow.period>=first.isoformat(),InputRow.period<end.isoformat(),GenerationRow.published_at<=issue)
            count,size=(await s.execute(population.with_only_columns(func.count(),func.coalesce(func.sum(
                func.length(cast(GenerationRow.body,Text))+func.length(cast(InputRow.body,Text))+func.length(cast(RegistryRow.body,Text))),0)))).one()
            # Reject oversized source history BEFORE materializing JSON; allowance for decoded-object overhead.
            if count>policy.max_features or size>policy.max_bytes//8:raise SourceError('BATCH_LIMIT')
            rows=(await s.execute(select(GenerationRow,InputRow,RegistryRow).join(InputRow,GenerationRow.billing_input==InputRow.identity)
                .join(RegistryRow,GenerationRow.registry==RegistryRow.identity).where(InputRow.environment==environment,InputRow.scope==binding,
                    InputRow.period>=first.isoformat(),InputRow.period<end.isoformat(),GenerationRow.published_at<=issue)
                .order_by(InputRow.period,GenerationRow.published_at,GenerationRow.identity).limit(policy.max_features+1))).all()
            if len(rows)>policy.max_features:raise SourceError('BATCH_LIMIT')
            ids={gid for g,b,r in rows for gid in g.body['source_vector'].values()}
            manifests={p.generation:p for p in (await s.scalars(select(PublicationRow).where(PublicationRow.generation.in_(ids)))).all()} if ids else {}
            chosen={}
            for g,b,r in rows:
                vector=g.body['source_vector']
                if any(gid not in manifests or manifests[gid].published_at>issue or manifests[gid].extracted_at>issue for gid in vector.values()):continue
                chosen[b.period]=(g,b,r)
            financial_ids={g.identity for g,b,r in chosen.values()}
            units=(await s.execute(select(UnitGeneration,DriverRow).join(DriverRow,UnitGeneration.drivers==DriverRow.identity)
                .where(UnitGeneration.reconciliation.in_(financial_ids),UnitGeneration.published_at<=issue)
                .order_by(UnitGeneration.published_at,UnitGeneration.identity).limit(policy.max_features+1))).all() if financial_ids else []
            if len(units)>policy.max_features:raise SourceError('BATCH_LIMIT')
            unit_by={u.reconciliation:(u,d) for u,d in units}
            runtime_ids={g.runtime_input for g,b,r in chosen.values()}
            runtime_rows={r.identity:r.body for r in (await s.scalars(select(InputRow).where(InputRow.identity.in_(runtime_ids)))).all()} if runtime_ids else {}
            source_vector={};fences={};unit_fences={};days=[]
            for i in range((end-first).days):
                day=first+timedelta(days=i);period=day.isoformat();close=datetime.combine(day+timedelta(days=1),datetime.min.time(),timezone.utc)
                status='OPEN' if day>=issue.date() else 'TOO_RECENT' if close>issue-timedelta(hours=policy.maturity_hours) else 'MISSING'
                values={};ua={};uar={};uv={};counts={};usage={};factors={};model_dimensions={};mix={};mix_proven=False;coverage=None;corrections=0;mapping=None;gid=None;uid=None;published=None
                if period in chosen:
                    g,b,r=chosen[period];gid=g.identity;published=g.published_at;result=g.body['result'];source_vector.update(g.body['source_vector'])
                    fences[digest([environment,binding,period])]=gid
                    mapping=digest(result['versions'])[:32]
                    coverage=result['coverage'].get('allocation')
                    if status not in ('OPEN','TOO_RECENT'):
                        status='STALE' if result['quality'].get('billing')!='FRESH' else 'ELIGIBLE_COMPLETE' if result['status'] in ('RECONCILED','CORRECTED') and all(result['controls'].get(k)=='PASS' for k in ('signed_net','components','absolute','count')) else 'PARTIAL'
                    corrections=manifests[b.source_generation].correction_rows if b.source_generation in manifests else 0
                    for a in result['amounts']:
                        cur=a['currency'];values['TOTAL:'+cur]=a['net'];ua[cur]=total(c['absolute'] for c in result['classified'] if c['currency']==cur and c['classification']=='UNALLOCATED');uar[cur]=a['ratios'].get('unallocated') or '0'
                    groups={};buckets={x['id']:x for x in b.body['buckets']}
                    from .reconciliation_mapping import Registry
                    registry=Registry.model_validate(r.body)
                    from .billing_contracts import instant
                    for c in result['classified']:
                        cur=c['currency'];keys=['CLASS_'+c['classification']+':'+cur,'CATEGORY_'+c['category']+':'+cur]
                        bucket=buckets.get(c['bucket']);m=registry.lookup(bucket,instant(bucket['usage_start']),instant(bucket['usage_end'])) if bucket else None
                        if m and m.classification=='DIRECT' and registry.runtime_scope==binding:
                            model_scope='MODEL_'+digest([m.provider,m.model])[:24]
                            keys.append(model_scope+':'+cur)
                            model_dimensions[model_scope]=ModelDimension(provider=m.provider,model=m.model)
                        if policy.recorded_at<=issue and policy.observability_population_complete and policy.observability_binding==binding and bucket and digest([bucket[x] for x in ('service','sku','project','region')]) in policy.observability_ownership:
                            keys.append('OBSERVABILITY:'+cur)
                        for k in keys:groups.setdefault(k,[]).append(c['amount'])
                    from .control_contracts import quotient
                    factor_groups={}
                    for bucket in buckets.values():
                        m=registry.lookup(bucket,instant(bucket['usage_start']),instant(bucket['usage_end']))
                        if not m or not m.field or m.classification!='DIRECT' or registry.runtime_scope!=binding or bucket.get('unknown') or bucket.get('quantity') is None:continue
                        q=[q for q in result['quantities'] if q['model']==m.model and q['component']==m.field and q['currency']==bucket['currency'] and q['reason'] is None and q['confidence']=='AGGREGATED']
                        if len(q)!=1:continue
                        fk=digest([m.service,m.sku,m.project,m.region,m.model,m.field,m.unit])+':'+bucket['currency']
                        pair=factor_groups.setdefault(fk,[[],[]]);pair[0].append(bucket['quantity'])
                        pair[1].extend(c['amount'] for c in result['classified'] if c['bucket']==bucket['id'] and c['kind']=='gross')
                    for k,(quantities,amounts) in factor_groups.items():
                        quantity=total(quantities);amount=total(amounts)
                        if decimal(quantity)>0:factors[k]=(quantity,quotient(amount,quantity))
                    # Workload/class monetary scopes require frozen M13 ownership; no proration.
                    pair=unit_by.get(gid)
                    if pair:
                        u,driver=pair;uid=u.identity
                        unit_fences[digest([environment,binding,period,'ECONOMICS'])]=uid
                        from .unit_drivers import Registry as Drivers,matches
                        dr=Drivers.model_validate(driver.body)
                        if dr.environment==environment and dr.financial_scope==binding:
                            for c in result['classified']:
                                bucket=buckets.get(c['bucket'])
                                if not bucket:continue
                                rules=[rule for rule in dr.scope_rules if matches(rule,bucket,instant(bucket['usage_start']),instant(bucket['usage_end']))]
                                if len(rules)==1:
                                    rule=rules[0]
                                    groups.setdefault('WORKLOAD_'+rule.financial_scope+':'+c['currency'],[]).append(c['amount'])
                                    if rule.request_class:groups.setdefault('REQUEST_'+rule.request_class+':'+c['currency'],[]).append(c['amount'])
                        class_views={v['unit_id']:v for v in u.body['result']['results'] if v['currency']==currency and v['dimension']=='ALL'}
                        class_ids=('GENERAL_TURN','TEAMS_LOOKUP_TURN','GOVERNED_TROUBLESHOOTING_TURN','COMPLEX_MULTI_AGENT_TURN')
                        accepted=class_views.get('ACCEPTED_TURN')
                        class_valid=lambda v: bool(v and v.get('unit_count') is not None and v['financial_freshness']=='FRESH' and
                            v['coverage'].get('numerator_scope') is not None and decimal(v['coverage']['numerator_scope'])==1 and
                            v['coverage'].get('denominator') is not None and decimal(v['coverage']['denominator'])==1 and
                            (v['state']=='AVAILABLE' or v['unit_count']==0 and decimal(v['direct_amount'])==0))
                        if class_valid(accepted) and all(class_valid(class_views.get(k)) for k in class_ids):
                            cs=[class_views[k] for k in class_ids]
                            if sum(v['unit_count'] for v in cs)==accepted['unit_count'] and total(v['direct_amount'] for v in cs)==accepted['direct_amount']:
                                mix={v['unit_id']:(str(v['unit_count']),v['direct_amount']) for v in cs};mix_proven=True
                        for v in u.body['result']['results']:
                            if (v['state']=='AVAILABLE' and v['direct_state']=='AVAILABLE' and v['financial_freshness']=='FRESH'
                                    and v.get('unit_count',0)>=policy.min_unit_population and v.get('direct_cost_per_unit') is not None
                                    and v['coverage'].get('numerator_scope') is not None and decimal(v['coverage']['numerator_scope'])==1 and v['coverage'].get('denominator') is not None and decimal(v['coverage']['denominator'])==1):
                                k=v['unit_id']+':'+v['dimension']+':'+v['currency'];uv[k]=v['direct_cost_per_unit'];counts[k]=v['unit_count']
                    for k,v in groups.items():values[k]=total(v)
                    runtime=runtime_rows.get(g.runtime_input,{})
                    if runtime.get('quality',{}).get('coverage_complete') and not runtime.get('quality',{}).get('debt'):
                        for bucket in runtime.get('buckets',[]):
                            usage[bucket['model']+':calls']=str(bucket['events'])
                    # M12 quantity evidence is frozen and comparable; usage is not money.
                    for q in result['quantities']:
                        if q['runtime_quantity'] is not None and q['runtime_unknown']==0:
                            k=q['model']+':'+q['component'];usage[k]=total([usage.get(k,'0'),q['runtime_quantity']])
                days.append(Day(day=day,generation=gid,unit_generation=uid,published_at=published,state=status,values=values,coverage=coverage,
                    unallocated=ua,unallocated_ratio=uar,units=uv,unit_counts=counts,usage=usage,corrections=corrections,mapping_version=mapping,factors=factors,models=model_dimensions,mix=mix,mix_proven=mix_proven))
        return Inputs(environment=environment,binding=binding,month=month,currency=currency,scope=scope,issue=issue,cutoff=issue,
            days=tuple(days),source_vector=source_vector,fences=fences,unit_fences=unit_fences)

    async def fresh(self,inputs,at,policy=None):
        """Batched live overlay, separate from frozen historical capture quality."""
        policy=policy or Policy()
        async with self.sessions() as s:
            bindings=set()
            for model,vector in ((StateRow,inputs.fences),(BillingState,inputs.source_vector),(UnitState,inputs.unit_fences)):
                if not vector:continue
                rows=(await s.scalars(select(model).where(model.key.in_(vector)))).all()
                if len(rows)!=len(vector) or any(row.generation!=vector[row.key] or row.error for row in rows):return False
                if model is BillingState:bindings.update((row.source,row.scope) for row in rows)
            for source,binding in sorted(bindings):
                latest=await s.scalar(select(BillingState).where(BillingState.environment==inputs.environment,BillingState.source==source,BillingState.scope==binding)
                    .order_by(BillingState.partition.desc()).limit(1))
                if not latest or latest.error or not latest.last_success or (at-latest.last_success).total_seconds()>policy.current_source_stale_seconds:return False
            units={d.unit_generation for d in inputs.days if d.unit_generation}
            if units:
                from .unit_economics_repository import key_for,turn_scope
                unit_rows=(await s.scalars(select(UnitGeneration).where(UnitGeneration.identity.in_(units)))).all()
                for g in unit_rows:
                    # M13 denominator head movement invalidates the current unit presentation.
                    day=next(d.day.isoformat() for d in inputs.days if d.unit_generation==g.identity)
                    for source,sid in g.body.get('snapshot_ids',{}).items():
                        scope=turn_scope(inputs.environment) if source=='M9' else inputs.binding
                        head=await s.get(UnitState,key_for(inputs.environment,scope,day,source))
                        if not head or head.snapshot!=sid or head.error:return False
            latest=await s.scalar(select(StateRow.last_success).where(StateRow.environment==inputs.environment,StateRow.scope==inputs.binding).order_by(StateRow.last_success.desc()).limit(1))
            return bool(latest and (at-latest).total_seconds()<=policy.current_source_stale_seconds)
