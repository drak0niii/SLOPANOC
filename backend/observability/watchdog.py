"""One auxiliary monitor of the canonical M2 turn; no business execution."""
import asyncio
import time
from collections import deque
from .deadlines import Deadline, DeadlineExceeded, observed, bind
from .reliability_contract import Category
from .stages import RunStatus, Stage, TERMINAL_STATUSES

class Watchdog:
    def __init__(self, turn, config, *, clock=time.monotonic):
        self.turn, self.config, self.clock = turn, config, clock
        self.started = self.last_progress = clock()
        self.total = Deadline(self.started+config.turn_timeout_seconds, Category.TURN, clock)
        self.work = Deadline(self.total.absolute-config.cleanup_timeout_seconds, Category.TURN, clock)
        self.cleanup_budget = None
        self.cleanup_started = None
        self.cleanup_outcome = 'pending'
        self.cleaning = self.sealed = False
        self.cause = None
        self.expired_category = None
        self.stalled_at = None
        self.events = deque(maxlen=128)
        self.changed = asyncio.Event()
        self.task = self.driver = None
        self.timer = None
        self.cleanup_timer = None
        self.wakeups = 0
        self.warned = False
        self.children = set()
        self.workers = deque(maxlen=128)

    def emit(self, name, category=Category.TURN, **attrs):
        self.events.append((name, self.clock(), category.value))
        if self.turn is not None and name.startswith('reliability.cleanup_'):
            from .turn_trace import notify
            notify(self.turn, 'project', 'cleanup')
        if self.turn is not None:
            try:
                from .reliability_contract import project
                safe = project({'slopanoc.deadline_category':category.value, **attrs})
                self.turn._root.add_event(name, safe)
            except Exception:
                from .turn_trace import degraded
                degraded(self.turn.runtime)

    def material(self):
        if self.sealed:
            return
        now = self.clock()
        if self.stalled_at is not None:
            observed('stall_duration', Category.TURN, now-self.stalled_at)
            observed('progress_resumed', Category.TURN)
            self.emit('reliability.progress_resumed')
            self.stalled_at = None
            if self.turn is not None and self.turn.status not in TERMINAL_STATUSES:
                self.turn.status = RunStatus.RUNNING
                from .turn_trace import notify
                notify(self.turn, 'project', 'resumed')
        self.last_progress = now
        self.changed.set()

    def poll(self):
        if self.sealed or (self.turn is not None and self.turn.status in TERMINAL_STATUSES):
            return
        now = self.clock()
        if self.stalled_at is None and now-self.last_progress >= self.config.watchdog_stall_seconds:
            self.stalled_at = now
            if self.turn is not None:
                self.turn.status = RunStatus.STALLED
                from .turn_trace import notify
                notify(self.turn, 'project', 'stalled')
            self.emit(Stage.RUN_STALLED.value)
            observed('stall', Category.TURN, status='STALLED')
        if not self.warned and now >= self.started + (self.work.absolute-self.started)*.8:
            self.warned = True
            self.emit('reliability.deadline_nearing')
        if self.work.expired:
            self.expire()

    def expire(self):
        if self.sealed:
            return
        self.cause = DeadlineExceeded(self.work)
        observed('deadline_exceeded', Category.TURN, status='TIMEOUT')
        observed('deadline_overshoot', Category.TURN, max(0, self.clock()-self.work.absolute))
        self.sealed = True
        self.emit('reliability.deadline_exceeded')
        self.start_cleanup()
        for task in tuple(self.children):
            task.cancel()
        if self.driver is not None and not self.driver.done():
            self.driver.cancel()

    def start_cleanup(self):
        if self.cleanup_budget is None:
            self.cleanup_started = self.clock()
            self.cleaning = True
            self.cleanup_outcome = 'running'
            self.cleanup_budget = Deadline(min(self.total.absolute,
                self.clock()+self.config.cleanup_timeout_seconds), Category.CLEANUP, self.clock)
            self.emit('reliability.cleanup_started', Category.CLEANUP)
            try:
                loop = asyncio.get_running_loop()
            except RuntimeError:
                pass  # deterministic synchronous clock tests have no scheduler
            else:
                self.cleanup_timer = loop.call_later(self.cleanup_budget.remaining, self.expire_cleanup)
        return self.cleanup_budget

    def expire_cleanup(self):
        # Generator unwinding may begin before a cleanup context is reached.
        # An independent second cancellation bounds that unwinding too.
        self.cleanup_outcome = 'timeout'
        observed('cleanup_timeout', Category.CLEANUP, status='TIMEOUT')
        self.emit('reliability.cleanup_timeout', Category.CLEANUP)
        if self.driver is not None and not self.driver.done():
            self.driver.cancel()

    def seal(self):
        self.sealed = True
        for task in tuple(self.children):
            task.cancel()
        return self.start_cleanup()

    def track(self, task):
        self.children.add(task)
        task.add_done_callback(self.children.discard)
        return task

    def start(self, driver):
        self.driver = driver
        loop = asyncio.get_running_loop()
        # Independent enforcement survives a failing monitor/telemetry poll.
        self.timer = loop.call_later(self.work.remaining, self.expire)
        self.task = asyncio.create_task(self.run())

    async def run(self):
        try:
            while not self.sealed:
                self.poll()
                if self.sealed:
                    break
                self.changed.clear()
                next_stall = self.last_progress+self.config.watchdog_stall_seconds
                delay = min(self.config.watchdog_check_seconds, self.work.remaining,
                    max(0.001, next_stall-self.clock()) if self.stalled_at is None else self.config.watchdog_check_seconds)
                try:
                    await asyncio.wait_for(self.changed.wait(), delay)
                except TimeoutError:
                    pass
                self.wakeups += 1
        except asyncio.CancelledError:
            raise
        except Exception:
            observed('controller_failure', Category.TURN, status='FAILED')
            self.emit('reliability.controller_failed')
            # The independent timer remains armed; protection is not disabled.

    async def stop(self):
        self.sealed = True
        if self.timer:
            self.timer.cancel()
        if self.cleanup_timer:
            self.cleanup_timer.cancel()
        if self.task:
            self.task.cancel()
            try:
                await self.task
            except asyncio.CancelledError:
                pass
        for task in tuple(self.children):
            task.cancel()
        if self.children:
            from .deadlines import enforce
            async with enforce(self.start_cleanup()):
                await asyncio.gather(*tuple(self.children), return_exceptions=True)
        if self.stalled_at is not None:
            observed('stall_duration', Category.TURN, self.clock()-self.stalled_at)
            self.stalled_at = None
        if self.cleanup_started is not None:
            observed('cleanup_duration', Category.CLEANUP, max(0,self.clock()-self.cleanup_started))
        if self.cleanup_outcome == 'running':
            self.cleanup_outcome = 'completed'
            self.emit('reliability.cleanup_completed', Category.CLEANUP)
