"""Protected read-only diagnostics in the existing API; default identity is denied."""
from datetime import datetime
from fastapi import APIRouter, Depends, Query, Request
from backend.observability.authorization import require, denied
from backend.observability.schemas import Permission, EffectiveConfiguration
from backend.observability.api_models import RunView, RunPage, TimelinePage, HealthView
from backend.observability.service import Service

router = APIRouter(prefix='/api/observability',tags=['observability'])
operational = require(Permission.OPERATIONAL_METADATA)
configuration = require(Permission.EFFECTIVE_CONFIGURATION)


def service(request: Request):
    value=getattr(request.app.state,'observability_service',None)
    if value is None:
        from backend.config.settings import get_settings
        value=Service(None,get_settings().observability_config)
    return value


@router.get('/runs',response_model=RunPage,response_model_exclude_none=True)
@router.get('/active',response_model=RunPage,response_model_exclude_none=True)
async def runs(request:Request, principal=Depends(operational), backend=Depends(service),
    limit:int=Query(50,ge=1,le=100),cursor:str|None=Query(None,max_length=512),
    status:str|None=Query(None,max_length=16),stage:str|None=Query(None,max_length=64),
    environment:str|None=Query(None,max_length=64),since:datetime|None=None,until:datetime|None=None):
    backend.admit(principal)
    return await backend.listing(principal,limit=limit,cursor=cursor,active=request.url.path.endswith('/active'),
        status=status,stage=stage,environment=environment,since=since,until=until)


@router.get('/runs/{run_id}',response_model=RunView,response_model_exclude_none=True)
async def run(run_id:str,principal=Depends(operational),backend=Depends(service)):
    backend.admit(principal)
    return await backend.run(run_id,principal)


@router.get('/runs/{run_id}/timeline',response_model=TimelinePage,response_model_exclude_none=True)
async def timeline(run_id:str,principal=Depends(operational),backend=Depends(service),
    limit:int=Query(50,ge=1,le=100),cursor:str|None=Query(None,max_length=512)):
    backend.admit(principal)
    return await backend.timeline(run_id,principal,limit,cursor)


@router.get('/sessions/{session_id}/runs',response_model=RunPage,response_model_exclude_none=True)
async def session_runs(session_id:str,principal=Depends(operational),backend=Depends(service),
    limit:int=Query(50,ge=1,le=100),cursor:str|None=Query(None,max_length=512)):
    backend.admit(principal)
    return await backend.listing(principal,session_id=session_id,limit=limit,cursor=cursor)


@router.get('/health',response_model=HealthView,response_model_exclude_none=True)
async def health(principal=Depends(operational),backend=Depends(service)):
    backend.admit(principal)
    return await backend.health(principal)


@router.get('/config',response_model=list[EffectiveConfiguration])
async def config(principal=Depends(configuration),backend=Depends(service)):
    backend.admit(principal)
    backend.environment_scope(principal,backend.config.otel_environment)
    return list(backend.config.effective_configuration())

# M9 reads retain the M7 verified-principal/capability/environment gate.
from backend.observability.slo_api_models import SLOPage, SLOView, SSEReceipt
from backend.observability.authorization import verified_principal

@router.get('/slos',response_model=SLOPage)
async def slo_listing(environment:str|None=Query(None,max_length=16),principal=Depends(operational),backend=Depends(service)):
    from backend.observability.slo_service import slos
    return await slos(backend,principal,environment)

@router.get('/slos/{slo_id}',response_model=SLOView)
async def slo_detail(slo_id:str,environment:str|None=Query(None,max_length=16),principal=Depends(operational),backend=Depends(service)):
    from backend.observability.slo_service import slos
    return await slos(backend,principal,environment,slo_id)

@router.post('/sse-receipts',status_code=204)
async def sse_receipt(body:SSEReceipt,principal=Depends(verified_principal),backend=Depends(service)):
    from backend.observability.slo_sources import subject_digest, now_utc
    from fastapi import Response
    # A verified authenticated owner may submit diagnostics; role fields are never accepted.
    if Permission.OWN_PROGRESS not in principal.capabilities:
        raise denied('authorization_error','Receipt access is not permitted.')
    backend.admit(principal)
    backend.run_id(body.run_id)
    writer=getattr(backend.runtime,'sre',None)
    if writer is None: raise denied('connector_unavailable','Receipt source unavailable.')
    try:
        async with __import__('asyncio').timeout(2),backend.reads:
            # Flush already scheduled diagnostic writes so a fast browser receipt
            # cannot race the emitted-event projection. The total endpoint bound
            # still applies and chat execution never waits for this endpoint.
            if hasattr(writer, "drain"): await writer.drain()
            accepted=await writer.rollups.receive(body.run_id,subject_digest(principal.subject),tuple(principal.environments),now_utc())
    except Exception: raise denied('connector_unavailable','Receipt source unavailable.') from None
    if not accepted: raise denied('not_found','Receipt target unavailable.')
    return Response(status_code=204)

# Financial access is a distinct server capability; it grants no run diagnostics.
from backend.observability.finops.api_models import UsagePage, LedgerHealth
from backend.observability.finops import service as finops
financial = require(Permission.FINANCIAL_DATA)

@router.get('/finops/runtime-usage',response_model=UsagePage)
async def runtime_usage(principal=Depends(financial),backend=Depends(service),
    environment:str|None=Query(None,max_length=16),start:datetime|None=None,end:datetime|None=None,
    group:str=Query('model',max_length=32),limit:int=Query(50,ge=1,le=100),cursor:str|None=Query(None,max_length=512)):
    return await finops.runtime_usage(backend,principal,environment,start,end,group,limit,cursor)

@router.get('/finops/ledger-health',response_model=LedgerHealth)
async def ledger_health(principal=Depends(financial),backend=Depends(service),environment:str|None=Query(None,max_length=16)):
    return await finops.ledger_health(backend,principal,environment)

@router.get('/finops/ledger-completeness',response_model=SLOView)
async def ledger_completeness(principal=Depends(financial),backend=Depends(service),environment:str|None=Query(None,max_length=16)):
    return await finops.completeness(backend,principal,environment)
