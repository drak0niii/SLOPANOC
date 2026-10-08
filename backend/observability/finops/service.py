"""Bounded protected read facade; existing principal/environment/rate ownership."""
import asyncio
from datetime import timedelta, timezone
from ..authorization import denied
from ..slo_api_models import SLOView
from ..slo_evaluator import evaluate
from .repository import utcnow
from .api_models import UsagePage, LedgerHealth
from .contracts import ENVIRONMENTS
from .slo_source import AccountingSource

def ledger_for(service,principal,environment):
    env=environment or service.config.otel_environment
    if env not in ENVIRONMENTS:raise denied('validation_error','Invalid accounting environment.')
    service.environment_scope(principal,env);service.admit(principal)
    ledger=getattr(service,'accounting',None)
    if ledger is None:raise denied('connector_unavailable','Runtime accounting is unavailable.')
    return ledger,env

async def runtime_usage(service,principal,environment=None,start=None,end=None,group='model',limit=50,cursor=None):
    ledger,env=ledger_for(service,principal,environment)
    end=end or utcnow();start=start or end-timedelta(days=1)
    if start.tzinfo is None or end.tzinfo is None or not timedelta(0)<end-start<=timedelta(days=31) or end>utcnow()+timedelta(seconds=5):
        raise denied('validation_error','Invalid accounting time range.')
    start=start.astimezone(timezone.utc);end=end.astimezone(timezone.utc)
    # Aggregates are exact minute populations. Do not silently include partial buckets.
    if start.second or start.microsecond or end.second or end.microsecond:
        if cursor is not None:raise denied('validation_error','Invalid accounting cursor range.')
        start=start.replace(second=0,microsecond=0);end=end.replace(second=0,microsecond=0)
    if group not in ('model','provider','agent','workload','operation','operation_type') or not 1<=limit<=100:
        raise denied('validation_error','Invalid accounting query.')
    scope='|'.join((env,start.isoformat(),end.isoformat(),group))
    page=service.cursor(cursor,scope);offset=page.get('offset',0) if page else 0
    if type(offset) is not int or offset<0 or offset+limit>1000:raise denied('validation_error','Invalid accounting cursor.')
    try:
        async with asyncio.timeout(2),service.reads:
            items,more=await ledger.repository.usage(env,start,end,group,limit,offset)
    except Exception:raise denied('connector_unavailable','Runtime accounting is unavailable.') from None
    next_cursor=service.encode(scope=scope,offset=offset+limit) if more and offset+limit<1000 else None
    return UsagePage(environment=env,start=start,end=end,group=group,items=tuple(items),next_cursor=next_cursor)

async def ledger_health(service,principal,environment=None):
    ledger,env=ledger_for(service,principal,environment)
    try:
        async with asyncio.timeout(2),service.reads:health=await ledger.repository.health(env)
    except Exception:raise denied('connector_unavailable','Runtime accounting is unavailable.') from None
    updated=health['updated_at'];state='HEALTHY'
    if updated is None:state='DATA_SOURCE_UNAVAILABLE'
    elif (utcnow()-updated).total_seconds()>300:state='STALE_DATA'
    elif health['debt'] or health['pending'] or health['conflicts'] or ledger.health_counts['admissions_failed']:state='DEGRADED'
    return LedgerHealth(environment=env,state=state,**health,
        admissions_failed=ledger.health_counts['admissions_failed'],retention_months=ledger.config.finops_retention_months)

async def completeness(service,principal,environment=None):
    ledger,env=ledger_for(service,principal,environment);now=utcnow()
    try:
        async with asyncio.timeout(2),service.reads:
            snapshot=await AccountingSource(ledger.repository).snapshot(env,'cost_ledger_completeness',now)
    except Exception:
        from ..slo_evaluator import Snapshot
        snapshot=Snapshot(env,available=False,source='DURABLE_ACCOUNTING')
    return SLOView.model_validate(evaluate('cost_ledger_completeness',snapshot,now=now))
