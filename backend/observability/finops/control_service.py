"""Protected bounded cache reads and live freshness overlay; GET never evaluates."""
import asyncio,re
from datetime import datetime,timezone
from sqlalchemy import select
from .billing_service import context
from .control_api_models import Item,Page
from .control_models import InputRow
from .control_contracts import Inputs,Policy
from .billing_contracts import digest
from ..authorization import denied

def now():return datetime.now(timezone.utc)

def project(g,raw,freshness,scope='TOTAL'):
    allowed=set(Item.model_fields)-{'generation','kind','issue','month','freshness','backtest_quality','anomaly_id'}
    body={k:v for k,v in raw.items() if k in allowed}
    body.update(scope=scope,generation=g.identity,kind=g.kind,issue=g.issue.isoformat(),month=g.month,freshness=freshness,currency=g.body['currency'])
    if raw.get('backtest_quality'):body['backtest_quality']={k:raw['backtest_quality'].get(k) for k in ('mae','wape','bias')}
    if g.kind=='ANOMALY':
        body['anomaly_id']=digest([g.environment,g.scope,g.body['currency'],raw['type'],raw['dimension'],raw['day'],'robust-daily-v1'])
        body['algorithm']='robust-daily-v1'
        from .unit_registry import IDS
        unit=raw['dimension'].split(':')[0]
        if raw['type']=='UNIT_COST' and unit in IDS:body['unit_id']=unit
    if freshness!='FRESH':body.update(state='SOURCE_STALE',risk='SOURCE_STALE')
    return Item.model_validate(body)

async def read(service,principal,kind,environment=None,month=None,limit=50,cursor=None,detail=None,history=False,scope=None,currency=None,detector=None,severity=None):
    sources,env=context(service,principal,environment)
    if kind not in ('BUDGET','FORECAST','ANOMALY') or type(limit)is not int or not 1<=limit<=100:
        raise denied('validation_error','Invalid financial control query.')
    if month and not re.fullmatch(r'\d{4}-(0[1-9]|1[0-2])',month):raise denied('validation_error','Invalid month.')
    if detail and not re.fullmatch(r'[A-Za-z0-9_.-]{1,64}',detail):raise denied('validation_error','Invalid financial identifier.')
    from .anomaly_registry import DETECTORS
    if scope and not re.fullmatch(r'[A-Za-z0-9_.-]{1,32}',scope) or currency and not re.fullmatch(r'[A-Z]{3}',currency) or detector and detector not in DETECTORS or severity and severity not in ('NONE','WATCH','HIGH','CRITICAL'):
        raise denied('validation_error','Invalid financial control selection.')
    if month is None and not detail and not history:month=now().strftime('%Y-%m')
    repo=getattr(service,'control_repository',None);adapter=sources.adapters.get('DETAILED_BILLING')
    if not repo or not adapter:return Page(environment=env,state='NOT_CONFIGURED' if kind=='BUDGET' else 'UNEVALUATED')
    registry=getattr(service,'control_budget_registry',None)
    if kind=='BUDGET' and registry is not None and not detail and not history and not registry.active(env,month,now()):
        return Page(environment=env,state='NOT_CONFIGURED')
    try:
        async with asyncio.timeout(2),service.reads:
            rows=await repo.current(env,adapter.scope,kind,month)
            generations=await repo.history(env,adapter.scope,kind,month,1001) if history or detail and kind!='BUDGET' else [g for h,g in rows if g]
            if kind=='ANOMALY' and not history:
                retained=await repo.active(env,adapter.scope,month)
                ids={g.identity for g in generations}
                generations.extend(g for g,anomaly,state in retained if g.identity not in ids and not ids.add(g.identity))
            selection=digest([env,kind,month,detail,history,scope,currency,detector,severity,[(g.identity,g.issue.isoformat()) for g in generations],[(h.key,h.revision,h.error) for h,g in rows]])
            page=service.cursor(cursor,selection);offset=page.get('offset',0) if page else 0
            if type(offset)is not int or not 0<=offset<=100000:raise denied('validation_error','Invalid cursor.')
            accounting=getattr(service,'accounting',None)
            live_health=await accounting.repository.health(env) if accounting else None
            accounting_stale=bool(live_health and (live_health['debt'] or live_health['pending'] or live_health['conflicts'] or live_health['updated_at'] is None or (now()-live_health['updated_at']).total_seconds()>300))
            items=[];evaluated=0;state='UNEVALUATED';health_by={h.generation:h.error for h,g in rows}
            for g in generations:
                async with repo.sessions() as s:inp=await s.get(InputRow,g.inputs)
                inputs=Inputs.model_validate(inp.body['inputs']);fresh=await repo.adapter.fresh(inputs,now(),Policy.model_validate(inp.body['policy']))
                freshness='FRESH' if fresh and not accounting_stale and not health_by.get(g.identity) else 'STALE'
                result=g.body['result'];state=result['state'];evaluated+=result.get('evaluated',0)
                raw_items=result.get('items',result.get('results',[]))
                for raw in raw_items:
                    if scope and raw.get('scope','TOTAL')!=scope or currency and g.body['currency']!=currency or detector and raw.get('type')!=detector or severity and raw.get('severity','NONE')!=severity:continue
                    item=project(g,raw,freshness,inputs.scope)
                    if detail and detail not in (item.budget_id,item.generation,item.anomaly_id):continue
                    items.append(item)
            if detail and not items:raise denied('not_found','Financial control not found.')
            if any(i.freshness!='FRESH' for i in items):state='SOURCE_STALE'
            if kind=='ANOMALY' and not history:
                deduplicated={}
                for item in items:deduplicated.setdefault(item.anomaly_id,item)
                items=list(deduplicated.values())
            selected=items[offset:offset+limit];more=offset+limit<len(items)
            return Page(environment=env,state=state,items=tuple(selected),evaluated=evaluated,
                next_cursor=service.encode(scope=selection,offset=offset+limit) if more else None)
    except Exception as exc:
        from backend.gateway.safe_error import SafeErrorException
        if isinstance(exc,SafeErrorException):raise
        raise denied('connector_unavailable','Financial controls unavailable.') from None
