from copy import deepcopy
from datetime import timedelta
from uuid import uuid4
import pytest
from sqlalchemy import select
from backend.tests._m12_reconciliation import *
from backend.observability.finops.models import LedgerRow,AttemptRow
from backend.observability.finops.billing_contracts import SourceError
from backend.observability.finops.reconciliation_contracts import Policy

@pytest.mark.asyncio
async def test_runtime_correction_joins_original_period_and_never_changes_base(system):
    i=await capture(system.runtime)
    provider=RuntimeProvider(system.db.sessions)
    before=await provider.snapshot('development',WINDOW)
    await system.runtime.adjust(Adjustment(correction_id=uuid4(),original_event_id=i.event_id,environment='development',
        reason='QUANTITY_CORRECTION',process='accounting_recovery',at=JAN+timedelta(days=10),quantities={'input_tokens':50}))
    after=await provider.snapshot('development',WINDOW)
    assert after['buckets'][0]['quantities']['input_tokens']['observed']=='1050'
    assert before['ledger_digest']!=after['ledger_digest']
    async with system.db.sessions() as s:assert (await s.get(LedgerRow,i.event_id)).input_tokens==1000
    assert after==await provider.snapshot('development',WINDOW)

@pytest.mark.asyncio
async def test_durable_debt_remains_unknown_and_limits(system):
    i=await capture(system.runtime,None)
    other=i.model_copy(update={'attempt_id':uuid4(),'logical_call_id':uuid4(),'provider_request_id':uuid4()})
    await system.runtime.admit(other);await system.runtime.start(other,other.started_at)
    value=await RuntimeProvider(system.db.sessions).snapshot('development',WINDOW)
    assert value['quality']['debt']==1 and value['quality']['completeness']=='0.5'
    assert len(value['buckets'])==2
    assert sum(b['quantities']['input_tokens']['unknown'] for b in value['buckets'])==2
    assert all(b['quantities']['input_tokens']['observed'] is None for b in value['buckets'])
    with pytest.raises(SourceError):await RuntimeProvider(system.db.sessions,Policy(max_rows=1)).snapshot('development',WINDOW)


def test_same_extraction_manifest_attestation_and_bucket_bounds():
    r,b,p=inputs()
    ex=extraction([charge()]);projection=normalize(ex,source_mapping())
    manifest=SimpleNamespace(fingerprint='bad',row_count=projection.rows,controls=list(projection.controls))
    with pytest.raises(SourceError):billing_input(ex,manifest,source_mapping(),SCOPE,'development')
    ex=extraction([charge('1',credits=[dict(amount='-1',id='c',type='DISCOUNT')])]);projection=normalize(ex,source_mapping())
    manifest=SimpleNamespace(fingerprint=projection.fingerprint,row_count=projection.rows,controls=list(projection.controls),generation='a'*64,export_time=projection.export_time)
    with pytest.raises(SourceError):billing_input(ex,manifest,source_mapping(),SCOPE,'development',Policy(max_buckets=1))

@pytest.mark.asyncio
async def test_billing_aggregate_restricted_identifiers_not_copied(system):
    await system.ingestion.ingest('DETAILED_BILLING',WINDOW);await system.worker.drain()
    gid=(await system.source.current('development','DETAILED_BILLING',SCOPE))[0][1].generation
    row=await system.repo.billing('development',SCOPE,WINDOW.partition,gid)
    text=str(row.body)
    for sentinel in ('ACCOUNT_SENTINEL','PROJECT_SENTINEL','RESOURCE_SENTINEL','SECRET_SENTINEL','TAG_SENTINEL','sku-input'):
        assert sentinel not in text
    assert len(row.body['buckets'])==1

@pytest.mark.asyncio
async def test_snapshot_remains_frozen_during_late_correction(system,monkeypatch):
    from sqlalchemy.ext.asyncio import AsyncSession
    from sqlalchemy import text
    from backend.observability.finops.models import SourceRow
    i=await capture(system.runtime)
    async with system.db.engine.connect() as c:await c.execute(text('PRAGMA journal_mode=WAL'))
    original=AsyncSession.get;fired=False
    async def intercept(session,entity,*args,**kwargs):
        nonlocal fired
        if entity is SourceRow and not fired:
            fired=True
            await system.runtime.adjust(Adjustment(correction_id=uuid4(),original_event_id=i.event_id,environment='development',
                reason='QUANTITY_CORRECTION',process='accounting_recovery',at=JAN+timedelta(days=5),quantities={'input_tokens':50}))
        return await original(session,entity,*args,**kwargs)
    monkeypatch.setattr(AsyncSession,'get',intercept)
    provider=RuntimeProvider(system.db.sessions)
    frozen=await provider.snapshot('development',WINDOW)
    changed=await provider.snapshot('development',WINDOW)
    assert frozen['buckets'][0]['quantities']['input_tokens']['observed']=='1000'
    assert changed['buckets'][0]['quantities']['input_tokens']['observed']=='1050'
    assert frozen['ledger_digest']!=changed['ledger_digest']
