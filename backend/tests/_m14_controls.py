"""Synthetic isolated financial history; no shared resources or source queries."""
from datetime import datetime,date,timedelta,timezone
from types import SimpleNamespace
import pytest_asyncio
from sqlalchemy import select
from backend.observability.database import Database
from backend.observability.finops.control_contracts import Inputs,Day,Policy
from backend.observability.finops.budget_registry import Budget,Registry
from backend.observability.finops.billing_contracts import digest
from backend.observability.finops.control_inputs import Adapter
from backend.observability.finops.control_repository import Repository
from backend.observability.finops.control_models import ControlBase
from backend.observability.finops.reconciliation_models import ReconciliationBase,GenerationRow,RegistryRow,InputRow,StateRow
from backend.observability.finops.billing_models import BillingBase,PublicationRow,StateRow as BillingState
from backend.observability.finops.unit_economics_models import UnitEconomicsBase
from backend.observability.finops.reconciliation_mapping import Registry as FinancialRegistry
BINDING=digest('approved-synthetic-scope')
ISSUE=datetime(2026,10,10,tzinfo=timezone.utc)
POLICY=Policy()

def budget(amount='100',**changes):
    return Budget(**dict(dict(budget_id='budget-total',version='b1',environment='development',financial_binding=BINDING,amount=amount,
        currency='EUR',effective_from=date(2026,10,1),effective_to=date(2026,11,1),recorded_at=datetime(2026,1,1,tzinfo=timezone.utc),provenance_digest=digest('review')),**changes))

def day(at,value='100',state='ELIGIBLE_COMPLETE',**changes):
    return Day(**dict(dict(day=at,generation=digest([at.isoformat(),value]),published_at=datetime.combine(at+timedelta(days=1),datetime.min.time(),timezone.utc),
        state=state,values={'TOTAL:EUR':value},coverage='1',mapping_version='mapping-v1'),**changes))

def inputs(n=7,value='100',issue=ISSUE,prior=0,**changes):
    start=date(issue.year,issue.month,1)
    days=[day(start-timedelta(days=i),value) for i in range(prior,0,-1)]+[day(start+timedelta(days=i),value) for i in range(n)]
    return Inputs(**dict(dict(environment='development',binding=BINDING,issue=issue,cutoff=issue,month=issue.strftime('%Y-%m'),currency='EUR',days=tuple(days)),**changes))

@pytest_asyncio.fixture
async def controls(tmp_path):
    db=Database('sqlite+aiosqlite:///'+str(tmp_path/'m14.db'),isolated_test=True)
    async with db.engine.begin() as c:
        for metadata in (BillingBase.metadata,ReconciliationBase.metadata,UnitEconomicsBase.metadata,ControlBase.metadata):await c.run_sync(metadata.create_all)
    yield SimpleNamespace(db=db,adapter=Adapter(db.sessions),repo=Repository(db.sessions))
    await db.close()

async def financial(system,at,value='100',known=None,coverage='1',status='RECONCILED',correction=0):
    known=known or datetime.combine(at+timedelta(days=1),datetime.min.time(),timezone.utc)
    period=at.isoformat();sk=digest(['m11',period]);gid=digest(['m12',period,value,known.isoformat()]);sg=digest(['m11',gid]);rid=FinancialRegistry(environment='development').identity
    reg=FinancialRegistry(environment='development')
    b=dict(kind='BILLING',environment='development',period=period,scope=BINDING,buckets=[],components=[])
    result=dict(period=period,status=status,quality={'billing':'FRESH'},coverage={'allocation':coverage},controls={k:'PASS' for k in ('signed_net','components','absolute','count')},
        versions={'mapping':'mapping-v1'},amounts=[dict(currency='EUR',net=value,ratios={'unallocated':'0'})],classified=[],quantities=[])
    ids=[digest([kind,gid]) for kind in ('RUNTIME','BILLING','PRICING')]
    async with system.db.sessions() as s,s.begin():
        if not await s.get(RegistryRow,rid):s.add(RegistryRow(identity=rid,environment='development',version='mapping-v1',ownership_version='owner-v1',body=reg.model_dump(mode='json')))
        s.add(PublicationRow(generation=sg,state_key=sk,fingerprint=sg,schema_version=1,mapping_version='source-v1',window_start=datetime.combine(at,datetime.min.time(),timezone.utc),window_end=datetime.combine(at+timedelta(days=1),datetime.min.time(),timezone.utc),
            extracted_at=known,export_time=known,row_count=1,late_rows=0,correction_rows=correction,unmapped_rows=0,evidence_rows=0,controls=[],published_at=known,status='PUBLISHED'))
        await s.flush()
        st=await s.get(BillingState,sk)
        if st:st.generation=sg;st.last_success=known
        else:s.add(BillingState(key=sk,environment='development',source='DETAILED_BILLING',scope=BINDING,partition=period,mode='TEST_FIXTURE',revision=1,generation=sg,last_success=known))
        for identity,kind in zip(ids,('RUNTIME','BILLING','PRICING')):s.add(InputRow(identity=identity,environment='development',scope=BINDING,period=period,kind=kind,source_generation=sg if kind=='BILLING' else None,body=b if kind=='BILLING' else {}))
        await s.flush()
        s.add(GenerationRow(identity=gid,registry=rid,runtime_input=ids[0],billing_input=ids[1],pricing_input=ids[2],body=dict(result=result,source_vector={sk:sg}),published_at=known));await s.flush()
        key=digest(['development',BINDING,period]);state=await s.get(StateRow,key)
        if state:state.generation=gid;state.last_success=known
        else:s.add(StateRow(key=key,environment='development',scope=BINDING,period=period,revision=1,generation=gid,last_success=known))
    return gid
