"""Protected bounded SLO query facade; no formulas or raw series in API layer."""
import asyncio
from .slo_contract import DEFINITIONS
from .slo_evaluator import evaluate, Snapshot
from .slo_sources import now_utc
from .slo_api_models import SLOView,SLOPage
from .authorization import denied

async def slos(service,principal,environment=None,slo_id=None,*,clock=now_utc):
    environment=environment or service.config.otel_environment
    if environment not in ('local','development','staging','production'): raise denied('validation_error','Invalid SLO environment.')
    service.environment_scope(principal,environment);service.admit(principal)
    if slo_id is not None and slo_id not in DEFINITIONS: raise denied('not_found','SLO definition unavailable.')
    now=clock();provider=getattr(service,'slo_provider',None)
    values=[]
    try:
        async with asyncio.timeout(2),service.reads:
            for key in ([slo_id] if slo_id else DEFINITIONS):
                if provider is None: snapshot=Snapshot(environment,available=False,source='RUNTIME_ROLLUPS')
                else: snapshot=await provider.snapshot(environment,key,now)
                if snapshot.environment!=environment: raise ValueError('Provider environment mismatch')
                result=evaluate(key,snapshot,now=now)
                if environment==service.config.otel_environment:
                    from .slo_metrics import publish_result
                    publish_result(service.runtime,result)
                values.append(SLOView.model_validate(result))
    except Exception:
        values=[SLOView.model_validate(evaluate(key,Snapshot(environment,available=False,source='RUNTIME_ROLLUPS'),now=now)) for key in ([slo_id] if slo_id else DEFINITIONS)]
    return values[0] if slo_id else SLOPage(environment=environment,evaluated_at=now,items=tuple(values))
