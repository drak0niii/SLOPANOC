"""Paired local turns with identical durable accounting in both modes.

The synthetic CPU baseline is too small to certify typical production CPU
percentage. Report it honestly; do not hide accounting to improve the baseline.
"""
import asyncio
import json
import statistics
import time
import httpx
import pytest
from sqlalchemy import select,func
from backend.tests.test_finops_runtime_ledger import accounting
from backend.tests.test_observability_model_provider import fake_model,payload,request
from backend.tests._api_fakes import FakeRunner
from backend.api.chat_service import ChatService
from backend.api.session_service import ApiSessionService
from backend.observability.runtime import Runtime
from backend.observability.config import ObservabilityConfig
from backend.observability.model_adapter import instrument_model
from backend.observability.finops.usage_ledger import install
from backend.observability.finops.models import LedgerRow

@pytest.mark.asyncio
async def test_matched_accounting_enabled_cpu_and_root_latency(accounting):
    db,repo,ledger=accounting;install(ledger);physical=0
    def handler(req):
        nonlocal physical
        physical+=1
        return httpx.Response(200,json=payload())
    measurements={False:[],True:[]};cpus={False:[],True:[]};runtimes=[]
    async with fake_model(handler) as (base,_):
        model=instrument_model(base,'team_manager','orchestration')
        async def execute(*args):
            _=[v async for v in model.generate_content_async(request())]
            await asyncio.sleep(.005)
        sessions=ApiSessionService();services={}
        for enabled in (False,True):
            runtime=Runtime(ObservabilityConfig(observability_enabled=True,otel_enabled=enabled,
                        otel_exporter_mode='local',otel_batch_interval_seconds=.01))
            runtimes.append(runtime)
            services[enabled]=ChatService(sessions,runner=FakeRunner(sessions,side_effect=execute),observability_runtime=runtime)
        try:
            for n in range(30):
                for enabled in ((False,True) if n%2 else (True,False)):
                    sid=await sessions.create_session();start=time.monotonic();cpu=time.process_time()
                    _=[v async for v in services[enabled].execute_turn_events(sid,'synthetic local ordinary request')]
                    if n>=5:
                        measurements[enabled].append(time.monotonic()-start);cpus[enabled].append(time.process_time()-cpu)
            for runtime in runtimes:assert runtime.flush(2)
            async with db.sessions() as s:assert await s.scalar(select(func.count()).select_from(LedgerRow))==60
            assert physical==60 and (await repo.health('development'))['debt']==0
            baseline=statistics.median(cpus[False]);observed=statistics.median(cpus[True])
            report=dict(samples_per_mode=25,provider_calls=physical,durable_ledger_rows=60,
                        median_turn_ms={str(k):statistics.median(v)*1000 for k,v in measurements.items()},
                        median_process_cpu_ms={str(k):statistics.median(v)*1000 for k,v in cpus.items()},
                        synthetic_cpu_delta_percent=100*(observed-baseline)/baseline,
                        cpu_3_percent_verdict='INCONCLUSIVE_SYNTHETIC_BASELINE',
                        typical_production_cpu_certified=False,accounting_enabled_both_modes=True)
            print('M16_MATCHED_PERFORMANCE='+json.dumps(report,sort_keys=True))
        finally:
            for runtime in runtimes:runtime.close()
