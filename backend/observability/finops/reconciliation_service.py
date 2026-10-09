"""Financial read facade. Only published aggregates; fresh source-quality overlay."""
import asyncio
from datetime import date,timedelta,datetime,timezone
from .billing_service import context
from ..authorization import denied
from .reconciliation_api_models import Page,View
from .reconciliation_contracts import Reason
from .billing_contracts import digest

def now():return datetime.now(timezone.utc)

async def read(service,principal,environment=None,start=None,end=None,period=None,limit=31,cursor=None):
    sources,env=context(service,principal,environment)
    today=now().date()
    if period is not None:
        try:
            parsed=date.fromisoformat(period)
            if parsed.isoformat()!=period:raise ValueError()
            start=parsed;end=parsed+timedelta(days=1)
        except ValueError:raise denied('validation_error','Invalid reconciliation period.') from None
    end=end or today+timedelta(days=1);start=start or end-timedelta(days=1)
    if not isinstance(start,date) or not isinstance(end,date) or not timedelta(0)<end-start<=timedelta(days=31) or end>today+timedelta(days=1) or not 1<=limit<=31:
        raise denied('validation_error','Invalid reconciliation range.')
    repo=getattr(service,'reconciliation_repository',None);adapter=sources.adapters.get('DETAILED_BILLING')
    try:
        async with asyncio.timeout(2),service.reads:
            rows=await repo.current(env,adapter.scope,start.isoformat(),end.isoformat()) if repo and adapter else []
            current={s.period:(s,g) for s,g in rows}
            scope='|'.join((env,start.isoformat(),end.isoformat(),digest([(s.period,s.generation,s.revision) for s,g in rows])))
            page=service.cursor(cursor,scope);offset=page.get('offset',0) if page else 0
            if type(offset)is not int or offset<0 or offset>30:raise denied('validation_error','Invalid reconciliation cursor.')
            accounting=getattr(service,'accounting',None)
            live_health=await accounting.repository.health(env) if accounting else None
            runtime_state='SNAPSHOT_ONLY' if live_health is None else 'UNAVAILABLE' if live_health['updated_at'] is None else 'STALE' if (now()-live_health['updated_at']).total_seconds()>300 else 'FRESH'
            items=[]
            for day in (start+timedelta(days=i) for i in range(offset,min((end-start).days,offset+limit))):
                period=day.isoformat();state,generation=current.get(period,(None,None))
                b_rows=await sources.rows(env,'DETAILED_BILLING',False,day,day+timedelta(days=1))
                p_rows=await sources.rows(env,'PRICING_EXPORT',False,day,day+timedelta(days=1))
                bh=sources.health('DETAILED_BILLING',b_rows,now());ph=sources.health('PRICING_EXPORT',p_rows,now())
                base=dict(period=period,billing_freshness=bh.state,pricing_freshness=ph.state,source_mode=bh.mode,
                    job_error=state.error if state else None,runtime_freshness=runtime_state,
                    current_accounting_debt=live_health['debt'] if live_health else None,current_accounting_pending=live_health['pending'] if live_health else None)
                if generation is None:
                    items.append(View(**base,reasons=(state.error if state and state.error else 'SOURCE_UNAVAILABLE',)))
                    continue
                result=generation.body['result']
                # Explicit field projection prevents internal financial keys leaking.
                view={k:result[k] for k in ('invoice_months','status','quantities','amounts','allocation','coverage','controls','versions','reasons')}
                view['quantity_count']=len(view['quantities'])
                view['quantity_detail_state']='BOUNDED_SUMMARY' if len(view['quantities'])>100 else 'COMPLETE'
                view['quantities']=view['quantities'][:100]
                moved=any(not any(s.key==key and p and p.generation==gid for s,p,_ in b_rows+p_rows)
                    for key,gid in generation.body['source_vector'].items())
                display='Reconciled' if result['status']=='RECONCILED' else 'Partial' if result['status']=='PARTIALLY_RECONCILED' else 'Unresolved'
                if bh.state in ('UNAVAILABLE','NOT_CONFIGURED'):display='Source unavailable'
                elif bh.state in ('STALE','DELAYED') or moved or state.error or runtime_state in ('STALE','UNAVAILABLE'):display='Stale source'
                elif live_health and (live_health['debt'] or live_health['pending'] or live_health['conflicts']):display='Partial'
                items.append(View(**base,**view,generation=generation.identity,display_state=display,
                    source_generations=tuple(sorted(generation.body['source_vector'].values())),
                    runtime_quality=result['quality']['runtime'],published_at=generation.published_at.isoformat()))
            more=offset+limit<(end-start).days
            return Page(environment=env,items=tuple(items),next_cursor=service.encode(scope=scope,offset=offset+limit) if more else None)
    except Exception as exc:
        from backend.gateway.safe_error import SafeErrorException
        if isinstance(exc,SafeErrorException):raise
        raise denied('connector_unavailable','Reconciliation is unavailable.') from None
