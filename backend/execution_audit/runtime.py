"""Explicit opt-in bindings and class-C execution handles; no OTel source of truth."""
import asyncio
from contextlib import contextmanager
from contextvars import ContextVar
import time
from uuid import uuid4
from .journal import event

_installed=None
_agent=ContextVar('audit_agent_execution',default=None)
_tool=ContextVar('audit_tool_execution',default=None)
_context=ContextVar('audit_business_correlation',default={})

def install(journal,*,controlled_egress=False):
    global _installed
    if _installed is not None:raise ValueError('Journal already bound')
    _installed=Runtime(journal,asyncio.get_running_loop(),controlled_egress)
    return _installed

def release(runtime):
    global _installed
    if _installed is runtime:_installed=None

def current():return _installed

def agent_id():
    handle=_agent.get()
    return handle.identity if handle and not handle.closed else None

def tool_id():
    handle=_tool.get()
    return handle.identity if handle and not handle.closed else None

@contextmanager
def correlation(**refs):
    token=_context.set({**_context.get(),**{k:v for k,v in refs.items() if v is not None}})
    try:yield
    finally:_context.reset(token)

def references():
    # Explicit owner context preferred. Existing turn handle is a correlation source,
    # never a source for deciding whether an activity happened.
    refs=dict(_context.get())
    if tool_id():refs.setdefault('tool_execution_id',tool_id())
    from backend.observability.turn_trace import current_turn
    turn=current_turn()
    if turn:
        for key in ('session_id','run_id','turn_id','trace_id'):
            value=getattr(turn,key,None)
            if value is not None:refs.setdefault(key,value)
    return refs

class Runtime:
    def __init__(self,journal,loop,controlled_egress):
        self.journal,self.loop,self.controlled_egress=journal,loop,controlled_egress
        self.capture_failures=0
        self.slots=asyncio.Semaphore(32)
    async def observe(self,fact):
        try:
            async with asyncio.timeout(1),self.slots:
                return await self.journal.append(fact)
        except Exception:
            self.capture_failures+=1
            return False # Class C preserves existing read/tool semantics; NO full claim.
    def sync(self,coroutine):
        try:running=asyncio.get_running_loop()
        except RuntimeError:running=None
        if running is self.loop:
            coroutine.close();raise RuntimeError('Controlled sync adapter requires owning worker thread')
        future=asyncio.run_coroutine_threadsafe(coroutine,self.loop)
        try:return future.result(timeout=5)
        except BaseException:
            future.cancel();raise

class Activity:
    def __init__(self,domain,**safe):
        self.runtime=current();self.domain=domain;self.safe=safe
        self.identity=str(uuid4()) if self.runtime else None
        self.refs=references();self.started=time.monotonic()
        if agent_id():self.safe.setdefault('parent_agent_execution_id' if domain=='agent' else 'agent_execution_id',agent_id())
        self.token=None
        self.closed=False
    def fact(self,kind,outcome,version,**extra):
        refs=dict(self.refs);safe=dict(self.safe)
        if self.domain=='tool':refs['tool_execution_id']=self.identity
        elif self.domain=='retrieval':safe['retrieval_id']=self.identity
        elif self.domain=='agent':safe['agent_execution_id']=self.identity
        return event(kind,self.runtime.journal.environment,self.identity,version,
            consistency_class='C',outcome=outcome,**refs,**safe,**extra)
    async def start(self):
        if not self.runtime:return
        suffix='execution.started' if self.domain in ('tool','agent') else 'started'
        try:await self.runtime.observe(self.fact(self.domain+'.'+suffix,'STARTED',1))
        except Exception:self.runtime.capture_failures+=1

    @contextmanager
    def attached(self):
        variable=_agent if self.domain=="agent" else _tool
        token=variable.set(self) if self.runtime and self.domain in ("agent","tool") else None
        ref_token=_context.set({**_context.get(),**self.refs}) if self.runtime and self.domain=="agent" else None
        try:yield
        finally:
            if ref_token is not None:_context.reset(ref_token)
            if token is not None:variable.reset(token)
    async def finish(self,error=None,**safe):
        if not self.runtime:return
        try:
            outcome='COMPLETED';code=None
            if isinstance(error,(asyncio.CancelledError,GeneratorExit)):outcome,code='CANCELLED','CANCELLED'
            elif isinstance(error,TimeoutError):outcome,code='TIMEOUT','TIMEOUT'
            elif error is not None:outcome,code='FAILED','EXECUTION_ERROR'
            suffix='completed' if error is None else 'failed'
            if self.domain in ('tool','agent'):suffix='execution.'+suffix
            try:
                await self.runtime.observe(self.fact(self.domain+'.'+suffix,outcome,2,
                    failure_code=code,duration_ms=max(0,(time.monotonic()-self.started)*1000),**safe))
            except Exception:self.runtime.capture_failures+=1
        finally:
            self.closed=True
            if self.token is not None:_agent.reset(self.token)
