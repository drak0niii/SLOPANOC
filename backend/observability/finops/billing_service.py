"""Cached financial reads. Never creates a BigQuery job on an HTTP request."""
import asyncio
from datetime import datetime,date,timedelta,timezone
from decimal import Decimal,localcontext
from .billing_contracts import SOURCES,Policy,decimal,money,digest,SourceError
from .billing_api_models import SourceHealth,HealthPage,BillingSummary,PricingStatus,Amounts
from .billing_normalization import FIELDS
from ..authorization import denied

def utcnow():return datetime.now(timezone.utc)

class FinancialSources:
    def __init__(self,repository=None,adapters=(),policy=None,worker=None):
        self.repository=repository;self.adapters={a.source:a for a in adapters};self.policy=policy or Policy();self.worker=worker

    async def rows(self,environment,source,include_cache=False,start=None,end=None):
        adapter=self.adapters.get(source)
        if adapter is None or self.repository is None:return []
        return await self.repository.current(environment,source,adapter.scope,include_cache,start,end)

    def health(self,source,rows,now):
        adapter=self.adapters.get(source)
        policy_view=dict(delayed_seconds=self.policy.pricing_delayed_seconds if source=='PRICING_EXPORT' else self.policy.billing_delayed_seconds,
            stale_seconds=self.policy.pricing_stale_seconds if source=='PRICING_EXPORT' else self.policy.billing_stale_seconds,
            cadence_seconds=self.policy.pricing_cadence_seconds if source=='PRICING_EXPORT' else self.policy.billing_cadence_seconds)
        if adapter is None:return SourceHealth(source=source,state='NOT_CONFIGURED',reason='NOT_CONFIGURED',mode='NOT_CONFIGURED',**policy_view)
        manifests=[p for _,p,_ in rows if p is not None]
        nonempty=[p for p in manifests if p.row_count>0 and p.export_time is not None]
        exports=[p.export_time for p in nonempty];last_export=max(exports,default=None)
        as_of=max((p.price_from for p in nonempty if p.price_from),default=None) if source=='PRICING_EXPORT' else last_export
        delayed=self.policy.pricing_delayed_seconds if source=='PRICING_EXPORT' else self.policy.billing_delayed_seconds
        stale=self.policy.pricing_stale_seconds if source=='PRICING_EXPORT' else self.policy.billing_stale_seconds
        reason='SOURCE_CURRENT';state='FRESH'
        if not nonempty:state='UNAVAILABLE';reason='NO_DATA'
        elif (now-as_of).total_seconds()>stale:state='STALE';reason='SOURCE_AGE'
        elif (now-as_of).total_seconds()>delayed:state='DELAYED';reason='SOURCE_AGE'
        if any(s.error for s,_,_ in rows):
            state='STALE' if manifests else 'UNAVAILABLE'
            reason='SCHEMA_INCOMPATIBLE' if any(s.error=='SCHEMA_INCOMPATIBLE' for s,_,_ in rows) else 'SOURCE_UNAVAILABLE'
        ordered=sorted(manifests,key=lambda p:p.window_start)
        gaps=any(a.window_end!=b.window_start for a,b in zip(ordered,ordered[1:]))
        if gaps and state=='FRESH':state='DELAYED';reason='COVERAGE_PARTIAL'
        if self.worker and as_of:
            self.worker.metrics.gauge('source_age',source,max(0,(now-as_of).total_seconds()))
            population=sum(p.row_count for p in manifests)
            if population:self.worker.metrics.gauge('unmapped_ratio',source,sum(p.unmapped_rows for p in manifests)/population)
        if self.worker and state in ('STALE','DELAYED'):self.worker.metrics.add('stale_reads',source)
        return SourceHealth(source=source,state=state,reason=reason,mode=adapter.mode,as_of=as_of,last_export=last_export,
            last_extraction=max((p.extracted_at for p in manifests),default=None),
            coverage_start=min((p.window_start for p in manifests),default=None),coverage_end=max((p.window_end for p in manifests),default=None),
            generation=digest(sorted(p.generation for p in manifests)) if manifests else None,
            row_count=sum(p.row_count for p in manifests),unmapped_rows=sum(p.unmapped_rows for p in manifests),
            price_evidence_rows=sum(p.evidence_rows for p in manifests),
            pending_partitions=max(0,self.policy.retained_days-len(manifests)),**policy_view)


def context(service,principal,environment):
    env=environment or service.config.otel_environment
    if env not in ('local','development','staging','production'):raise denied('validation_error','Invalid financial environment.')
    service.environment_scope(principal,env);service.admit(principal)
    return getattr(service,'financial_sources',None) or FinancialSources(),env

async def health(service,principal,environment=None):
    sources,env=context(service,principal,environment)
    try:
        async with asyncio.timeout(2),service.reads:
            items=[]
            for source in SOURCES:items.append(sources.health(source,await sources.rows(env,source),utcnow()))
            return HealthPage(environment=env,sources=tuple(items))
    except Exception:raise denied('connector_unavailable','Financial sources are unavailable.') from None

async def pricing_status(service,principal,environment=None):
    sources,env=context(service,principal,environment)
    try:
        async with asyncio.timeout(2),service.reads:
            rows=await sources.rows(env,'PRICING_EXPORT');h=sources.health('PRICING_EXPORT',rows,utcnow())
            return PricingStatus(environment=env,source=h,
                earliest_pricing_as_of=min((p.price_from for _,p,_ in rows if p and p.price_from),default=None),
                latest_pricing_as_of=max((p.price_from for _,p,_ in rows if p and p.price_from),default=None))
    except Exception:raise denied('connector_unavailable','Pricing source is unavailable.') from None

async def summary(service,principal,environment=None,period_basis='usage',start=None,end=None,invoice_month=None,limit=50,cursor=None):
    sources,env=context(service,principal,environment)
    today=utcnow().date();end=end or today+timedelta(days=1);start=start or end-timedelta(days=1)
    if period_basis not in ('usage','invoice') or not isinstance(start,date) or not isinstance(end,date) or not timedelta(0)<end-start<=timedelta(days=31) or end>today+timedelta(days=1) or not 1<=limit<=100:
        raise denied('validation_error','Invalid financial period.')
    if period_basis=='invoice':
        import re
        if not isinstance(invoice_month,str) or not re.fullmatch(r'\d{4}(0[1-9]|1[0-2])',invoice_month):raise denied('validation_error','Invalid invoice period.')
        try:
            year,month=int(invoice_month[:4]),int(invoice_month[4:])
            start=date(year,month,1);end=date(year+1,1,1) if month==12 else date(year,month+1,1)
        except ValueError:raise denied('validation_error','Invalid invoice period.') from None
    elif invoice_month is not None:raise denied('validation_error','Invoice month requires invoice basis.')
    try:
        async with asyncio.timeout(2),service.reads:
            rows=await sources.rows(env,'DETAILED_BILLING',include_cache=True,start=start if period_basis=='usage' else None,end=end if period_basis=='usage' else None)
            h=sources.health('DETAILED_BILLING',rows,utcnow())
            selected=[(s,p,c) for s,p,c in rows if p and (period_basis=='invoice' or start<=p.window_start.date()<end)]
            complete=len({p.window_start.date() for _,p,_ in selected})==(end-start).days if period_basis=='usage' else False
            if not complete and h.state=='FRESH':h=h.model_copy(update={'state':'DELAYED','reason':'COVERAGE_PARTIAL'})
            result={}
            with localcontext() as ctx:
                ctx.prec=100
                for _,p,c in selected:
                    for g in c.summaries:
                        if period_basis=='invoice' and g['invoice_month']!=invoice_month:continue
                        key=(g['currency'],g['category'])
                        group=result.setdefault(key,{**{f:Decimal(0) for f in FIELDS},'rows':0,'unmapped_rows':0})
                        for f in FIELDS:group[f]+=decimal(g[f])
                        group['rows']+=g['rows'];group['unmapped_rows']+=g['unmapped_rows']
                scope='|'.join((env,period_basis,start.isoformat(),end.isoformat(),invoice_month or '',h.generation or 'none'))
                page=service.cursor(cursor,scope);offset=page.get('offset',0) if page else 0
                if type(offset) is not int or offset<0 or offset+limit>1000:raise SourceError('INVALID_WINDOW')
                values=[]
                for (cur,category),g in sorted(result.items())[offset:offset+limit]:
                    coverage=None if not g['absolute_net'] else money((Decimal(1)-g['unmapped_absolute_net']/g['absolute_net']).quantize(Decimal('0.000000000001')))
                    values.append(Amounts(currency=cur,category=category,**{f:money(g[f]) for f in FIELDS[:7]},
                        rows=g['rows'],unmapped_rows=g['unmapped_rows'],mapping_coverage=coverage,
                        eur_net=money(g['net']) if cur=='EUR' else None,eur_state='NATIVE_EUR' if cur=='EUR' else 'EUR_REPORTING_UNAVAILABLE'))
            more=offset+limit<len(result)
            return BillingSummary(environment=env,period_basis=period_basis,start=start.isoformat(),end=end.isoformat(),invoice_month=invoice_month,
                source=h,coverage_complete=complete,amounts=tuple(values) if values or selected and complete else None,
                next_cursor=service.encode(scope=scope,offset=offset+limit) if more else None)
    except Exception as exc:
        from backend.gateway.safe_error import SafeErrorException
        if isinstance(exc,SafeErrorException):raise
        raise denied('connector_unavailable','Billing summary is unavailable.') from None
