"""Focused local SQL overhead, not M16 C41 acceptance. Never reads runtime URLs."""
import asyncio
import json
import statistics
import tempfile
import time
from datetime import timedelta
from pathlib import Path
from uuid import uuid4
from backend.observability.database import Database
from backend.observability.config import ObservabilityConfig
from backend.observability.models import Base
from backend.observability.finops.models import FinOpsBase
from backend.observability.finops.contracts import Identity
from backend.observability.finops.repository import Repository
from backend.observability.hybrid.models import HybridBase
from backend.observability.hybrid.capture import Capture
from backend.observability.hybrid.config import HybridConfig
from backend.observability.hybrid.local import compose
from backend.observability.hybrid.read import ReadRepository
from backend.observability.projection import RunState, utcnow

async def measure():
    samples={False:[],True:[]}
    with tempfile.TemporaryDirectory(prefix='slopanoc-hybrid-local-') as directory:
        db=Database('sqlite+aiosqlite:///'+directory+'/isolated.sqlite',isolated_test=True)
        try:
            async with db.engine.begin() as c:
                for base in (Base,FinOpsBase,HybridBase):await c.run_sync(base.metadata.create_all)
            for enabled in (False,True,True,False,False,True,True,False):
                repo=Repository(db.sessions,audit_capture=Capture() if enabled else None)
                wall=cpu=0
                for _ in range(50):
                    now=utcnow()
                    i=Identity(environment='development',attempt_id=uuid4(),logical_call_id=uuid4(),
                        provider_request_id=uuid4(),attempt=1,provider='gcp.gemini',model='gemini-2.5-flash',
                        agent='team_manager',operation='orchestration',operation_type='GENERATION',
                        workload='USER_TURN',started_at=now,deadline_at=now+timedelta(minutes=1))
                    await repo.admit(i)  # excluded preparation; same owner transaction for start
                    w,c=time.perf_counter(),time.process_time()
                    await repo.start(i,now)
                    cpu+=time.process_time()-c;wall+=time.perf_counter()-w
                samples[enabled].append({'wall_ms_per_operation':wall*1000/50,'process_cpu_ms_per_operation':cpu*1000/50})
            pipeline=compose(db,ObservabilityConfig(),HybridConfig(environment='development',capture_enabled=True),isolated_local=True)
            # Publisher/projector CPU separately; 200 accumulated audit events, fake transport only.
            worker={name:{'events':0,'wall_ms':0,'process_cpu_ms':0} for name in ('publisher','projector')}
            for _ in range(2):
                for name,fn in (('publisher',pipeline.publisher.tick),('projector',lambda:pipeline.projector.tick(pipeline.transport))):
                    w,c=time.perf_counter(),time.process_time()
                    count=await fn()
                    worker[name]['process_cpu_ms']+=(time.process_time()-c)*1000
                    worker[name]['wall_ms']+=(time.perf_counter()-w)*1000
                    worker[name]['events']+=count
            assert worker['publisher']['events']==worker['projector']['events']==200
            # Fresh event immediately drained, not the intentionally accumulated benchmark backlog.
            w=time.perf_counter();now=utcnow();run=RunState(run_id=str(uuid4()),producer_instance_id=str(uuid4()),state_version=1,
                session_id='local-measurement',environment='development',status='RUNNING',current_stage='request.received',
                started_at=now,stage_started_at=now,last_progress_at=now,heartbeat_at=now,observed_at=now,elapsed_ms=0)
            await pipeline.run_repository.persist(run);await pipeline.tick()
            view=await ReadRepository(db.sessions).activity('development')
            assert str(view.runs[0].run_id)==run.run_id
            freshness={'projection_lag_seconds':view.runs[0].projection_lag,
                'event_to_read_wall_ms':(time.perf_counter()-w)*1000,
                'includes':'SQL owner commit, explicit worker ticks, SQL projection commit, read repository',
                'worker_poll_interval_excluded':True,'propagation_slo':'NOT CLAIMED'}
            await pipeline.close()
        finally:await db.close()
    baseline={k:statistics.mean(x[k] for x in samples[False]) for k in samples[False][0]}
    enabled={k:statistics.mean(x[k] for x in samples[True]) for k in samples[True][0]}
    return {'scope':'isolated SQLite M10 Repository.start transaction; no provider/OTLP/cloud',
        'operations_per_arm':200,'sample_batches_per_arm':4,'baseline':baseline,'outbox_enabled':enabled,
        'added':{k:enabled[k]-baseline[k] for k in baseline},'samples':{'baseline':samples[False],'enabled':samples[True]},
        'async_workers':worker,'freshness':freshness,'M16_C41':'NOT PASS','M16_C42':'NOT PASS',
        'limits':'single process; SQLite; fake transport; explicit ticks; no PostgreSQL/WAL/network/load proof',
        'cloud_changes':'NONE'}

if __name__=='__main__':
    result=asyncio.run(measure())
    output=Path('docs/Telemetry/validation/HYBRID_PHASE1_LOCAL_MEASUREMENT.json')
    output.write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps({k:result[k] for k in ('baseline','outbox_enabled','added','async_workers','freshness')},indent=2))
