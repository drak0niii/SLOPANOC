"""Bounded best-effort operational writer and evaluator provider seam."""
import asyncio
import hashlib
from datetime import datetime, timezone, timedelta
from threading import Lock, RLock
from .slo_rollups import Receipt
from .slo_evaluator import Counts, Snapshot
from .slo_contract import RequestClass, CancelOrigin, classify, SETTLEMENT_GRACE_SECONDS, SAFETY_KINDS


def now_utc(): return datetime.now(timezone.utc)
def subject_digest(subject): return hashlib.sha256(subject.encode('utf-8')).hexdigest()

class FixtureProvider:
    """Explicitly injected fixtures; never selected from production headers/config."""
    def __init__(self,snapshots): self.snapshots=snapshots
    async def snapshot(self,environment,slo_id,now):
        return self.snapshots.get((environment,slo_id),Snapshot(environment,available=False))

class RuntimeProvider:
    def __init__(self,rollups,config): self.rollups,self.config=rollups,config
    async def snapshot(self,environment,slo_id,now):
        return await self.rollups.snapshot(environment,slo_id,now,self.config.turn_timeout_seconds+SETTLEMENT_GRACE_SECONDS)

class CloudMonitoringProvider:
    """Future read-only adapter boundary; no invented production ingestion or values."""
    async def snapshot(self,environment,slo_id,now):
        return Snapshot(environment,available=False,source='CLOUD_MONITORING_NOT_ACTIVATED')

class Writer:
    def __init__(self,rollups,config,runtime=None,*,clock=now_utc):
        self.rollups,self.config,self.runtime,self.clock=rollups,config,runtime,clock
        self.loop=asyncio.get_running_loop();self.queue=asyncio.Queue(maxsize=1024)
        self.lock=Lock();self.inflight=0;self.lost=False;self.closed=False
        self.task=self.loop.create_task(self._drive());self.last_prune=None;self.last_evaluation=None
    def publish(self,event):
        with self.lock:
            if self.closed or self.inflight>=1024:
                self.lost=True;return False
            self.inflight+=1
        def put():
            try: self.queue.put_nowait(event)
            except asyncio.QueueFull:
                with self.lock: self.inflight-=1;self.lost=True
        try: self.loop.call_soon_threadsafe(put)
        except RuntimeError:
            with self.lock: self.inflight-=1;self.lost=True
            return False
        return True
    async def _drive(self):
        while not self.closed or self.inflight:
            event=None
            try:
                event=await asyncio.wait_for(self.queue.get(),1)
                async with asyncio.timeout(2):
                    if isinstance(event,Receipt): await self.rollups.save(event,self.clock())
                    else:
                        slo_id,at,counts=event
                        await self.rollups.record(self.config.otel_environment,slo_id,at,counts)
            except TimeoutError:
                if event is not None: self.lost=True
            except asyncio.CancelledError: raise
            except Exception: self.lost=True
            finally:
                if event is not None:
                    with self.lock: self.inflight-=1
                    self.queue.task_done()
            try:
                async with asyncio.timeout(2):
                    await self.rollups.settle(self.clock())
                    await self.rollups.heartbeat(self.config.otel_environment,self.clock(),not self.lost)
                    if self.last_evaluation is None or self.clock()-self.last_evaluation>=timedelta(seconds=30):
                        from .slo_contract import DEFINITIONS
                        from .slo_evaluator import evaluate
                        from .slo_metrics import publish_result,record
                        provider=RuntimeProvider(self.rollups,self.config)
                        for key in DEFINITIONS:
                            snapshot=await provider.snapshot(self.config.otel_environment,key,self.clock())
                            publish_result(self.runtime,evaluate(key,snapshot,now=self.clock()))
                        projection=getattr(self.runtime,'projection',None)
                        if projection is not None:
                            counts,_=projection.health.snapshot()
                            record(self.runtime,'slopanoc.persistence.pending',len(projection.slots),'projection','RUNNING')
                            record(self.runtime,'slopanoc.persistence.failed',counts['failed'],'projection','RUNNING')
                        self.last_evaluation=self.clock()
                    if self.last_prune is None or self.clock()-self.last_prune>=timedelta(hours=1):
                        await self.rollups.prune(self.clock());self.last_prune=self.clock()
            except asyncio.CancelledError: raise
            except Exception: self.lost=True
    async def drain(self):
        # Wait for scheduled publications before joining, without real clock sleeps.
        await asyncio.sleep(0);await self.queue.join()
    async def close(self):
        self.closed=True
        try:
            async with asyncio.timeout(3): await self.task
        except TimeoutError:
            self.lost=True;self.task.cancel()
            await asyncio.gather(self.task,return_exceptions=True)

class TraceAssessment:
    """Unsampled finite counters/sets; optional components required only when used."""
    def __init__(self):
        self.phases=set();self.ended=set();self.components={k:[0,0] for k in ('agent','tool','dependency','model')}
        self.errors=False;self.phase_counts={}
    def phase(self,operation,ended):
        counts=self.phase_counts.setdefault(operation,[0,0]);counts[int(ended)]+=1
        (self.ended if ended else self.phases).add(operation)
    def require(self,kind):
        self.components[kind][0]+=1
    def component(self,kind,valid):
        self.components[kind][1]+=int(valid)
    def complete(self,*,root,terminal,correlation,closure):
        return bool(root and terminal and correlation and closure and not self.errors
            and self.phases<=self.ended and all(expected==present for expected,present in self.components.values())
            and all(expected==present for expected,present in self.phase_counts.values()))

class Tracker:
    def __init__(self,turn,writer):
        self.turn,self.writer=turn,writer;self.version=0;self.active=False;self.lock=RLock()
        self.cancel_origin=CancelOrigin.UNKNOWN;self.sse_expected=False;self.emitted_at=None
        self.transport='pending';self.specialists=set();self.governed=False;self.teams=False;self.general=False
        self.phase_names={};self.trace=TraceAssessment();self.trace.phases.add('request');self.trace.ended.add('request')
        self.digest=subject_digest('unbound')
    def activate(self,subject,sse_expected=False):
        if self.active:return
        self.digest=subject_digest(subject);self.sse_expected=sse_expected;self.active=True;self.publish()
        from .slo_metrics import record
        record(self.turn.runtime,"slopanoc.turn.accepted",1,"accepted","RUNNING")
    def phase(self,operation,ended=False):
        self.trace.phase(operation,ended)
    def require(self,kind):
        self.trace.require(kind)
    def component(self,kind,valid,*,agent=None,tool=None):
        self.trace.component(kind,valid)
        if agent in {'technical_authority_engineer','incident_manager','problem_manager','automated_operations_engineer'}: self.specialists.add(agent)
        if tool in {'teams_list_chats','teams_get_messages','get_resolved_chat_messages','teams_get_hosted_content','teams_get_all_hosted_content','teams_get_members'}: self.teams=True
    def route(self,governed):
        self.governed|=governed;self.general|=not governed
    def emit(self):
        if self.emitted_at is None:self.emitted_at=now_utc();self.publish()
    def relay(self,result):
        # Confirmed capacity/server failure must survive final consumer cleanup.
        if self.transport not in ('backpressure_failed','relay_failed'):self.transport=result
        self.publish()
    def publish(self):
        if not self.active:return
        t=self.turn;self.version+=1
        closed=t.terminal_at is not None
        cause=CancelOrigin.DEADLINE if t.status.value=='TIMEOUT' else CancelOrigin.FAILURE if t.status.value=='FAILED' else self.cancel_origin
        root=t.trace_id is not None and t.span_id is not None
        complete=self.trace.complete(root=root,terminal=closed,correlation=bool(t.run_id and t.turn_id),closure=closed and getattr(t,'_sre_root_closed',False))
        receipt=Receipt(run_id=t.run_id,environment=self.writer.config.otel_environment,subject_digest=self.digest,
            accepted_at=t.started_at,deadline_at=t.started_at+timedelta(seconds=self.writer.config.turn_timeout_seconds),
            version=self.version,terminal_at=t.terminal_at,terminal_status=t.status.value if closed else None,
            terminal_count=int(closed),valid_outcome=closed and t.status.value=='COMPLETED' and t._wire_outcome=='ok',
            cancel_origin=cause,request_class=classify(specialists=self.specialists,governed=self.governed,teams_lookup=self.teams,general=self.general),
            duration_ms=t._end_elapsed_ms if closed else None,trace_complete=complete,sse_expected=self.sse_expected,
            emitted_at=self.emitted_at,transport=self.transport)
        self.writer.publish(receipt)
        if closed and not getattr(self,"duration_recorded",False):
            self.duration_recorded=True
            from .slo_metrics import record
            record(t.runtime,"slopanoc.turn.duration",receipt.duration_ms/1000,receipt.request_class.value,t.status.value)


def observe(turn,method,*args,**kwargs):
    """Observer failure cannot change return values, cancellation or business completion."""
    tracker=getattr(turn,'sre',None)
    if tracker is None:return
    try:
        with tracker.lock: getattr(tracker,method)(*args,**kwargs)
    except Exception: tracker.writer.lost=True


def model_observation(runtime,value,turn=None):
    writer=getattr(runtime,'sre',None)
    if writer is None:return
    try:
        origin=getattr(getattr(turn,'sre',None),'cancel_origin',CancelOrigin.UNKNOWN)
        excluded=value.status.value=='CANCELLED' and origin==CancelOrigin.USER
        writer.publish(('model_reliability',value.completed_at,Counts(excluded=1) if excluded else Counts(good=int(value.status.value=='COMPLETED'),bad=int(value.status.value!='COMPLETED'))))
        if value.streaming and value.workload=='user_turn':
            valid=value.ttft_ms is not None and value.ttft_boundary=='first_provider_output'
            writer.publish(('model_ttft',value.completed_at,Counts(good=int(valid and value.ttft_ms<=5000),bad=int(valid and value.ttft_ms>5000),unknown=int(not valid))))
        observe(turn,'component','model',value.trace_id is not None and value.span_id is not None)
    except Exception: writer.lost=True


def dependency_observation(scope):
    writer=getattr(scope.runtime,'sre',None)
    if writer is None:return
    try:
        if scope.kind=='external':
            dep=scope.dependency
            key='gateway' if dep=='power_automate_gateway' else 'database' if dep.endswith('_db') else 'storage' if dep in ('chat_attachments','knowledge_artifacts') else 'knowledge' if dep=='knowledge' else None
            if key:
                origin=getattr(getattr(scope.turn,'sre',None),'cancel_origin',CancelOrigin.UNKNOWN)
                excluded=scope.status.value=='CANCELLED' and origin==CancelOrigin.USER
                writer.publish(('dependency_'+key,now_utc(),Counts(excluded=1) if excluded else Counts(good=int(scope.status.value=='COMPLETED'),bad=int(scope.status.value!='COMPLETED'))))
        if scope.turn: observe(scope.turn,'component','dependency',scope.span.get_span_context().is_valid)
    except Exception:writer.lost=True


from contextlib import contextmanager

@contextmanager
def knowledge_operation(_operation):
    """One whole retrieval outcome, independent of local stages and empty results."""
    from .agent_instrumentation import runtime_for_execution
    from .turn_trace import current_turn
    try:
        runtime=runtime_for_execution();writer=getattr(runtime,'sre',None);turn=current_turn()
    except Exception:
        writer=None;turn=None
    kind='good'
    try:
        yield None
    except BaseException as error:
        origin=getattr(getattr(turn,'sre',None),'cancel_origin',CancelOrigin.UNKNOWN)
        kind='excluded' if isinstance(error,asyncio.CancelledError) and origin==CancelOrigin.USER else 'bad'
        raise
    finally:
        if writer is not None:
            try: writer.publish(('dependency_knowledge',now_utc(),Counts(**{kind:1})))
            except Exception: writer.lost=True
