"""Per-admitted-category capture phases; isolated SQLite, no cloud or runtime config.

Exercises reference capture, not invented business transitions. Clean owner transaction
benchmark remains the existing M10 start benchmark. Phase timings overlap; do not sum.
"""
import asyncio
from collections import defaultdict
from datetime import timedelta
import json
from pathlib import Path
import tempfile
import time
from unittest.mock import patch
from uuid import uuid4
from sqlalchemy.ext.asyncio import AsyncSession,create_async_engine,async_sessionmaker
from backend.observability.hybrid import capture,contracts
from backend.observability.hybrid.models import HybridBase
from backend.observability.projection import utcnow
from scripts.observability.measure_hybrid_local import measure

async def run():
    result={'scope':'fabricated reference fixtures / isolated SQLite capture; real M10 owner benchmark separately',
            'M16_C41':'NOT PASS','M16_C42':'NOT PASS','cloud_changes':'NONE','categories':{}}
    with tempfile.TemporaryDirectory(prefix='slopanoc-audit-phases-') as directory:
        engine=create_async_engine('sqlite+aiosqlite:///'+directory+'/phases.sqlite')
        sessions=async_sessionmaker(engine,expire_on_commit=False)
        try:
            async with engine.begin() as c:await c.run_sync(HybridBase.metadata.create_all)
            for kind in sorted(capture.LEGACY_TYPES):
                phases=defaultdict(lambda:{'calls':0,'wall_ms':0.,'cpu_ms':0.})
                def timed(name,fn):
                    def wrapper(*args,**kwargs):
                        w,c=time.perf_counter(),time.process_time()
                        try:return fn(*args,**kwargs)
                        finally:
                            p=phases[name];p['calls']+=1;p['wall_ms']+=(time.perf_counter()-w)*1000;p['cpu_ms']+=(time.process_time()-c)*1000
                    return wrapper
                execute=AsyncSession.execute
                async def sql(self,stmt,*args,**kwargs):
                    table=getattr(getattr(stmt,'table',None),'name',None)
                    if getattr(stmt,'is_insert',False) and table in ('observability_critical_audit','observability_audit_outbox'):
                        w,c=time.perf_counter(),time.process_time()
                        try:return await execute(self,stmt,*args,**kwargs)
                        finally:
                            p=phases[table];p['calls']+=1;p['wall_ms']+=(time.perf_counter()-w)*1000;p['cpu_ms']+=(time.process_time()-c)*1000
                    return await execute(self,stmt,*args,**kwargs)
                w,c=time.perf_counter(),time.process_time()
                with patch.object(capture,'make_event',timed('construction',capture.make_event)),patch.object(capture,'decode_event',timed('validation',capture.decode_event)),patch.object(capture,'canonical_digest',timed('serialization_hash',capture.canonical_digest)),patch.object(AsyncSession,'execute',sql):
                    for _ in range(30):
                        identity=str(uuid4());now=utcnow();args={}
                        aggregate='governance' if kind.startswith('governance') else ('run' if kind.startswith(('run.','turn.')) else ('usage' if kind=='model.usage.captured' else 'provider_attempt'))
                        version=2 if kind=='run.terminal' else 1
                        if aggregate=='run':args.update(run_id=identity,turn_id='actual-fixture-turn',status='COMPLETED' if version==2 else 'RUNNING')
                        if kind.startswith('model.'):args.update(agent='team_manager',model_attempt_id=identity)
                        event=capture.make_event(kind,'development',aggregate,identity,version,now,**args)
                        async with sessions() as s,s.begin():await capture.Capture().append(s,event)
                total={'wall_ms_per_operation':(time.perf_counter()-w)*1000/30,'cpu_ms_per_operation':(time.process_time()-c)*1000/30}
                for p in phases.values():
                    p['wall_ms_per_call']=p['wall_ms']/p['calls'];p['cpu_ms_per_call']=p['cpu_ms']/p['calls']
                result['categories'][kind]={'operations':30,'profiled_capture_transaction':total,'phases':dict(phases)}
        finally:await engine.dispose()
    result['clean_M10_owner_transaction']=await measure()
    result['limits']='Category profile includes construction, caller transaction and commit but no business owner transition; overhead relative to real owning transition quantified only for M10 start. No new capture owners. SQLite/no production SQL/WAL/broker/network/load. Overlapping instrumented phases are not additive.'
    Path('docs/Telemetry/validation/AUDIT_CATEGORY_MEASUREMENT.json').write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps(result['clean_M10_owner_transaction']['added']))

if __name__=='__main__':asyncio.run(run())
