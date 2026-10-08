"""Local paired observer microbenchmark; fake waits/export flush outside timers."""
import asyncio
import json
import statistics
import time
from contextlib import nullcontext
from pathlib import Path
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import create_async_engine
from backend.observability.config import ObservabilityConfig
from backend.observability.runtime import Runtime
from backend.observability.turn_trace import TurnTrace
from backend.observability.dependency_instrumentation import dependency_scope
from backend.observability.dependency_database import pool_kwargs, observe_engine
from backend.observability.knowledge_instrumentation import knowledge_stage
from backend.knowledge.retrieval.service import KnowledgeRetrievalService
from backend.tests.test_observability_dependency_knowledge import Repo, query


def summary(values):
    ordered=sorted(values)
    return {'median_ms':statistics.median(values),'p95_ms':ordered[int(.95*(len(ordered)-1))], 'p99_ms':ordered[int(.99*(len(ordered)-1))]}

@pytest.mark.asyncio
async def test_local_dependency_overhead(tmp_path):
    modes=['baseline','disabled','none','local'];runtimes={};turns={};engines={}
    for mode in modes:
        c=ObservabilityConfig(observability_enabled=mode in {'none','local'},otel_enabled=mode in {'none','local'},otel_exporter_mode='local' if mode=='local' else 'none')
        r=Runtime(c);runtimes[mode]=r;turns[mode]=TurnTrace('perf-'+mode,'local',r)
        kwargs={} if mode=='baseline' else pool_kwargs('sqlite+aiosqlite:///:memory:',{},'case_db')
        engine=create_async_engine('sqlite+aiosqlite:///:memory:',**kwargs);engines[mode]=engine
        if mode!='baseline':observe_engine(engine,'case_db')
    waits={}
    async def scenario(name,mode):
        raw=mode=='baseline'
        if name=='database':
            async with engines[mode].connect() as conn:await conn.execute(select(1))
        elif name=='knowledge':
            service=KnowledgeRetrievalService(Repo(),observer=None if raw else knowledge_stage)
            await service.retrieve(query())
        else:
            with nullcontext() if raw else dependency_scope('power_automate_gateway' if name=='http' else 'chat_attachments',
                    'teams.getMessages' if name=='http' else 'upload','http.client' if name=='http' else 'storage.client'):
                start=time.perf_counter()
                await asyncio.sleep(.001)
                waits[(name,mode)]=time.perf_counter()-start
    results={}
    try:
        for name in ['http','database','storage','knowledge']:
            samples={m:[] for m in modes};cpu={m:[] for m in modes}
            for i in range(110):
                for mode in modes[i%4:]+modes[:i%4]:
                    with turns[mode].attached():
                        wall=time.perf_counter();process=time.process_time()
                        await scenario(name,mode)
                        elapsed=time.perf_counter()-wall
                        used=time.process_time()-process
                    if i>=10:
                        samples[mode].append((elapsed-waits.get((name,mode),0))*1000)
                        cpu[mode].append(used*1000)
                assert all(not t.snapshot().dependencies for t in turns.values())
            results[name]={m:summary(samples[m])|{'cpu_median_ms':statistics.median(cpu[m])} for m in modes}
            paired=[a-b for a,b in zip(samples['local'],samples['baseline'])]
            results[name]['paired_local_minus_baseline']=summary(paired)
            assert results[name]['paired_local_minus_baseline']['p95_ms']<10
        for r in runtimes.values():assert r.flush()
        import tracemalloc
        tracemalloc.start()
        before=tracemalloc.get_traced_memory()[0]
        with turns['none'].attached():
            for _ in range(1000):
                with dependency_scope('chat_attachments','exists','storage.client'):pass
        retained,peak=tracemalloc.get_traced_memory()
        tracemalloc.stop()
        assert turns['none'].snapshot().dependencies==()
        results['memory']={'retained_delta_bytes':retained-before,'peak_delta_bytes':peak-before}
        results['queues']={m:r.health.snapshot()['trace']['queue_size'] for m,r in runtimes.items()}
        assert all(v==0 for v in results['queues'].values())
        Path('/private/tmp/slopanoc-m5-overhead.json').write_text(json.dumps(results,indent=2))
        print(json.dumps(results,sort_keys=True))
    finally:
        for t in turns.values():t.finish()
        for e in engines.values():await e.dispose()
        for r in runtimes.values():r.close()
