"""Isolated performance receipt; no runtime URL/credentials or external clients."""
import asyncio
import json
import time
from pathlib import Path
from unittest.mock import patch
from collections import defaultdict
from backend.observability.hybrid import capture, contracts
from scripts.observability.measure_hybrid_local import measure

async def run():
    phases=defaultdict(lambda:{'calls':0,'wall_ms':0.,'cpu_ms':0.})
    def timed(name,fn):
        def wrapper(*args,**kw):
            w,c=time.perf_counter(),time.process_time()
            try:return fn(*args,**kw)
            finally:
                p=phases[name];p['calls']+=1
                p['wall_ms']+=(time.perf_counter()-w)*1000
                p['cpu_ms']+=(time.process_time()-c)*1000
        return wrapper
    from sqlalchemy.ext.asyncio import AsyncSession
    execute=AsyncSession.execute
    async def sql(self,statement,*args,**kw):
        table=getattr(getattr(statement,'table',None),'name',None)
        if getattr(statement,'is_insert',False) and table in ('observability_critical_audit','observability_audit_outbox'):
            name='critical_audit_insert' if table.endswith('critical_audit') else 'outbox_insert'
            w,c=time.perf_counter(),time.process_time()
            try:return await execute(self,statement,*args,**kw)
            finally:
                p=phases[name];p['calls']+=1;p['wall_ms']+=(time.perf_counter()-w)*1000;p['cpu_ms']+=(time.process_time()-c)*1000
        return await execute(self,statement,*args,**kw)
    # First measurement clean; second is separately profiled, so instrumentation
    # does not contaminate the reported clean baseline/overhead comparison.
    result=await measure()
    with patch.object(capture,'make_event',timed('event_model_construction',capture.make_event)), \
         patch.object(contracts,'validate_privacy',timed('privacy_validation',contracts.validate_privacy)), \
         patch.object(capture,'canonical_digest',timed('canonical_serialization_hash',capture.canonical_digest)), \
         patch.object(contracts.AuditEvent,'model_dump_json',timed('envelope_serialization',contracts.AuditEvent.model_dump_json)), \
         patch.object(AsyncSession,'execute',sql):
        profiled=await measure()
    for p in phases.values():
        p['wall_ms_per_call']=p['wall_ms']/p['calls'];p['cpu_ms_per_call']=p['cpu_ms']/p['calls']
    result['relative_added_percent']={k:100*result['added'][k]/result['baseline'][k] for k in result['baseline']}
    result['profiled_phases']=dict(phases)
    result['profile_scope']='separate instrumented 200 captured operations plus publisher/projector/freshness; serialization/privacy also called at other sinks; phase totals are overlapping and must not be summed'
    result['ORM_session_overhead']='SQL execute measurements include ORM/driver dispatch and await. Remaining owner transaction includes row locks, source/bucket updates, flush/commit and validation; no invented separable pure ORM number.'
    before=json.loads(Path('docs/Telemetry/validation/HYBRID_PHASE1_LOCAL_MEASUREMENT.json').read_text())
    result['previous_receipt_added']=before['added']
    result['optimization']='One sink validation rather than duplicate decode; skip audit ORM SELECT when INSERT RETURNING confirms a new immutable row. Duplicate still SELECTs and verifies hash. Same caller-owned transaction and outbox idempotency.'
    Path('docs/Telemetry/validation/HYBRID_PHASE1_PRODUCTION_MEASUREMENT.json').write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps({k:result[k] for k in ('baseline','outbox_enabled','added','relative_added_percent','profiled_phases')},indent=2))

if __name__=='__main__':asyncio.run(run())
