"""Actual pinned M3 SDK boundary, fake HTTP provider and durable isolated accounting."""
import json
import asyncio
from datetime import timedelta
import httpx
import pytest
from google.genai import types
from sqlalchemy import select
from backend.tests.test_finops_runtime_ledger import accounting
from backend.tests.test_observability_model_provider import fake_model, payload, request, Bytes, SENTINELS
from backend.observability.model_adapter import instrument_model
from backend.observability.model_provider import embedding_request
from backend.observability.model_context import observation_sink
from backend.observability.finops.models import AttemptRow, InboxRow, LedgerRow
from backend.observability.finops.usage_ledger import install, release, AdmissionFailed

@pytest.mark.asyncio
@pytest.mark.parametrize('stream',[False,True])
async def test_real_m3_transport_waits_for_durable_admission_and_safe_capture(accounting,stream):
    db,repo,ledger=accounting;install(ledger);calls=0
    async def handler(req):
        nonlocal calls
        calls+=1
        # Separate connection proves transactions committed BEFORE actual transport.
        async with db.sessions() as s:
            rows=(await s.execute(select(AttemptRow))).scalars().all()
            assert len(rows)==1 and rows[0].state=='STARTED' and rows[0].started_at is not None
        if stream:return httpx.Response(200,stream=Bytes([payload(100),payload(150),payload(200)]))
        return httpx.Response(200,json=payload(200))
    async with fake_model(handler) as (base,client):
        result=[x async for x in instrument_model(base,'team_manager','orchestration').generate_content_async(request(),stream=stream)]
        assert result and calls==1
    async with db.sessions() as s:
        rows=(await s.execute(select(LedgerRow))).scalars().all()
        assert len(rows)==1 and rows[0].input_tokens==200
        assert (await s.execute(select(AttemptRow.state))).scalar_one()=='USAGE_CAPTURED'
        raw=json.dumps([r.payload for r in (await s.execute(select(InboxRow))).scalars()])+json.dumps(rows[0].metadata_safe)
        assert not any(marker in raw for marker in SENTINELS)
        assert 'prompt' not in raw and 'response' not in raw and 'values' not in raw

@pytest.mark.asyncio
@pytest.mark.parametrize('phase',['admit','start'])
async def test_failed_admission_or_start_never_invokes_provider(accounting,monkeypatch,phase):
    db,repo,ledger=accounting;install(ledger);calls=0
    async def failed(*args):raise OSError('PRIVATE_SQL_CREDENTIAL')
    monkeypatch.setattr(repo,phase,failed)
    def handler(req):
        nonlocal calls
        calls+=1;return httpx.Response(200,json=payload())
    async with fake_model(handler) as (base,client):
        with pytest.raises(AdmissionFailed):
            _=[x async for x in instrument_model(base,'team_manager','orchestration').generate_content_async(request())]
    assert calls==0 and ledger.health_counts['admissions_failed']==1
    async with db.sessions() as s:
        assert list((await s.execute(select(LedgerRow))).scalars())==[]
        assert all(r.started_at is None for r in (await s.execute(select(AttemptRow))).scalars())

@pytest.mark.asyncio
async def test_final_capture_outage_preserves_business_result_and_durable_debt(accounting,monkeypatch):
    db,repo,ledger=accounting;install(ledger);calls=0
    async def failed(*args):raise OSError('PRIVATE_TEAMS_CONTENT')
    monkeypatch.setattr(repo,'capture',failed)
    def handler(req):
        nonlocal calls
        calls+=1;return httpx.Response(200,json=payload())
    async with fake_model(handler) as (base,client):
        result=[x async for x in instrument_model(base,'team_manager','orchestration').generate_content_async(request())]
    assert result and calls==1 and (await repo.health('development'))['debt']==1
    async with db.sessions() as s:assert (await s.execute(select(AttemptRow.state))).scalar_one()=='FINAL_CAPTURE_FAILED'
    assert await ledger.recover()==0 and calls==1

@pytest.mark.asyncio
async def test_total_db_outage_after_provider_still_leaves_started_obligation(accounting,monkeypatch):
    db,repo,ledger=accounting;install(ledger);calls=0
    async def failed(*args):raise OSError('PRIVATE_TOKEN')
    def handler(req):
        nonlocal calls
        calls+=1
        for method in ('finished','capture','failed_capture'):monkeypatch.setattr(repo,method,failed)
        return httpx.Response(200,json=payload())
    async with fake_model(handler) as (base,client):
        result=[x async for x in instrument_model(base,'team_manager','orchestration').generate_content_async(request())]
    assert result and calls==1
    async with db.sessions() as s:assert (await s.execute(select(AttemptRow.state))).scalar_one()=='STARTED'
    assert (await repo.health('development'))['debt']==1

@pytest.mark.asyncio
async def test_embedding_and_warmup_nonuser_workloads(accounting):
    db,repo,ledger=accounting;install(ledger)
    async with fake_model(lambda req:httpx.Response(200,json={'embeddings':[{'values':[.1,.2],'statistics':{'token_count':7}}]})) as (_,client):
        res=await embedding_request(client,agent='knowledge_embedding',operation='embedding',model='text-embedding-005',contents='PRIVATE_EMBEDDING')
        assert res.embeddings
    async with fake_model(lambda req:httpx.Response(200,json=payload())) as (base,_):
        result=[x async for x in instrument_model(base,'system','warmup',workload='warmup').generate_content_async(request())]
        assert result
    async with db.sessions() as s:
        rows=(await s.execute(select(LedgerRow))).scalars().all();assert len(rows)==2
        embedding=next(r for r in rows if r.metadata_safe['identity']['operation_type']=='EMBEDDING')
        warmup=next(r for r in rows if r.metadata_safe['identity']['workload']=='SYSTEM_WARMUP')
        assert embedding.input_tokens==7 and embedding.output_tokens is None
        assert warmup.metadata_safe['run_id'] is None
        assert 'PRIVATE_EMBEDDING' not in json.dumps(embedding.metadata_safe)

@pytest.mark.asyncio
async def test_sdk_retries_remain_separate_accounting_attempts(accounting):
    db,repo,ledger=accounting;install(ledger);calls=0
    def handler(req):
        nonlocal calls
        calls+=1
        return httpx.Response(503,json={'error':{'code':503,'message':'PRIVATE_ERROR'}}) if calls==1 else httpx.Response(200,json=payload(200))
    async with fake_model(handler,retry_options=types.HttpRetryOptions(attempts=2,initial_delay=.001,max_delay=.001)) as (base,_):
        result=[x async for x in instrument_model(base,'team_manager','orchestration').generate_content_async(request())]
    assert result and calls==2
    async with db.sessions() as s:
        rows=(await s.execute(select(LedgerRow))).scalars().all()
        assert len(rows)==2 and len({r.attempt_id for r in rows})==2
        assert sorted((r.input_tokens for r in rows),key=lambda x:x is None)==[200,None]

@pytest.mark.asyncio
async def test_unsupported_sdk_never_executes_when_accounting_enabled(accounting,monkeypatch):
    db,repo,ledger=accounting;install(ledger);calls=0
    monkeypatch.setattr('backend.observability.model_provider.compatible',lambda:False)
    def handler(req):
        nonlocal calls
        calls+=1;return httpx.Response(200,json=payload())
    async with fake_model(handler) as (base,_):
        with pytest.raises(AdmissionFailed):_=[x async for x in instrument_model(base,'team_manager','orchestration').generate_content_async(request())]
    assert calls==0

@pytest.mark.asyncio
async def test_capture_timeout_is_bounded_and_does_not_replay_provider(accounting,monkeypatch):
    db,repo,ledger=accounting;install(ledger);ledger.config=ledger.config.model_copy(update={'finops_write_seconds':.1})
    calls=0
    async def blocked(*args):await asyncio.Event().wait()
    monkeypatch.setattr(repo,'capture',blocked)
    def handler(req):
        nonlocal calls
        calls+=1;return httpx.Response(200,json=payload())
    async with fake_model(handler) as (base,_):
        async with asyncio.timeout(2):result=[x async for x in instrument_model(base,'team_manager','orchestration').generate_content_async(request())]
    assert result and calls==1 and (await repo.health('development'))['debt']==1

@pytest.mark.asyncio
async def test_cancellation_during_capture_retains_durable_obligation(accounting,monkeypatch):
    db,repo,ledger=accounting;install(ledger);entered=asyncio.Event();calls=0
    async def blocked(*args):entered.set();await asyncio.Event().wait()
    monkeypatch.setattr(repo,'capture',blocked)
    def handler(req):
        nonlocal calls
        calls+=1;return httpx.Response(200,json=payload())
    async with fake_model(handler) as (base,_):
        async def run():return [x async for x in instrument_model(base,'team_manager','orchestration').generate_content_async(request())]
        task=asyncio.create_task(run());await asyncio.wait_for(entered.wait(),2);task.cancel()
        with pytest.raises(asyncio.CancelledError):await task
    assert calls==1 and (await repo.health('development'))['debt']==1

@pytest.mark.asyncio
@pytest.mark.parametrize('agent,purpose,workload,expected',[
    ('km_image_interpreter','image_interpretation','ingestion','INGESTION'),
    ('technical_authority_engineer','structured_output_repair','background','BACKGROUND'),
    ('incident_manager','remediation',None,'BACKGROUND')])
async def test_nonturn_model_attribution_is_durable_without_quantity_estimates(accounting,agent,purpose,workload,expected):
    db,repo,ledger=accounting;install(ledger);calls=0
    def handler(req):
        nonlocal calls
        calls+=1
        body=payload();body.pop('usageMetadata',None)
        return httpx.Response(200,json=body)
    async with fake_model(handler) as (base,_):
        result=[x async for x in instrument_model(base,agent,purpose,workload=workload).generate_content_async(request())]
    assert result and calls==1
    async with db.sessions() as s:
        row=(await s.execute(select(LedgerRow))).scalar_one()
        i=row.metadata_safe['identity']
        assert i['agent']==agent and i['operation']==purpose and i['workload']==expected
        assert i['operation_type']=='GENERATION' and row.input_tokens is None
        assert row.metadata_safe['usage_availability']=='UNKNOWN'
