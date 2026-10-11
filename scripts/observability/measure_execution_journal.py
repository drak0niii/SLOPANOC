"""Local journal profile by domain. No runtime URLs, providers or cloud clients."""
import asyncio
from collections import defaultdict
import json
from pathlib import Path
import tempfile,time
from unittest.mock import patch
from uuid import uuid4
from sqlalchemy.ext.asyncio import AsyncSession
from backend.observability.database import Database
from backend.observability.hybrid.models import HybridBase
from backend.observability.hybrid import capture
from backend.execution_audit.journal import Journal,event
from backend.execution_audit.runtime import install,release,Activity
from backend.knowledge.retrieval.service import KnowledgeRetrievalService
from backend.knowledge.retrieval.contracts import KnowledgeRetrievalQuery
from backend.observability.projection import utcnow

async def run():
    phases=defaultdict(lambda:{'calls':0,'wall_ms':0.,'cpu_ms':0.});domain='unknown'
    def timed(name,fn):
        def wrapped(*args,**kwargs):
            w,c=time.perf_counter(),time.process_time()
            try:return fn(*args,**kwargs)
            finally:
                p=phases[domain+'.'+name];p['calls']+=1;p['wall_ms']+=(time.perf_counter()-w)*1000;p['cpu_ms']+=(time.process_time()-c)*1000
        return wrapped
    execute=AsyncSession.execute
    async def sql(self,stmt,*args,**kwargs):
        table=getattr(getattr(stmt,'table',None),'name',None)
        if getattr(stmt,'is_insert',False) and table in ('observability_critical_audit','observability_audit_outbox'):
            w,c=time.perf_counter(),time.process_time()
            try:return await execute(self,stmt,*args,**kwargs)
            finally:
                p=phases[domain+'.'+table];p['calls']+=1;p['wall_ms']+=(time.perf_counter()-w)*1000;p['cpu_ms']+=(time.process_time()-c)*1000
        return await execute(self,stmt,*args,**kwargs)
    totals={}
    with tempfile.TemporaryDirectory(prefix='slopanoc-execution-profile-') as directory:
        db=Database('sqlite+aiosqlite:///'+directory+'/schema.sqlite',isolated_test=True)
        try:
            async with db.engine.begin() as c:await c.run_sync(HybridBase.metadata.create_all)
            journal=Journal(db.sessions,'test')
            from backend.execution_audit import journal as module
            with patch.object(module,'event',timed('construction',module.event)),patch.object(capture,'decode_event',timed('validation',capture.decode_event)),patch.object(capture,'canonical_digest',timed('serialization_hash',capture.canonical_digest)),patch.object(AsyncSession,'execute',sql):
                for domain,kind,consistency,outcome in [('session','session.created','C','CREATED'),('agent','agent.execution.started','C','STARTED'),('retrieval','retrieval.started','C','STARTED'),('evidence','evidence.selection.recorded','A','RECORDED'),('tool','tool.execution.started','C','STARTED'),('approval','approval.requested','B','PENDING'),('authorization','authorization.decided','D','AUTHORIZED'),('action','action.dispatch_attempted','D','DISPATCHING')]:
                    w,c=time.perf_counter(),time.process_time()
                    for _ in range(30):
                        identity=str(uuid4());args={}
                        if domain=='agent':args['agent_execution_id']=identity
                        elif domain=='retrieval':args['retrieval_id']=identity
                        elif domain=='tool':args['tool_execution_id']=identity
                        elif domain=='authorization':args.update(authorization_id=identity,decision='ALLOW',policy_version='teams-write-v1')
                        elif domain=='action':args.update(attempt_id=identity,authorization_id=uuid4())
                        fact=module.event(kind,'test',identity,consistency_class=consistency,outcome=outcome,**args)
                        await journal.append(fact)
                    totals[domain]={'operations':30,'wall_ms_per_fact':(time.perf_counter()-w)*1000/30,'cpu_ms_per_fact':(time.process_time()-c)*1000/30,'scope':'capture fixture; not a claimed business transition'}
            class EmptyKnowledge:
                async def list_all(self):return []
            retrieval=KnowledgeRetrievalService(EmptyKnowledge())
            query=KnowledgeRetrievalQuery(query_text='local fixture',as_of=utcnow(),limit=1)
            owner={}
            for enabled in (False,True):
                runtime=install(journal) if enabled else None
                try:
                    w,c=time.perf_counter(),time.process_time()
                    for _ in range(100):assert not (await retrieval.retrieve(query)).items
                    owner[str(enabled)]={'wall_ms_per_operation':(time.perf_counter()-w)*10,'cpu_ms_per_operation':(time.process_time()-c)*10}
                finally:
                    if runtime:release(runtime)
        finally:await db.close()
    for phase in phases.values():
        phase['wall_ms_per_call']=phase['wall_ms']/phase['calls'];phase['cpu_ms_per_call']=phase['cpu_ms']/phase['calls']
    result={'scope':'local SQLite/no provider/no transport; profiled capture arms separate from clean retrieval owner arms',
        'domains':totals,'phases':dict(phases),'retrieval_owner_baseline':owner['False'],'retrieval_owner_enabled':owner['True'],
        'retrieval_owner_added':{k:owner['True'][k]-owner['False'][k] for k in owner['False']},
        'limits':'Overlapping phase timings are not additive. Domain capture fixtures do not include real business owner cost. Only actual empty-corpus retrieval owner baseline is compared. No GCP/SQL WAL/load proof.',
        'C41':'NOT PASS','C42':'NOT PASS','cloud_changes':'NONE'}
    Path('docs/Telemetry/validation/EXECUTION_JOURNAL_MEASUREMENT.json').write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps(result['retrieval_owner_added']))

if __name__=='__main__':asyncio.run(run())
