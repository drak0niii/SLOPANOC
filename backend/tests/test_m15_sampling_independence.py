"""Paired retention policies exercise actual SLI and M11–M14 calculations."""
from copy import deepcopy
from datetime import timedelta
from uuid import UUID
from backend.observability.sampling_policy import decide
from backend.observability.slo_rollups import Receipt,contributions
from backend.observability.slo_contract import RequestClass
from backend.observability.slo_sources import TraceAssessment
from backend.tests._m12_reconciliation import inputs as reconciliation_inputs,registry, NOW
from backend.tests._m13_unit_economics import economics_inputs
from backend.tests._m14_controls import inputs as control_inputs,budget,POLICY
from backend.observability.finops.reconciliation import reconcile
from backend.observability.finops.unit_economics import calculate as economics
from backend.observability.finops.control_repository import calculate as controls
from backend.observability.finops.budget_registry import Registry
import pytest

def population(probability):
    receipts=[];retained=0
    for n in range(100):
        retained+=decide(format(n+1,'032x'),'HEALTHY',probability).cloud_trace
        receipt=Receipt(run_id=str(UUID(int=n+1)),environment='development',subject_digest='a'*64,
            accepted_at=NOW,deadline_at=NOW+timedelta(seconds=180),terminal_at=NOW+timedelta(seconds=1),
            terminal_status='COMPLETED',terminal_count=1,valid_outcome=True,
            request_class=RequestClass.GENERAL,duration_ms=1000,trace_complete=True,
            sse_expected=True,emitted_at=NOW+timedelta(seconds=1),browser_received=True,transport='relay_succeeded')
        receipts.append(contributions(receipt,NOW+timedelta(seconds=2)))
    return receipts,retained

def test_slo_and_instrumentation_completeness_are_unsampled():
    full,full_retained=population(1)
    sampled,sampled_retained=population(.1)
    assert full==sampled and full_retained==100 and 0<sampled_retained<25
    assert all(row['trace_completeness']['good']==1 for row in sampled)
    assert all(row['availability']['good']==1 and row['terminal_completion']['good']==1 and row['sse_delivery']['good']==1 for row in sampled)

def test_actual_provider_ttft_source_is_independent_of_retention():
    from types import SimpleNamespace
    from backend.observability.slo_sources import model_observation
    sources=[];retained=[]
    for probability in (1,.1):
        events=[];writer=SimpleNamespace(publish=events.append,lost=False)
        runtime=SimpleNamespace(sre=writer)
        kept=0
        for n,ttft in enumerate((1000,None,6000)*30):
            kept+=decide(format(n+1,'032x'),'HEALTHY',probability).cloud_trace
            observation=SimpleNamespace(status=SimpleNamespace(value='COMPLETED'),completed_at=NOW,
                streaming=True,workload='user_turn',ttft_ms=ttft,
                ttft_boundary='first_provider_output' if ttft is not None else None,trace_id='a',span_id='b')
            model_observation(runtime,observation)
        sources.append(events);retained.append(kept)
    assert sources[0]==sources[1] and retained[0]==90 and retained[1]<25
    ttft=[value for key,at,value in sources[1] if key=='model_ttft']
    assert sum(v.good for v in ttft)==30 and sum(v.bad for v in ttft)==30 and sum(v.unknown for v in ttft)==30

def financial_outputs(probability):
    # Policy is used only for trace retention; the same unsampled input history
    # feeds real primary billing normalization, reconciliation, economics/control.
    retention=[decide(format(n+1,'032x'),'HEALTHY',probability) for n in range(100)]
    runtime,billing,pricing=reconciliation_inputs()
    recon=reconcile(runtime,billing,pricing,registry())
    units=economics(**economics_inputs())
    data=control_inputs(prior=56)
    definitions=Registry(budgets=(budget('4000'),))
    results={kind:controls(data,definitions,POLICY,kind) for kind in ('BUDGET','FORECAST','ANOMALY')}
    return {'runtime':runtime,'billing':billing,'pricing':pricing,'reconciliation':recon,'unit_economics':units,
        'controls':results},sum(d.cloud_trace for d in retention)

def test_m11_to_m14_exact_outputs_do_not_read_trace_retention():
    full,kept_full=financial_outputs(1)
    sampled,kept_sampled=financial_outputs(.1)
    assert full==sampled and kept_full==100 and kept_sampled<25

@pytest.mark.asyncio
async def test_durable_m10_attempts_quantities_and_completeness_are_identical(tmp_path):
    from sqlalchemy import select
    from backend.observability.database import Database
    from backend.observability.finops.models import FinOpsBase,AttemptRow,LedgerRow
    from backend.observability.finops.repository import Repository
    from backend.observability.finops.contracts import Identity,Payload
    from backend.observability.model_usage import ModelUsage
    from backend.tests._m12_reconciliation import JAN
    results=[]
    for probability in (1,.1):
        db=Database('sqlite+aiosqlite:///'+str(tmp_path/str(probability)),isolated_test=True)
        try:
            async with db.engine.begin() as conn:await conn.run_sync(FinOpsBase.metadata.create_all)
            repo=Repository(db.sessions);provider_calls=0;retained=0
            for n in range(30):
                identity=Identity(environment='development',attempt_id=UUID(int=n+1),logical_call_id=UUID(int=n+101),
                    provider_request_id=UUID(int=n+201),attempt=1,provider='gcp.gemini',model='gemini-2.5-flash',
                    agent='team_manager',operation='orchestration',operation_type='GENERATION',workload='USER_TURN',
                    started_at=JAN+timedelta(hours=1),deadline_at=NOW)
                await repo.admit(identity,JAN-timedelta(days=1));await repo.start(identity,identity.started_at)
                provider_calls+=1 # local provider double: one physical invocation
                await repo.capture(Payload(identity=identity,observed_at=NOW,provider_finished_at=NOW,
                    usage=ModelUsage(input_tokens=100,output_tokens=2,cached_tokens=0,total_tokens=102,
                        availability='KNOWN',source='provider'),status='COMPLETED',config_version='a'*16))
                await repo.materialize(identity.event_id)
                retained+=decide(format(n+1,'032x'),'HEALTHY',probability).cloud_trace
            async with db.sessions() as session:
                attempts=(await session.execute(select(AttemptRow.attempt_id,AttemptRow.state).order_by(AttemptRow.attempt_id))).all()
                ledger=(await session.execute(select(LedgerRow.event_id,LedgerRow.payload_hash,LedgerRow.input_tokens,
                    LedgerRow.output_tokens,LedgerRow.cached_tokens).order_by(LedgerRow.event_id))).all()
                results.append((provider_calls,attempts,ledger,retained))
        finally:await db.close()
    assert results[0][:3]==results[1][:3]
    assert len(results[0][2])==30 and results[0][0]==30 and results[0][3]>results[1][3]
