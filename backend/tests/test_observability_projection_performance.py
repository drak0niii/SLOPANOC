"""Local synthetic measurements; no production performance claim."""
import time
import statistics
from types import SimpleNamespace
from uuid import uuid4
import pytest
from sqlalchemy import text
from backend.tests._m7_storage import storage,state,change,config
from backend.observability.persistence import Coordinator
from backend.observability.turn_trace import TurnTrace
from backend.observability.stages import Stage,RunStatus
from backend.observability.authorization import Principal
from backend.observability.schemas import Role,ROLE_PERMISSIONS
from backend.observability.service import Service


def measured(call,count=1000):
    batches=[]
    for _ in range(5):
        before=time.perf_counter_ns()
        for i in range(count):call(i)
        batches.append((time.perf_counter_ns()-before)/count/1000)
    return statistics.median(batches)


def test_local_publication_coalescing_measurement():
    c=Coordinator(None,config())
    turn=TurnTrace(str(uuid4()),'session',SimpleNamespace(enabled=False,projection=c))
    baseline=measured(lambda i:None)
    projection=measured(lambda i:turn.event(Stage.PLANNING_STARTED if i%2 else Stage.PLANNING_COMPLETED))
    progress=measured(lambda i:turn.progress())
    assert len(c.slots)==1 and len(turn.publisher.events)<=128
    assert projection<10000 # doc 01 synchronous overhead target, local synthetic only.
    print(f'M7 LOCAL hot path median us: noop={baseline:.3f}; publish/coalesce={projection:.3f}; material_progress={progress:.3f}; samples=5x1000')


@pytest.mark.asyncio
async def test_local_indexed_database_and_api_measurement(tmp_path):
    db,repo=await storage(tmp_path/'bench.sqlite');cfg=config();backend=Service(repo,cfg)
    principal=Principal('trusted',Role.DEVELOPER_SRE,ROLE_PERMISSIONS[Role.DEVELOPER_SRE],frozenset({'development'}),True)
    durations=[];rows=[]
    try:
        for _ in range(200):
            s=state();before=time.perf_counter_ns();await repo.persist(s);durations.append((time.perf_counter_ns()-before)/1e6);rows.append(s)
        timings={}
        for name,operation in {
            'repository_lookup':lambda:repo.get(rows[0].run_id,('development',)),
            'active_list_50':lambda:backend.listing(principal,active=True),
            'timeline':lambda:backend.timeline(rows[0].run_id,principal),
            'api_service_lookup':lambda:backend.run(rows[0].run_id,principal),
        }.items():
            values=[]
            for _ in range(20):
                before=time.perf_counter_ns();await operation();values.append((time.perf_counter_ns()-before)/1e6)
            timings[name]=round(statistics.median(values),3)
        terminal=change(rows[0],state_version=2,status=RunStatus.COMPLETED,current_stage=Stage.TURN_COMPLETED,terminal_at=rows[0].observed_at)
        before=time.perf_counter_ns();await repo.persist(terminal);terminal_ms=(time.perf_counter_ns()-before)/1e6
        await backend.listing(principal,status='COMPLETED')
        async with db.sessions() as s:
            plans={
                'exact':(await s.execute(text("EXPLAIN QUERY PLAN SELECT run_id FROM observability_run_status WHERE run_id=:id"),{'id':rows[0].run_id})).all(),
                'active':(await s.execute(text("EXPLAIN QUERY PLAN SELECT run_id FROM observability_run_status WHERE environment='development' AND status='RUNNING' ORDER BY heartbeat_at"))).all(),
                'timeline':(await s.execute(text("EXPLAIN QUERY PLAN SELECT event_seq FROM observability_run_event WHERE run_id=:id ORDER BY event_seq"),{'id':rows[0].run_id})).all(),
            }
        assert all('INDEX' in str(p).upper() for p in plans.values())
        print(f'M7 LOCAL SQLite background/API median ms: write_with_event={statistics.median(durations):.3f}; terminal_with_summary={terminal_ms:.3f}; queries={timings}; dataset=200 runs; queries=20 each; plans=indexed')
    finally:await db.close()
