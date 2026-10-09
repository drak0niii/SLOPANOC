"""Protected cache-only reads; calculation and denominator capture never run on GET."""
import asyncio
from datetime import datetime, timezone, date, timedelta
from sqlalchemy import select
from .billing_service import context
from ..authorization import denied
from .billing_contracts import digest
from .unit_registry import UNITS, IDS
from .unit_economics_contracts import Result, State
from .unit_economics_api_models import Page, Day
from .unit_economics_repository import key_for, turn_scope
from .unit_economics_models import PublicationRow
from .reconciliation_models import StateRow as FinancialState

def now():
    return datetime.now(timezone.utc)

def unavailable(unit,currency='EUR'):
    state=State.DEFINED_NOT_AVAILABLE if unit.availability=='DEFINED_NOT_AVAILABLE' else State.DENOMINATOR_UNAVAILABLE
    return Result(unit_id=unit.unit_id,name=unit.name,definition=unit.eligibility,currency=currency,state=state,
        direct_state=state,fully_loaded_state=state,analytical_view=unit.unit_id,outcome_source=unit.denominator_source)

async def read(service,principal,environment=None,start=None,end=None,unit_id=None,currency=None,dimension=None,limit=31,cursor=None):
    sources,env=context(service,principal,environment)
    today=now().date();end=end or today;start=start or end-timedelta(days=1)
    if unit_id is not None and unit_id not in IDS or currency is not None and (len(currency)!=3 or not currency.isascii() or not currency.isupper() or not currency.isalpha()):
        raise denied('validation_error','Invalid unit economics selection.')
    if dimension is not None:
        import re
        if not re.fullmatch(r'ALL|other|(?:gemini-|text-embedding-|embedding-)[A-Za-z0-9_.-]{1,100}',dimension):
            raise denied('validation_error','Invalid unit economics dimension.')
    if not isinstance(start,date) or not isinstance(end,date) or not timedelta(0)<end-start<=timedelta(days=31) or end>today+timedelta(days=1) or not 1<=limit<=31:
        raise denied('validation_error','Invalid unit economics range.')
    repo=getattr(service,'unit_economics_repository',None);adapter=sources.adapters.get('DETAILED_BILLING')
    try:
        async with asyncio.timeout(2),service.reads:
            rows=await repo.current(env,adapter.scope,start.isoformat(),end.isoformat()) if repo and adapter else []
            scope=digest([env,start.isoformat(),end.isoformat(),unit_id,currency,dimension,[(s.key,s.revision,s.generation) for s,g in rows]])
            page=service.cursor(cursor,scope);offset=page.get('offset',0) if page else 0
            if type(offset)is not int or not 0<=offset<31:
                raise denied('validation_error','Invalid unit economics cursor.')
            current={s.period:(s,g) for s,g in rows};items=[]
            accounting=getattr(service,'accounting',None)
            live_health=await accounting.repository.health(env) if accounting else None
            for day in (start+timedelta(days=i) for i in range(offset,min((end-start).days,offset+limit))):
                period=day.isoformat();state,g=current.get(period,(None,None));views=[]
                if g:
                    bh=sources.health('DETAILED_BILLING',await sources.rows(env,'DETAILED_BILLING',False,day,day+timedelta(days=1)),now())
                    financial_stale=bh.state!='FRESH'
                    denominator_stale=False
                    async with repo.sessions() as s:
                        f=(await s.scalars(select(FinancialState).where(FinancialState.environment==env,
                            FinancialState.scope==adapter.scope,FinancialState.period==period))).one_or_none()
                        financial_stale|=not f or f.generation!=g.reconciliation or bool(f.error)
                        from .reconciliation_models import GenerationRow as FinancialGeneration
                        from .billing_models import StateRow as SourceState
                        fg=await s.get(FinancialGeneration,g.reconciliation)
                        if fg:
                            for sk,gid in fg.body['source_vector'].items():
                                sh=await s.get(SourceState,sk)
                                financial_stale|=not sh or sh.generation!=gid or bool(sh.error)
                        else:financial_stale=True
                        for source,sid in g.body['snapshot_ids'].items():
                            head=await s.get(PublicationRow,key_for(env,turn_scope(env) if source=='M9' else adapter.scope,period,source))
                            denominator_stale|=not head or head.snapshot!=sid or bool(head.error)
                    if live_health:
                        financial_stale|=live_health['updated_at'] is None or (now()-live_health['updated_at']).total_seconds()>300
                    for raw in g.body['result']['results']:
                        view=Result.model_validate(raw)
                        if unit_id and view.unit_id!=unit_id or currency and view.currency!=currency or dimension and view.dimension!=dimension:
                            continue
                        # Historical numbers stay auditable, current quality never stays green after source movement.
                        if view.state!=State.DEFINED_NOT_AVAILABLE and (financial_stale or denominator_stale or state.error):
                            view=view.model_copy(update=dict(state=State.SOURCE_STALE,
                                direct_state=State.SOURCE_STALE,fully_loaded_state=State.SOURCE_STALE,
                                financial_freshness='STALE' if financial_stale else view.financial_freshness,
                                denominator_freshness='STALE' if denominator_stale else 'RETAINED'))
                        elif view.state!=State.DEFINED_NOT_AVAILABLE and live_health and (live_health['debt'] or live_health['pending'] or live_health['conflicts']):
                            view=view.model_copy(update=dict(state=State.PARTIAL))
                        views.append(view)
                    if not views and currency:
                        for u in UNITS:
                            if unit_id is None or unit_id==u.unit_id:
                                v=unavailable(u,currency)
                                if v.state!=State.DEFINED_NOT_AVAILABLE:
                                    v=v.model_copy(update=dict(state=State.CURRENCY_UNAVAILABLE,
                                        direct_state=State.CURRENCY_UNAVAILABLE,fully_loaded_state=State.CURRENCY_UNAVAILABLE))
                                views.append(v)
                    items.append(Day(period=period,generation=g.identity,reconciliation_generation=g.reconciliation,
                        denominator_generations=tuple(sorted(g.body['snapshot_ids'].values())),published_at=g.published_at.isoformat(),
                        driver_version=g.body['result']['versions']['drivers'],job_state='FAILED' if state.error else 'AVAILABLE',
                        results=tuple(views[:100]),result_count=len(views),bounded_summary=len(views)>100))
                else:
                    views=[unavailable(u,currency or 'EUR') for u in UNITS if unit_id is None or u.unit_id==unit_id]
                    items.append(Day(period=period,results=tuple(views),result_count=len(views)))
            more=offset+limit<(end-start).days
            return Page(environment=env,items=tuple(items),next_cursor=service.encode(scope=scope,offset=offset+limit) if more else None)
    except Exception as exc:
        from backend.gateway.safe_error import SafeErrorException
        if isinstance(exc,SafeErrorException):raise
        raise denied('connector_unavailable','Unit economics is unavailable.') from None
