"""Bounded daemon workers with admission held until actual synchronous completion.

Callers can time out without killing threads. Workers receive isolated business
state; their deferred mailbox writes are committed only by an active owner.
"""
import asyncio
from concurrent.futures import Future, TimeoutError as FutureTimeout
from contextvars import ContextVar, copy_context, Context
from dataclasses import dataclass
from functools import wraps
from queue import Queue
from threading import Thread, Lock, Event
from collections import deque
import time
from .deadlines import DeadlineExceeded, child_budget, enforce, current_controller, observed, check, bind
from .reliability_contract import Category

_work = ContextVar('slopanoc_blocking_work', default=None)
_deferred = ContextVar('slopanoc_deferred_business_writes', default=None)

class AdmissionRejected(RuntimeError):
    def __init__(self):
        super().__init__('blocking_admission_rejected')

@dataclass
class Work:
    category: Category
    started: float
    caller: str = 'not_started'
    underlying: str = 'not_started'
    outcome: str = 'NOT_STARTED'
    ended: float | None = None
    late: bool = False

class DeferredWrites:
    def __init__(self, budget, loop):
        self.calls = []
        self.budget, self.loop = budget, loop
        self.owner_context = copy_context()

    def owner_call(self, fn, args, kwargs):
        # Return-valued local mailbox mutations execute on the active owner.
        # No external business invocation is repeated or moved into this callback.
        result = Future()
        def commit():
            if result.cancelled():
                return
            try:
                self.budget.check()
                check()
                value = fn(*args, **kwargs)
            except BaseException as exc:
                if not result.done():
                    result.set_exception(exc)
            else:
                if not result.done():
                    result.set_result(value)
        self.budget.check()
        try:
            self.loop.call_soon_threadsafe(commit, context=self.owner_context)
            return result.result(timeout=self.budget.remaining)
        except (FutureTimeout, RuntimeError):
            result.cancel()
            self.budget.check()
            raise DeadlineExceeded(self.budget) from None

    def apply(self):
        check()
        for fn, args, kwargs in self.calls:
            fn(*args, **kwargs)
        self.calls.clear()


def owner_business(fn):
    """Preserve synchronous return semantics through a guarded owner-loop handoff."""
    @wraps(fn)
    def wrapped(*args, **kwargs):
        batch = _deferred.get()
        return batch.owner_call(fn,args,kwargs) if batch is not None else fn(*args,**kwargs)
    return wrapped


def defer_business(fn):
    """Worker-local writes are committed in original order by the active owner."""
    @wraps(fn)
    def wrapped(*args, **kwargs):
        batch = _deferred.get()
        if batch is not None:
            batch.calls.append((fn,args,kwargs))
            return None
        return fn(*args, **kwargs)
    return wrapped

class BlockingExecutor:
    def __init__(self, workers=8, pending=16):
        self.workers, self.pending = workers, pending
        self._queue = Queue(maxsize=workers+pending)
        self._lock = Lock()
        self._threads = []
        self._count = 0
        self.history = deque(maxlen=128)  # safe metadata only, never task/result history

    @property
    def occupied(self):
        with self._lock:
            return self._count

    def submit(self, fn, category):
        future = Future()
        entry = Work(Category(category), time.monotonic())
        with self._lock:
            if self._count >= self.workers+self.pending:
                observed('saturation', entry.category)
                observed('admission_rejected', entry.category, status='FAILED')
                raise AdmissionRejected()
            self._count += 1
            self._queue.put_nowait((future, fn, entry))
            if len(self._threads) < self.workers:
                thread = Thread(target=self._worker, name='slopanoc-blocking-work', daemon=True)
                self._threads.append(thread)
                thread.start()
        return future, entry

    def _worker(self):
        while True:
            future, fn, entry = self._queue.get()
            try:
                if future.set_running_or_notify_cancel():
                    entry.underlying, entry.outcome = 'running', 'OUTCOME_UNKNOWN'
                    try:
                        result = fn()
                    except BaseException as exc:
                        entry.outcome = 'OUTCOME_UNKNOWN' if entry.caller in ('timeout','cancelled') else 'CONFIRMED_FAILURE'
                        future.set_exception(exc)
                    else:
                        entry.outcome = 'CONFIRMED_SUCCESS'
                        future.set_result(result)
                entry.underlying = 'terminated'
                entry.ended = time.monotonic()
                entry.late = entry.caller in ('timeout','cancelled')
                with self._lock:
                    self.history.append(entry)
            finally:
                with self._lock:
                    self._count -= 1
                self._queue.task_done()
                del future, fn, entry
                result = None

    async def run(self, fn, *args, category=Category.TOOL, budget=None, **kwargs):
        budget = budget or child_budget(category)
        budget.check()  # No admission or remote egress under expired parent.
        loop = asyncio.get_running_loop()
        batch = DeferredWrites(budget, loop)
        ctx = copy_context()
        ctx.run(_deferred.set, batch)
        owner = current_controller()
        from .agent_instrumentation import runtime_for_execution
        runtime = owner.turn.runtime if owner and owner.turn else runtime_for_execution()
        def invoke():
            # The absolute budget is checked even if submission waited in queue.
            with bind(budget):
                check()
                budget.check()
                return fn(*args, **kwargs)
        # Bind metadata before submission; a worker may begin immediately.
        ready = Event()
        def submitted():
            ready.wait()
            return ctx.run(invoke)
        future, entry = self.submit(submitted, category)
        ctx.run(_work.set, entry)
        if owner is not None:
            owner.workers.append(entry)
        ready.set()
        entry.caller = 'waiting'
        completed = asyncio.Event()
        def actual_done(_future):
            try:
                loop.call_soon_threadsafe(completed.set, context=Context())
            except RuntimeError:
                pass
        future.add_done_callback(actual_done)
        try:
            async with enforce(budget):
                await completed.wait()
                result = future.result()
                budget.check()
                check()
                batch.apply()
                entry.caller = 'completed'
                return result
        except (TimeoutError, asyncio.CancelledError) as exc:
            entry.caller = 'cancelled' if isinstance(exc, asyncio.CancelledError) else 'timeout'
            # Pending work can be cancelled; actual running work keeps capacity.
            future.cancel()
            if entry.underlying == 'running':
                observed('worker_running', Category(category), status='TIMEOUT' if entry.caller=='timeout' else 'CANCELLED')
                if owner:
                    owner.emit('reliability.worker_running', Category(category),
                        **{'slopanoc.caller_disposition':entry.caller,'slopanoc.worker_disposition':'running',
                           'slopanoc.outcome_certainty':'OUTCOME_UNKNOWN'})
            def late(_future):
                # No business result retrieval, root/agent/tool reattachment or mutation.
                def observe_late():
                    try:
                        from .reliability_metrics import record
                        record('late_completion', Category(category), status='TIMEOUT' if entry.caller=='timeout' else 'CANCELLED', runtime=runtime)
                        if runtime is not None and runtime.enabled and not runtime.closed:
                            from .logging import emit
                            from .schemas import OperationalEvent
                            emit(runtime, OperationalEvent.LATE_COMPLETION, metadata={'request_count':1})
                    except Exception:
                        pass
                    if owner:
                        owner.events.append(('reliability.late_completion',time.monotonic(),Category(category).value))
                Context().run(observe_late)
                batch.calls.clear()
            future.add_done_callback(late)
            raise
        except BaseException:
            entry.caller = 'failed'
            batch.calls.clear()
            raise

_executor = None
_executor_lock = Lock()

def executor():
    global _executor
    if _executor is None:
        from .deadlines import policy
        config = policy()
        with _executor_lock:
            if _executor is None:
                _executor = BlockingExecutor(config.blocking_workers, config.blocking_pending)
    return _executor

async def run_blocking(fn, *args, category=Category.TOOL, **kwargs):
    return await executor().run(fn, *args, category=category, **kwargs)

async def isolated_call(fn, *args, category=Category.TOOL, **kwargs):
    """Sync tools receive a private state snapshot, never mutable canonical state."""
    from copy import deepcopy
    from types import SimpleNamespace
    original = kwargs.get('tool_context')
    before = state = None
    if original is not None:
        live = original.state
        before = deepcopy(live.to_dict() if hasattr(live,'to_dict') else dict(live))
        state = deepcopy(before)
        kwargs['tool_context'] = SimpleNamespace(state=state)
    result = await run_blocking(fn,*args,category=category,**kwargs)
    check()
    if state is not None:
        changed = {key:value for key,value in state.items() if key not in before or value != before[key]}
        # Known-read registries accumulate across parallel calls; no lost evidence IDs.
        def merge(old,new):
            if isinstance(old,dict) and isinstance(new,dict):
                return {**old, **{k:merge(old[k],v) if k in old else v for k,v in new.items()}}
            if isinstance(old,list) and isinstance(new,list):
                return old + [v for v in new if v not in old]
            return new
        for key,value in changed.items():
            if key.startswith('known_'):
                value = merge(original.state.get(key),value)
            original.state[key]=value
    return result
