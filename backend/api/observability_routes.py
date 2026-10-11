"""Protected read-only diagnostics in the existing API; default identity is denied."""
from datetime import datetime
from fastapi import APIRouter, Depends, Query, Request
from backend.observability.authorization import require, denied
from backend.observability.schemas import Permission, EffectiveConfiguration
from backend.observability.authorization import verified_principal
from backend.observability.policy_views import PolicyView, AccessView, ArchivePage, RunRetentionView, policy_view
from backend.observability.api_models import RunView, RunPage, TimelinePage, HealthView
from backend.observability.service import Service

read_router = APIRouter(prefix='/api/observability',tags=['observability'])
receipt_router = APIRouter(prefix='/api/observability',tags=['observability'])
operational = require(Permission.OPERATIONAL_METADATA)
configuration = require(Permission.EFFECTIVE_CONFIGURATION)
technical = require(Permission.TECHNICAL_DIAGNOSTICS)


def service(request: Request):
    value=getattr(request.app.state,'observability_service',None)
    if value is None:
        from backend.config.settings import get_settings
        value=Service(None,get_settings().observability_config)
    return value

@read_router.get('/policies',response_model=PolicyView)
async def policies(principal=Depends(configuration),backend=Depends(service)):
    backend.admit(principal)
    backend.environment_scope(principal,backend.config.otel_environment)
    return policy_view(backend.effective_policy() if hasattr(backend,'effective_policy') else backend.config)

@read_router.get('/access',response_model=AccessView)
async def access(principal=Depends(verified_principal),backend=Depends(service)):
    backend.admit(principal)
    return AccessView(capabilities=tuple(sorted(principal.capabilities,key=lambda v:v.value)),
        environments=tuple(sorted(principal.environments)))

@read_router.get('/archive/{trace_id}',response_model=ArchivePage,response_model_exclude_none=True)
async def archive_page(request:Request,trace_id:str,digest:str=Query(pattern='^[0-9a-f]{64}$'),
    offset:int=Query(0,ge=0,le=10000),limit:int=Query(50,ge=1,le=100),
    principal=Depends(technical),backend=Depends(service)):
    import asyncio
    from backend.observability.archive import attrs,identifier
    from backend.observability.policy_views import ArchiveSpanView
    from opentelemetry.proto.collector.trace.v1.trace_service_pb2 import ExportTraceServiceRequest
    backend.admit(principal)
    backend.environment_scope(principal,backend.config.otel_environment)
    archive=getattr(request.app.state,'observability_archive',None)
    if archive is None or archive.environment!=backend.config.otel_environment:
        raise denied('connector_unavailable','Archive retrieval is unavailable.')
    try:
        identifier(trace_id,32)
        if hasattr(backend,'reconstruct_archive'):
            async with asyncio.timeout(2):
                payload=await backend.reconstruct_archive(archive,principal,trace_id,digest)
        else:
            async with asyncio.timeout(2),backend.reads:
                payload=await asyncio.to_thread(archive.reconstruct,principal,trace_id,digest)
        value=ExportTraceServiceRequest();value.ParseFromString(payload)
        all_spans=[s for r in value.resource_spans for scope in r.scope_spans for s in scope.spans]
        page=ArchivePage(trace_id=trace_id,digest=digest,spans=tuple(ArchiveSpanView(
            trace_id=s.trace_id.hex(),span_id=s.span_id.hex(),parent_span_id=s.parent_span_id.hex() or None,
            name=s.name,kind=s.kind,start_ns=s.start_time_unix_nano,end_ns=s.end_time_unix_nano,status=s.status.code,
            attributes=attrs(s.attributes),events=tuple({'name':e.name,'at_ns':e.time_unix_nano,
                'attributes':attrs(e.attributes)} for e in s.events)) for s in all_spans[offset:offset+limit]),
            next_offset=offset+limit if offset+limit<len(all_spans) else None)
        if len(page.model_dump_json().encode())>4*1024*1024: raise ValueError('Archive page bound')
        return page
    except Exception:
        raise denied('connector_unavailable','Archive retrieval is unavailable.') from None


@read_router.get('/runs',response_model=RunPage,response_model_exclude_none=True)
@read_router.get('/active',response_model=RunPage,response_model_exclude_none=True)
async def runs(request:Request, principal=Depends(operational), backend=Depends(service),
    limit:int=Query(50,ge=1,le=100),cursor:str|None=Query(None,max_length=512),
    status:str|None=Query(None,max_length=16),stage:str|None=Query(None,max_length=64),
    environment:str|None=Query(None,max_length=64),since:datetime|None=None,until:datetime|None=None):
    backend.admit(principal)
    return await backend.listing(principal,limit=limit,cursor=cursor,active=request.url.path.endswith('/active'),
        status=status,stage=stage,environment=environment,since=since,until=until)


@read_router.get('/runs/{run_id}',response_model=RunView,response_model_exclude_none=True)
async def run(run_id:str,principal=Depends(operational),backend=Depends(service)):
    backend.admit(principal)
    return await backend.run(run_id,principal)

@read_router.get('/runs/{run_id}/retention',response_model=RunRetentionView)
async def run_retention(run_id:str,principal=Depends(operational),backend=Depends(service)):
    from backend.observability.projection import utcnow
    backend.admit(principal)
    value=await backend.query('retention_view',backend.run_id(run_id),backend.environment_scope(principal),utcnow())
    if value is None:raise denied('not_found','Run is unavailable.')
    return value


@read_router.get('/runs/{run_id}/timeline',response_model=TimelinePage,response_model_exclude_none=True)
async def timeline(run_id:str,principal=Depends(operational),backend=Depends(service),
    limit:int=Query(50,ge=1,le=100),cursor:str|None=Query(None,max_length=512)):
    backend.admit(principal)
    return await backend.timeline(run_id,principal,limit,cursor)


@read_router.get('/sessions/{session_id}/runs',response_model=RunPage,response_model_exclude_none=True)
async def session_runs(session_id:str,principal=Depends(operational),backend=Depends(service),
    limit:int=Query(50,ge=1,le=100),cursor:str|None=Query(None,max_length=512)):
    backend.admit(principal)
    return await backend.listing(principal,session_id=session_id,limit=limit,cursor=cursor)


from backend.observability.hybrid.read import ActivityView

@read_router.get('/hybrid/activity',response_model=ActivityView)
async def hybrid_activity(environment:str|None=Query(None,max_length=16),
    limit:int=Query(50,ge=1,le=100),principal=Depends(operational),backend=Depends(service)):
    import asyncio
    backend.admit(principal)
    env=environment or backend.config.otel_environment
    backend.environment_scope(principal,env)
    reader=getattr(backend,'hybrid_reads',None)
    if reader is None: raise denied('connector_unavailable','Hybrid projections unavailable.')
    try:
        async with asyncio.timeout(2),backend.reads:
            return await reader.activity(env,limit)
    except Exception:
        raise denied('connector_unavailable','Hybrid projections unavailable.') from None


@read_router.get('/health',response_model=HealthView)
async def health(principal=Depends(operational),backend=Depends(service)):
    backend.admit(principal)
    return await backend.health(principal)


@read_router.get('/config',response_model=list[EffectiveConfiguration])
async def config(principal=Depends(configuration),backend=Depends(service)):
    backend.admit(principal)
    backend.environment_scope(principal,backend.config.otel_environment)
    return list((backend.effective_policy() if hasattr(backend,'effective_policy') else backend.config).effective_configuration())

# M9 reads retain the M7 verified-principal/capability/environment gate.
from backend.observability.slo_api_models import SLOPage, SLOView, SSEReceipt
from backend.observability.authorization import verified_principal

@read_router.get('/slos',response_model=SLOPage)
async def slo_listing(environment:str|None=Query(None,max_length=16),principal=Depends(operational),backend=Depends(service)):
    from backend.observability.slo_service import slos
    return await slos(backend,principal,environment)

@read_router.get('/slos/{slo_id}',response_model=SLOView)
async def slo_detail(slo_id:str,environment:str|None=Query(None,max_length=16),principal=Depends(operational),backend=Depends(service)):
    from backend.observability.slo_service import slos
    return await slos(backend,principal,environment,slo_id)

@receipt_router.post('/sse-receipts',status_code=204)
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

@read_router.get('/finops/runtime-usage',response_model=UsagePage)
async def runtime_usage(principal=Depends(financial),backend=Depends(service),
    environment:str|None=Query(None,max_length=16),start:datetime|None=None,end:datetime|None=None,
    group:str=Query('model',max_length=32),limit:int=Query(50,ge=1,le=100),cursor:str|None=Query(None,max_length=512)):
    return await finops.runtime_usage(backend,principal,environment,start,end,group,limit,cursor)

@read_router.get('/finops/ledger-health',response_model=LedgerHealth)
async def ledger_health(principal=Depends(financial),backend=Depends(service),environment:str|None=Query(None,max_length=16)):
    return await finops.ledger_health(backend,principal,environment)

@read_router.get('/finops/ledger-completeness',response_model=SLOView)
async def ledger_completeness(principal=Depends(financial),backend=Depends(service),environment:str|None=Query(None,max_length=16)):
    return await finops.completeness(backend,principal,environment)

# M11 reads published financial projections only, never synchronous BigQuery.
from datetime import date
from backend.observability.finops import billing_service
from backend.observability.finops.billing_api_models import HealthPage, BillingSummary, PricingStatus

@read_router.get('/finops/billing-health',response_model=HealthPage)
async def billing_health(principal=Depends(financial),backend=Depends(service),environment:str|None=Query(None,max_length=16)):
    return await billing_service.health(backend,principal,environment)

@read_router.get('/finops/pricing-status',response_model=PricingStatus)
async def pricing_status(principal=Depends(financial),backend=Depends(service),environment:str|None=Query(None,max_length=16)):
    return await billing_service.pricing_status(backend,principal,environment)

@read_router.get('/finops/billing-summary',response_model=BillingSummary)
async def billing_summary(principal=Depends(financial),backend=Depends(service),environment:str|None=Query(None,max_length=16),
    period_basis:str=Query('usage',max_length=8),start:date|None=None,end:date|None=None,
    invoice_month:str|None=Query(None,max_length=6),limit:int=Query(50,ge=1,le=100),cursor:str|None=Query(None,max_length=512)):
    return await billing_service.summary(backend,principal,environment,period_basis,start,end,invoice_month,limit,cursor)

# M12 financial cache reads; no synchronous source jobs or reconciliation.
from backend.observability.finops import reconciliation_service
from backend.observability.finops.reconciliation_api_models import Page as ReconciliationPage

@read_router.get('/finops/reconciliation',response_model=ReconciliationPage)
async def reconciliation(environment: str|None=None,start: date|None=None,end: date|None=None,
        limit: int=31,cursor: str|None=None,principal=Depends(financial),backend=Depends(service)):
    return await reconciliation_service.read(backend,principal,environment,start,end,limit=limit,cursor=cursor)

@read_router.get('/finops/reconciliation/{period}',response_model=ReconciliationPage)
async def reconciliation_period(period: str,environment: str|None=None,principal=Depends(financial),backend=Depends(service)):
    return await reconciliation_service.read(backend,principal,environment,period=period)

from backend.observability.finops import unit_economics_service
from backend.observability.finops.unit_economics_api_models import Page as UnitEconomicsPage

@read_router.get('/finops/unit-economics',response_model=UnitEconomicsPage)
async def unit_economics(environment: str|None=None,start: date|None=None,end: date|None=None,
        currency: str|None=None,dimension: str|None=None,limit: int=31,cursor: str|None=None,
        principal=Depends(financial),backend=Depends(service)):
    return await unit_economics_service.read(backend,principal,environment,start,end,
        currency=currency,dimension=dimension,limit=limit,cursor=cursor)

@read_router.get('/finops/unit-economics/{unit_id}',response_model=UnitEconomicsPage)
async def unit_economics_unit(unit_id: str,environment: str|None=None,start: date|None=None,end: date|None=None,
        currency: str|None=None,dimension: str|None=None,limit: int=31,cursor: str|None=None,
        principal=Depends(financial),backend=Depends(service)):
    return await unit_economics_service.read(backend,principal,environment,start,end,unit_id,
        currency,dimension,limit,cursor)

@read_router.get('/finops/allocation',response_model=ReconciliationPage)
async def allocation(environment: str|None=None,start: date|None=None,end: date|None=None,
        limit: int=31,cursor: str|None=None,principal=Depends(financial),backend=Depends(service)):
    return await reconciliation_service.read(backend,principal,environment,start,end,limit=limit,cursor=cursor)

# M14: financial capability, cached projections only, no mutation endpoints.
from backend.observability.finops import control_service
from backend.observability.finops.control_api_models import Page as ControlPage

@read_router.get('/finops/budgets',response_model=ControlPage)
async def control_budgets(environment: str|None=Query(None,max_length=16),month: str|None=Query(None,max_length=7),
        limit: int=Query(50,ge=1,le=100),cursor: str|None=Query(None,max_length=512),history: bool=False,scope: str|None=Query(None,max_length=32),currency: str|None=Query(None,max_length=3),
        detector: str|None=Query(None,max_length=32),severity: str|None=Query(None,max_length=8),
        principal=Depends(financial),backend=Depends(service)):
    return await control_service.read(backend,principal,'BUDGET',environment,month,limit,cursor,history=history,scope=scope,currency=currency,detector=detector,severity=severity)

@read_router.get('/finops/budgets/{budget_id}',response_model=ControlPage)
async def control_budgets_detail(budget_id: str,environment: str|None=Query(None,max_length=16),
        month: str|None=Query(None,max_length=7),limit: int=Query(50,ge=1,le=100),cursor: str|None=Query(None,max_length=512),
        principal=Depends(financial),backend=Depends(service)):
    return await control_service.read(backend,principal,'BUDGET',environment,month,limit,cursor,detail=budget_id,history=True)

@read_router.get('/finops/forecasts',response_model=ControlPage)
async def control_forecasts(environment: str|None=Query(None,max_length=16),month: str|None=Query(None,max_length=7),
        limit: int=Query(50,ge=1,le=100),cursor: str|None=Query(None,max_length=512),history: bool=False,scope: str|None=Query(None,max_length=32),currency: str|None=Query(None,max_length=3),
        detector: str|None=Query(None,max_length=32),severity: str|None=Query(None,max_length=8),
        principal=Depends(financial),backend=Depends(service)):
    return await control_service.read(backend,principal,'FORECAST',environment,month,limit,cursor,history=history,scope=scope,currency=currency,detector=detector,severity=severity)

@read_router.get('/finops/forecasts/{generation_id}',response_model=ControlPage)
async def control_forecasts_detail(generation_id: str,environment: str|None=Query(None,max_length=16),
        month: str|None=Query(None,max_length=7),limit: int=Query(50,ge=1,le=100),cursor: str|None=Query(None,max_length=512),
        principal=Depends(financial),backend=Depends(service)):
    return await control_service.read(backend,principal,'FORECAST',environment,month,limit,cursor,detail=generation_id,history=True)

@read_router.get('/finops/anomalies',response_model=ControlPage)
async def control_anomalies(environment: str|None=Query(None,max_length=16),month: str|None=Query(None,max_length=7),
        limit: int=Query(50,ge=1,le=100),cursor: str|None=Query(None,max_length=512),history: bool=False,scope: str|None=Query(None,max_length=32),currency: str|None=Query(None,max_length=3),
        detector: str|None=Query(None,max_length=32),severity: str|None=Query(None,max_length=8),
        principal=Depends(financial),backend=Depends(service)):
    return await control_service.read(backend,principal,'ANOMALY',environment,month,limit,cursor,history=history,scope=scope,currency=currency,detector=detector,severity=severity)

@read_router.get('/finops/anomalies/{anomaly_id}',response_model=ControlPage)
async def control_anomalies_detail(anomaly_id: str,environment: str|None=Query(None,max_length=16),
        month: str|None=Query(None,max_length=7),limit: int=Query(50,ge=1,le=100),cursor: str|None=Query(None,max_length=512),
        principal=Depends(financial),backend=Depends(service)):
    return await control_service.read(backend,principal,'ANOMALY',environment,month,limit,cursor,detail=anomaly_id,history=True)

# ANOC retains both existing surfaces. The independent app mounts only reviewed reads.
router = APIRouter()
router.include_router(read_router)
router.include_router(receipt_router)


from backend.execution_audit.read import AuditPage

@read_router.get('/runs/{run_id}/audit',response_model=AuditPage)
async def run_audit(run_id:str,limit:int=Query(50,ge=1,le=100),cursor:str|None=Query(None,max_length=512),
    principal=Depends(operational),backend=Depends(service)):
    import asyncio
    backend.admit(principal)
    identity=backend.run_id(run_id)
    env=backend.config.otel_environment
    backend.environment_scope(principal,env)
    await backend.run(identity,principal)
    reader=getattr(backend,'execution_audit_reads',None)
    if reader is None:raise denied('connector_unavailable','Audit timeline unavailable.')
    try:
        async with asyncio.timeout(2),backend.reads:
            return await reader.timeline(env,identity,limit,cursor)
    except ValueError:
        raise denied('validation_error','Invalid audit cursor.') from None
    except Exception:
        raise denied('connector_unavailable','Audit timeline unavailable.') from None
