"""Independently runnable operational GET API. No ANOC lifecycle or writer startup."""
import asyncio
import os
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request, Depends, APIRouter
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy import text
from backend.api.errors import handle_safe_error, handle_unexpected_error, safe_error_response
from backend.gateway.safe_error import SafeErrorException, SafeError
from backend.observability.config import ObservabilityConfig
from backend.observability.read_config import ReadConfiguration, producer_policy
from backend.observability.read_service import ReaderService, bind_repositories
from backend.observability.read_database import ReadDatabase
from backend.observability.repository import Repository
from backend.api.observability_routes import read_router

# Fail closed even if a future GET is added to the shared router.
READ_PATHS = frozenset({
 '/policies','/access','/archive/{trace_id}','/runs','/active','/runs/{run_id}',
 '/runs/{run_id}/retention','/runs/{run_id}/audit','/runs/{run_id}/timeline','/sessions/{session_id}/runs',
 '/health','/config','/slos','/slos/{slo_id}','/hybrid/activity',
 '/finops/runtime-usage','/finops/ledger-health','/finops/ledger-completeness',
 '/finops/billing-health','/finops/pricing-status','/finops/billing-summary',
 '/finops/reconciliation','/finops/reconciliation/{period}','/finops/allocation',
 '/finops/unit-economics','/finops/unit-economics/{unit_id}',
 '/finops/budgets','/finops/budgets/{budget_id}','/finops/forecasts',
 '/finops/forecasts/{generation_id}','/finops/anomalies','/finops/anomalies/{anomaly_id}',
})
# Endpoint source audit. Local state below is explicitly unavailable in reader DTOs.
SOURCE_CLASSIFICATION = {path:('PUBLISHED/CACHED' if path.startswith('/finops/') and
    path not in ('/finops/runtime-usage','/finops/ledger-health','/finops/ledger-completeness')
    or path in ('/config','/policies') else 'DURABLE') for path in READ_PATHS}
SOURCE_CLASSIFICATION.update({'/access':'PROCESS_LOCAL_READ_PLANE',
 '/health':'DURABLE + PROCESS_LOCAL_ANOC (unavailable) + PROCESS_LOCAL_READ_PLANE',
 '/finops/ledger-health':'DURABLE + PROCESS_LOCAL_ANOC (unavailable)',
 '/archive/{trace_id}':'PUBLISHED/CACHED'})


def create_app(*, config=None, settings=None, isolated_test=False):
    config=config or ObservabilityConfig.from_settings_mapping(os.environ,graph_fallback=10,knowledge_fallback=10)
    settings=settings or ReadConfiguration.from_environment(environment=config.otel_environment)
    policy=producer_policy(settings,config.otel_environment)

    @asynccontextmanager
    async def lifespan(app):
        from backend.authentication import validate_identity_configuration
        validate_identity_configuration(config.otel_environment)
        from backend.observability.logging import ProductionLogging
        logging_lease=ProductionLogging(config)  # safe JSON only; no OTel runtime/exporter
        database=None
        service=ReaderService(None,policy or config,settings,policy=policy)
        try:
            if settings.database_url is not None:
                database=ReadDatabase(settings,isolated_test=isolated_test)
                service.repository=Repository(database.sessions,service.config)
                bind_repositories(service,database.sessions)
            app.state.observability_service=service
            app.state.read_database=database
            app.state.observability_archive=None
            if settings.archive_root is not None:
                # Local filesystem adapter uses existing protected reconstruction only.
                from backend.observability.archive import FilesystemArchive
                archive=FilesystemArchive(settings.archive_root,config.otel_environment,read_only=True)
                class ArchiveReader:
                    environment=archive.environment
                    def reconstruct(self,*args): return archive.reconstruct(*args)
                app.state.observability_archive=ArchiveReader()
            yield
        finally:
            try:
                if database:
                    async with asyncio.timeout(3): await database.close()
            finally:
                app.state.observability_service=None
                app.state.read_database=None
                app.state.observability_archive=None
                logging_lease.close()

    app=FastAPI(title='SLOPANOC Observability Read API',lifespan=lifespan,
        docs_url=None,redoc_url=None,openapi_url=None)
    app.state.authentication_environment=config.otel_environment
    from dataclasses import replace
    from backend.observability.authorization import verified_principal, verified_principal_provider, denied
    async def scoped_principal(request: Request, value=Depends(verified_principal_provider)):
        principal=await verified_principal(value)
        environment=request.query_params.get('environment')
        if environment is not None:
            if environment not in principal.environments: raise denied('authorization_error')
            principal=replace(principal,environments=frozenset({environment}))
        return principal
    app.dependency_overrides[verified_principal]=scoped_principal
    app.add_exception_handler(SafeErrorException,handle_safe_error)
    app.add_exception_handler(Exception,handle_unexpected_error)
    async def invalid_request(_request,_error):
        return safe_error_response(SafeError(error_code='validation_error',user_message='Invalid diagnostic query.'))
    app.add_exception_handler(RequestValidationError,invalid_request)
    # Explicit allowlist at composition; mutation routes are never installed.
    approved = APIRouter()
    for route in read_router.routes:
        path=route.path.removeprefix('/api/observability')
        if path in READ_PATHS:
            if route.methods!={'GET'}: raise ValueError('Reader route is not GET-only')
            approved.routes.append(route)
    app.include_router(approved)
    installed={r.path.removeprefix('/api/observability') for r in app.routes}
    if installed!=READ_PATHS: raise ValueError('Reader route inventory mismatch')

    @app.middleware('http')
    async def bound_reads(request: Request, call_next):
        if request.method not in ('GET','HEAD','OPTIONS'):
            return JSONResponse(status_code=405,content={'detail':'Method not allowed'})
        service=getattr(app.state,'observability_service',None)
        if service is None and request.url.path.startswith('/api/'):
            return safe_error_response(SafeError(error_code='connector_unavailable',user_message='Read service unavailable.'))
        # Health/config/archive are bound to one deployment environment; reject mismatched selector.
        if service and request.url.path.startswith('/api/observability'):
            env=request.query_params.get('environment')
            if env is not None and request.url.path in {
                '/api/observability/health','/api/observability/config','/api/observability/policies'}:
                from backend.observability.authorization import denied
                if env!=service.config.otel_environment:
                    return safe_error_response(denied('authorization_error').safe_error)
        try:
            async with asyncio.timeout(settings.read_deadline): return await call_next(request)
        except TimeoutError:
            return safe_error_response(SafeError(error_code='connector_unavailable',user_message='Read deadline exceeded.'))

    if settings.allowed_origins:
        app.add_middleware(CORSMiddleware,allow_origins=list(settings.allowed_origins),
            allow_methods=['GET','HEAD','OPTIONS'],allow_headers=['Authorization','traceparent','tracestate'],
            allow_credentials=False,max_age=600)

    @app.get('/health')
    async def health(): return {'status':'ok','service':'observability_read_plane'}

    @app.get('/readiness')
    async def readiness():
        db=getattr(app.state,'read_database',None)
        ready=False
        if db:
            try:
                async with asyncio.timeout(settings.read_deadline),db.sessions() as session:
                    await session.execute(text('SELECT 1'));ready=True
            except Exception: pass
        return JSONResponse(status_code=200 if ready else 503,
            content={'status':'ready' if ready else 'unavailable','service':'observability_read_plane'})
    return app
