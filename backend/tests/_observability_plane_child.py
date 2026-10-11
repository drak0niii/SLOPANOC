"""Isolated subprocess fixture only. Never a production launch/auth configuration."""
import asyncio
import json
import os
from pathlib import Path
import sys
from unittest.mock import Mock

mode,port,root=sys.argv[1:]
root=Path(root).resolve()
if not root.name.startswith('reader-process-') or not (root/'disposable.marker').is_file():
    raise SystemExit('Disposable fixture marker required')
os.environ.update(SLOPANOC_DATABASE_URL='sqlite+aiosqlite:///'+str(root/'data.sqlite'),
    SLOPANOC_KNOWLEDGE_DATABASE_URL='sqlite+aiosqlite:///'+str(root/'data.sqlite'),
    SLOPANOC_SESSION_BACKEND='memory',SLOPANOC_MODEL_WARMUP_ENABLED='false',
    SLOPANOC_OTEL_ENABLED='false',SLOPANOC_PROJECTION_ENABLED='false',SLOPANOC_FINOPS_ENABLED='false',
    SLOPANOC_OTEL_ENVIRONMENT='development',
    SLOPANOC_AUTH_MODE='development',SLOPANOC_DEV_AUTH_POLICY_FILE=str(root/'auth.json'))
from backend.observability.authorization import Principal,verified_principal_provider
from backend.observability.schemas import Role,ROLE_PERMISSIONS
from backend.observability.read_config import ReadConfiguration
from backend.tests._m7_storage import config
if mode=='anoc':
    import backend.api.app as business
    business.validate_runtime_database_configuration=lambda _settings:('postgresql','postgresql')
    app=business.create_app()
else:
    from backend.api.observability_app import create_app
    app=create_app(config=config(),settings=ReadConfiguration(
        database_url=os.environ['SLOPANOC_DATABASE_URL'],read_deadline=.2),isolated_test=True)
counts={'model':0,'agent':0,'tool':0,'command':0,'jobs':0}
def forbidden(kind):
    def call(*args,**kwargs):
        counts[kind]+=1
        raise AssertionError('Fixture forbids external/business dispatch')
    return call
from google.genai import models
models.Models.generate_content=forbidden('model');models.AsyncModels.generate_content=forbidden('model')
from google.adk.runners import Runner
Runner.run_async=forbidden('agent')
from google.adk.tools.function_tool import FunctionTool
FunctionTool.run_async=forbidden('tool')
import backend.api.execution_service as execution
# Instrument actual execution entry method, while preserving the business API.
for name in ('execute','execute_proposal'):
    if hasattr(execution,name):setattr(execution,name,forbidden('command'))

from backend.observability.finops.billing_ingestion import Ingestion
from backend.observability.finops.reconciliation_worker import Worker as ReconciliationWorker
from backend.observability.finops.unit_economics_worker import Worker as UnitWorker
from backend.observability.finops.control_worker import Worker as ControlWorker
from backend.observability.retention_worker import RetentionWorker
for owner,method in ((Ingestion,'start'),(Ingestion,'ingest'),(ReconciliationWorker,'start'),
                     (ReconciliationWorker,'run_period'),(UnitWorker,'start'),(UnitWorker,'run_period'),
                     (ControlWorker,'start'),(ControlWorker,'evaluate'),(RetentionWorker,'run')):
    setattr(owner,method,forbidden('jobs'))

from fastapi import Depends
from backend.observability.authorization import verified_principal
@app.get('/_fixture/identity')
async def fixture_identity(principal=Depends(verified_principal)):
    return {'subject':principal.subject}

@app.get('/_fixture/stats')
async def stats():return dict(pid=os.getpid(),counts=counts)

@app.middleware('http')
async def fixture_delay(request,call_next):
    if mode=='read' and request.headers.get('X-Fixture-Slow')=='true':await asyncio.sleep(.4)
    return await call_next(request)
import uvicorn
uvicorn.run(app,host='127.0.0.1',port=int(port),log_level='error',access_log=False)
