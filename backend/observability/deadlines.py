"""Monotonic absolute budgets. Runtime enforcement is independent of OTel.

Timeout scopes run in the owning Task, preserving generator/ContextVar ownership.
No retry engine, telemetry span, persistence or provider initialization lives here.
"""
import asyncio
import time
from contextlib import asynccontextmanager, contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from functools import wraps
from .reliability_contract import Category, CODES

_budget = ContextVar('slopanoc_deadline', default=None)
_controller = ContextVar('slopanoc_reliability_controller', default=None)
_cleanup_mode = ContextVar('slopanoc_cleanup_mode', default=False)
_policy_provider = None

def set_policy_provider(provider):
    # Composition root supplies Settings; observability contracts stay inert.
    global _policy_provider
    _policy_provider = provider

class DeadlineExceeded(TimeoutError):
    def __init__(self, budget):
        self.category, self.deadline, self.code = budget.category, budget.absolute, CODES[budget.category]
        super().__init__(self.code.value)  # Never include request/content/exception text.

@dataclass(frozen=True)
class Deadline:
    absolute: float
    category: Category
    clock: object = time.monotonic
    parent: object = None

    @property
    def remaining(self):
        return max(0.0, self.absolute - self.clock())

    @property
    def expired(self):
        return self.clock() >= self.absolute

    def expired_owner(self):
        if self.parent is not None and self.parent.expired:
            return self.parent.expired_owner()
        return self

    def check(self):
        if self.expired:
            raise DeadlineExceeded(self.expired_owner())

    def child(self, category, cap):
        self.check()
        return Deadline(min(self.absolute, self.clock() + cap), Category(category), self.clock, self)


def current_controller():
    return _controller.get()


def policy():
    owner = current_controller()
    if owner is not None:
        return owner.config
    if _policy_provider is not None:
        return _policy_provider()
    from .config import ObservabilityConfig
    return ObservabilityConfig()


def current_budget():
    owner = current_controller()
    if owner is not None and _cleanup_mode.get():
        return owner.cleanup_budget
    return _budget.get()


def child_budget(category, *, parent=None, config=None):
    category = Category(category)
    owner = current_controller()
    if owner is not None and owner.sealed and not _cleanup_mode.get():
        if owner.cause is not None:
            raise owner.cause
        raise asyncio.CancelledError()
    config = config or policy()
    cap = getattr(config, category.value + '_timeout_seconds')
    parent = current_budget() if parent is None else parent
    return parent.child(category, cap) if parent is not None else Deadline(time.monotonic()+cap, category)


@contextmanager
def bind(budget, controller=None):
    token = _budget.set(budget)
    owner_token = _controller.set(controller) if controller is not None else None
    try:
        yield budget
    finally:
        if owner_token is not None:
            _controller.reset(owner_token)
        _budget.reset(token)


@asynccontextmanager
async def enforce(budget):
    budget.check()
    with bind(budget):
        timeout = asyncio.timeout_at(asyncio.get_running_loop().time()+budget.remaining)
        try:
            async with timeout:
                yield budget
                budget.check()  # a synchronous return must also obey the deadline
        except TimeoutError:
            if timeout.expired():
                owner = budget.expired_owner()
                error = DeadlineExceeded(owner)
                observed('deadline_exceeded', owner.category)
                raise error from None
            raise


@asynccontextmanager
async def boundary(category, *, budget=None):
    async with enforce(budget or child_budget(category)) as value:
        yield value


def operation(category):
    def decorate(fn):
        @wraps(fn)
        async def run(*args, **kwargs):
            async with boundary(category):
                return await fn(*args, **kwargs)
        return run
    return decorate


def check():
    value = current_budget()
    if value is not None:
        value.check()
    owner = current_controller()
    if owner is not None and owner.sealed and not _cleanup_mode.get():
        raise owner.cause or asyncio.CancelledError()


def retry_allowed(backoff=0):
    """Admission only: existing SDK/application owns attempts, not this module."""
    value = current_budget()
    if value is None:
        return
    value.check()
    if value.remaining <= backoff + policy().retry_minimum_seconds:
        observed('retry_denied', value.category)
        raise DeadlineExceeded(value)
    observed('retry', value.category)


def observed(name, category, value=1, status='RUNNING'):
    if name == 'deadline_exceeded':
        owner = current_controller()
        if owner is not None:
            owner.expired_category = Category(category).value
    # Telemetry failure never changes enforcement or causes business replay.
    try:
        from .reliability_metrics import record
        record(name, Category(category), value, status)
    except Exception:
        from .turn_trace import degraded
        owner = current_controller()
        degraded(owner.turn.runtime if owner and owner.turn else None)


@asynccontextmanager
async def cleanup_scope():
    owner = current_controller()
    if owner is not None and (owner.sealed or owner.cleaning):
        budget = owner.start_cleanup()
    else:
        parent = owner.work if owner is not None else current_budget()
        absolute = time.monotonic()+policy().cleanup_timeout_seconds
        if parent is not None and not parent.expired:
            absolute = min(absolute, parent.absolute)
        budget = Deadline(absolute, Category.CLEANUP)
    token = _cleanup_mode.set(True)
    try:
        async with enforce(budget):
            yield
    except DeadlineExceeded:
        if owner is not None:
            owner.cleanup_outcome = 'timeout'
        observed('cleanup_timeout', Category.CLEANUP, status='TIMEOUT')
        if owner is not None:
            owner.emit('reliability.cleanup_timeout', Category.CLEANUP)
        raise
    except BaseException:
        if owner is not None and owner.cleanup_outcome != 'timeout':
            owner.cleanup_outcome = 'failed'
        observed('cleanup_failure', Category.CLEANUP, status='FAILED')
        if owner is not None:
            owner.emit('reliability.cleanup_failed', Category.CLEANUP)
        raise
    finally:
        _cleanup_mode.reset(token)


def cleanup_operation(fn):
    @wraps(fn)
    async def run(*args, **kwargs):
        async with cleanup_scope():
            return await fn(*args, **kwargs)
    return run


async def close_iterator(iterator):
    async with cleanup_scope():
        await iterator.aclose()

@contextmanager
def synchronous_boundary(category):
    """Greenlet-backed SDK waits use the existing owning asyncio task.

Worker threads get pre/post checks and explicit SDK transport caps; this cannot
preempt inline synchronous CPU/I/O and must never be used instead of offloading.
"""
    budget = child_budget(category)
    try:
        task = asyncio.current_task()
    except RuntimeError:
        task = None
    fired = False
    timer = None
    def expire():
        nonlocal fired
        fired = True
        task.cancel()
    with bind(budget):
        try:
            if task is not None:
                timer = asyncio.get_running_loop().call_later(budget.remaining, expire)
            yield budget
            budget.check()
        except asyncio.CancelledError:
            if fired:
                task.uncancel()
                observed('deadline_exceeded', budget.expired_owner().category)
                raise DeadlineExceeded(budget.expired_owner()) from None
            raise
        finally:
            if timer is not None:
                timer.cancel()


def iterator_operation(category):
    """One immutable budget for the complete iterator, never per-yield windows."""
    def decorate(fn):
        @wraps(fn)
        async def run(*args, **kwargs):
            budget = child_budget(category)
            iterator = fn(*args, **kwargs)
            try:
                while True:
                    try:
                        async with enforce(budget):
                            item = await iterator.__anext__()
                    except StopAsyncIteration:
                        return
                    yield item
            finally:
                await close_iterator(iterator)
        return run
    return decorate


def cancellation_cause(exc):
    """Classify deadline-driven cancellation without claiming remote termination."""
    if isinstance(exc, asyncio.CancelledError):
        owner = current_controller()
        if owner is not None and owner.cause is not None:
            return owner.cause
        budget = current_budget()
        if budget is not None and budget.expired:
            return DeadlineExceeded(budget.expired_owner())
    return exc
