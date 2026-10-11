"""CPU qualification on the existing production-shaped TAE governed workflow.

Same real ADK Runner/tools/governance/knowledge/ChatService inputs in both modes.
Scripted responses replace external provider content; every scripted model call
also traverses the installed GenAI fake transport and durable M10 accounting.
No synthetic CPU padding or provider sleeps are added to the workload.
"""
import json
import statistics
import time
import httpx
import pytest
from google.adk.agents import Agent
from google.adk.runners import Runner
from google.genai import types
from backend.tests.test_procedure_action_tae_e2e import _mop,_SEARCH,_SELECT,_CATALOG,_recommend,_USER,_ScriptedCallLlm,isolated_km_repo
from backend.tests.test_finops_runtime_ledger import accounting
from backend.tests.test_observability_model_provider import fake_model,payload,request
from backend.agents.technical_authority_engineer.agent_tool import TechnicalAuthorityAgentTool
from backend.agents.technical_authority_engineer.schemas import TechnicalAuthorityRequest,TechnicalAuthorityResponse
from backend.agents.technical_authority_engineer import procedure_actions as pa
from backend.agents.team_manager.agent import team_manager
from backend.agents.team_manager.source_requirements import record_source_requirements
from backend.tests.test_ess_service_unavailable_e2e_verification import _fc
from backend.tools.knowledge.tools import knowledge_search,knowledge_select_evidence
from backend.api.session_service import ApiSessionService,APP_NAME
from backend.api.chat_service import ChatService
from backend.observability.config import ObservabilityConfig
from backend.observability.runtime import Runtime
from backend.observability.model_adapter import instrument_model
from backend.observability.finops.usage_ledger import install
from backend.tests._m7_storage import close_chat_storage
from backend.observability.finops.models import LedgerRow
from sqlalchemy import select,func

@pytest.mark.asyncio
async def test_representative_governed_turn_cpu(accounting,isolated_km_repo,monkeypatch):
    await isolated_km_repo.add(_mop());db,repo,ledger=accounting;install(ledger)
    samples={False:[],True:[]};wall={False:[],True:[]};physical=0
    import os,cProfile
    from collections import Counter
    from contextvars import ContextVar
    from google.adk.agents.base_agent import BaseAgent
    from google.adk.tools.function_tool import FunctionTool
    from backend.observability.finops.usage_ledger import Ledger
    from backend.observability.finops.models import AttemptRow
    mode=ContextVar('m16_cpu_mode',default=False)
    counts={False:Counter(),True:Counter()};lifecycle={False:[],True:[]}
    original_agent=BaseAgent.run_async;original_tool=FunctionTool.run_async;original_admission=Ledger.admission
    original_specialist_tool=TechnicalAuthorityAgentTool.run_async
    async def counted_agent(self,*args,**kwargs):
        counts[mode.get()]['agent_operations']+=1
        async for value in original_agent(self,*args,**kwargs):yield value
    async def counted_tool(self,*args,**kwargs):
        counts[mode.get()]['tool_calls']+=1
        return await original_tool(self,*args,**kwargs)
    async def counted_specialist_tool(self,*args,**kwargs):
        counts[mode.get()]['tool_calls']+=1
        return await original_specialist_tool(self,*args,**kwargs)
    async def counted_admission(self,*args,**kwargs):
        result=await original_admission(self,*args,**kwargs)
        counts[mode.get()]['accounting_admissions']+=1
        return result
    monkeypatch.setattr(BaseAgent,'run_async',counted_agent)
    monkeypatch.setattr(FunctionTool,'run_async',counted_tool)
    monkeypatch.setattr(TechnicalAuthorityAgentTool,'run_async',counted_specialist_tool)
    monkeypatch.setattr(Ledger,'admission',counted_admission)
    diagnosis=os.environ.get('SLOPANOC_M16_CPU_LAYER')
    detailed=os.environ.get('SLOPANOC_M16_CPU_DIAGNOSTIC')=='1'
    profiles={key:cProfile.Profile(timer=time.thread_time) for key in (False,True)}
    export_profiles={key:cProfile.Profile(timer=time.thread_time) for key in ('trace','log')}
    initialization_cpu=time.process_time()
    def handler(req):
        nonlocal physical
        physical+=1;counts[mode.get()]['provider_calls']+=1;return httpx.Response(200,json=payload())
    runtimes={enabled:Runtime(ObservabilityConfig(observability_enabled=True,otel_enabled=enabled,
        otel_exporter_mode='none')) for enabled in (False,True)}
    # Real processors/export queues are required; non-retaining transport stand-ins
    # prevent developer capture buffers from changing workload/GC over repetitions.
    from opentelemetry.sdk.trace.export import SpanExportResult
    from opentelemetry.sdk._logs.export import LogRecordExportResult
    from opentelemetry.sdk.metrics.export import MetricExporter,MetricExportResult
    class CounterSink:
        def __init__(self,signal):self.signal=signal
        def export(self,records):
            # Match production encoding, without the test-only repr/sentinel scan
            # that distorted earlier process-CPU measurements. Privacy corpus is
            # tested separately, not serialized again by the benchmark sink.
            if detailed:export_profiles[self.signal].enable()
            if self.signal=='trace':
                from opentelemetry.exporter.otlp.proto.common.trace_encoder import encode_spans
                encode_spans(records).SerializeToString()
            else:
                from opentelemetry.exporter.otlp.proto.common._log_encoder import encode_logs
                encode_logs(records).SerializeToString()
            if detailed:export_profiles[self.signal].disable()
            return SpanExportResult.SUCCESS if self.signal=='trace' else LogRecordExportResult.SUCCESS
        def shutdown(self):pass
    class CounterMetrics(MetricExporter):
        def export(self,*args,**kwargs):return MetricExportResult.SUCCESS
        def force_flush(self,*args,**kwargs):return True
        def shutdown(self,*args,**kwargs):pass
    runtimes[True].close()
    runtimes[True]=Runtime(ObservabilityConfig(observability_enabled=True,otel_enabled=True,otel_exporter_mode='local'),
        exporters={'trace':CounterSink('trace'),'log':CounterSink('log'),'metric':CounterMetrics()})
    initialization_ms=(time.process_time()-initialization_cpu)*1000
    try:
        provider_initialization_start=time.process_time()
        async with fake_model(handler) as (base,_):
            provider=instrument_model(base,'team_manager','orchestration')
            provider_initialization_ms=(time.process_time()-provider_initialization_start)*1000
            class AccountedScript(_ScriptedCallLlm):
                async def generate_content_async(self,*args,**kwargs):
                    counts[mode.get()]['model_operations']+=1
                    actual_request = kwargs.get('llm_request', args[0] if args else None)
                    assert actual_request is not None
                    actual_request = actual_request.model_copy(update={'model':'gemini-2.5-flash'})
                    _=[v async for v in provider.generate_content_async(actual_request)]
                    async for value in super().generate_content_async(*args,**kwargs):yield value
            blocks={False:[],True:[]}
            # Prepare fixture objects outside CPU measurement. Execute complete
            # mode blocks so enabled exporter work cannot pollute the baseline.
            # Reverse block order on the second repetition; drain warm batches
            # before measuring and settle measured exports before stopping CPU.
            for repetition in range(2):
                for enabled in ((True,False) if repetition==0 else (False,True)):
                    token=mode.set(enabled)
                    gate=None
                    if diagnosis and enabled:
                        from backend.tests._m16_cpu_diagnostic import layers
                        gate=layers(runtimes[enabled],diagnosis);gate.__enter__()
                    prepared=[]
                    for _ in range(40):
                        sessions=ApiSessionService();sid=await sessions.create_session('eng')
                        specialist=Agent(name='technical_authority_engineer',model=AccountedScript(model='scripted-tae',parts_by_call=[_SEARCH,_SELECT,_CATALOG,_recommend()]),
                            tools=[knowledge_search,knowledge_select_evidence,pa.procedure_action_catalog],input_schema=TechnicalAuthorityRequest,output_schema=TechnicalAuthorityResponse)
                        model=AccountedScript(model='scripted-tm',parts_by_call=[
                            _fc('record_source_requirements',{'requires_teams':False,'requires_governed_knowledge':True}),
                            _fc('technical_authority_engineer',{'problem_statement':_USER}),
                            [types.Part.from_text(text='Check the node alarm state. Run show foo NODE-1 and report the alarm state output.')]])
                        agent=team_manager.model_copy(update={'model':model,'tools':[record_source_requirements,TechnicalAuthorityAgentTool(agent=specialist)]})
                        runner=Runner(app_name=APP_NAME,agent=agent,session_service=sessions.adk_session_service)
                        chat=ChatService(sessions,runner=runner,observability_runtime=runtimes[enabled])
                        prepared.append((sessions,sid,chat))
                    block_start=None
                    before_counts=counts[enabled].copy()
                    async with db.sessions() as session:
                        before_rows=await session.scalar(select(func.count()).select_from(LedgerRow))
                    try:
                        for n,(sessions,sid,chat) in enumerate(prepared):
                            if n==10:block_start=time.process_time()
                            start=time.perf_counter();cpu=time.process_time()
                            profiling=detailed and n>=10
                            if profiling:profiles[enabled].enable()
                            counts[enabled]['accepted_turns']+=1
                            events=[v async for v in chat.execute_turn_events(sid,_USER,'eng')]
                            if profiling:profiles[enabled].disable()
                            used=time.process_time()-cpu;elapsed=time.perf_counter()-start
                            finals=[e.data['content'] for e in events if e.type.value=='message.completed']
                            assert finals and 'show foo NODE-1' in finals[-1]
                            counts[enabled]['terminal_outcomes']+=1
                            if n==0:first_turn_ms=used*1000
                            if n>=10:
                                samples[enabled].append(used);wall[enabled].append(elapsed)
                            if n==9:assert runtimes[enabled].flush(2)
                        settlement_start=time.process_time()
                        assert runtimes[enabled].flush(2)
                        settlement_ms=(time.process_time()-settlement_start)*1000
                        blocks[enabled].append((time.process_time()-block_start)/30)
                        assert (await repo.health('development'))['debt']==0
                        async with db.sessions() as session:
                            after_rows=await session.scalar(select(func.count()).select_from(LedgerRow))
                        counts[enabled]['ledger_rows']+=after_rows-before_rows
                        lifecycle[enabled].append(dict(first_turn_ms=first_turn_ms,settlement_ms=settlement_ms,
                            warm_turns=10,measured_turns=30,counts=dict(counts[enabled]-before_counts)))
                    finally:
                        for sessions,sid,chat in prepared:await close_chat_storage(chat)
                        if gate:gate.__exit__(None,None,None)
                        mode.reset(token)
        for runtime in runtimes.values():assert runtime.flush(2)
        async with db.sessions() as session:rows=await session.scalar(select(func.count()).select_from(LedgerRow))
        assert physical==rows==1120 and (await repo.health('development'))['debt']==0
        assert counts[False]==counts[True]
        assert counts[False]['accepted_turns']==counts[False]['terminal_outcomes']==80
        assert counts[False]['agent_operations']==160 and counts[False]['tool_calls']==400
        assert counts[False]['provider_calls']==counts[False]['model_operations']==counts[False]['accounting_admissions']==counts[False]['ledger_rows']==560
        print('M16_CPU_EQUIVALENCE='+json.dumps(dict(counts={str(k):dict(v) for k,v in counts.items()},initialization_ms=initialization_ms,provider_initialization_ms=provider_initialization_ms,lifecycle={str(k):v for k,v in lifecycle.items()}),sort_keys=True))
        if detailed:
            for key,profile in profiles.items():profile.dump_stats('/private/tmp/m16-cpu-main-'+str(key)+'.prof')
            for key,profile in export_profiles.items():profile.dump_stats('/private/tmp/m16-cpu-export-'+key+'.prof')
        baseline=statistics.mean(blocks[False]);observed=statistics.mean(blocks[True]);delta=100*(observed-baseline)/baseline
        print('M16_GOVERNED_CPU='+json.dumps(dict(samples_per_mode={str(k):len(v) for k,v in samples.items()},warmup_turns_per_mode_per_block=10,settled_blocks_per_mode=2,
            metric='mean process CPU per governed turn in reversed settled mode blocks, normal batching and same durable accounting',
            mode_blocks_ms={str(k):[v*1000 for v in values] for k,values in blocks.items()},
            turn_p50_ms={str(k):statistics.median(v)*1000 for k,v in samples.items()},
            turn_p95_ms={str(k):sorted(v)[int(.95*(len(v)-1))]*1000 for k,v in samples.items()},turn_p99_ms=None,
            environment=__import__('platform').platform(),python=__import__('platform').python_version(),
            baseline_ms=baseline*1000,enabled_ms=observed*1000,increment_ms=(observed-baseline)*1000,
            overhead_percent=delta,threshold_percent=3,physical_calls=physical,ledger_rows=rows,
            synthetic_cpu_padding=False,production_capacity_certified=False),sort_keys=True))
        if diagnosis or detailed:
            print('M16_CPU_DIAGNOSTIC_ONLY='+str(diagnosis or 'profile'))
        else:
            assert delta<3
    finally:
        shutdown_start=time.process_time()
        for runtime in runtimes.values():runtime.close()
        print('M16_CPU_SHUTDOWN='+json.dumps(dict(cpu_ms=(time.process_time()-shutdown_start)*1000,excluded_from_steady_state=True)))
