"""Bounded asynchronous projection, no turn replay or crash-durable delivery claim."""
import asyncio
import time
import random
from dataclasses import dataclass
from threading import RLock
from uuid import uuid4
from weakref import WeakValueDictionary
from contextvars import Context
from .projection import RunState, utcnow
from .persistence_metrics import ProjectionHealth
from .stages import TERMINAL_STATUSES


@dataclass
class Slot:
    state: RunState
    due: float
    attempts: int = 0
    retry_until: float | None = None


class Coordinator:
    def __init__(self, repository, config, *, instance_id=None):
        self.repository, self.config = repository, config
        self.instance_id = instance_id or str(uuid4())
        self.config_version = config.effective_configuration()[0].config_version
        self.health = ProjectionHealth()
        self.lock = RLock()
        self.slots = {}
        self.admitted = set()
        self.publishers = WeakValueDictionary()
        self.last_write = {}
        self.loop = None
        self.task = None
        self.closed = False
        self.stopping = False
        self.wake = None
        self._wake_pending = False
        self.inflight = None

    def start(self):
        self.loop = asyncio.get_running_loop()
        self.wake = asyncio.Event()
        # Detached context prevents self-observation as a user dependency.
        self.task = self.loop.create_task(self.run(), context=Context())

    def attach(self, turn):
        from .projection import Publisher
        with self.lock:
            if self.closed or self.stopping or len(self.publishers) >= self.config.projection_capacity or len(self.admitted) >= self.config.projection_capacity:
                self.health.add('rejected')
                return None
            p = Publisher(turn,self)
            self.publishers[turn.run_id] = p
            return p

    def _signal(self):
        with self.lock:
            self._wake_pending = False
            if self.wake:
                self.wake.set()

    def submit(self, state):
        if self.closed or self.stopping:
            self.health.add('rejected')
            return False
        # RunState is frozen and was validated centrally; repository revalidates sink.
        with self.lock:
            old = self.slots.get(state.run_id)
            if old and (old.state.state_version >= state.state_version or
                        old.state.status in TERMINAL_STATUSES and state.status not in TERMINAL_STATUSES):
                return False
            if old and (old.state.producer_instance_id != state.producer_instance_id or
                old.state.status in TERMINAL_STATUSES and state.status in TERMINAL_STATUSES and
                any(getattr(old.state,k) != getattr(state,k) for k in
                    ('status','terminal_at','started_at','error_code','error_stage','elapsed_ms','current_stage','trace_id','root_span_id','turn_id','turn_id_origin'))):
                self.health.add('invalid')
                return False
            if old:
                self.health.add('coalesced')
            if state.run_id not in self.admitted and len(self.admitted) >= self.config.projection_capacity:
                self.health.add('rejected')
                if state.status in TERMINAL_STATUSES:
                    self.health.add('terminal_unpersisted')
                return False
            self.admitted.add(state.run_id)
            due = self.last_write.get(state.run_id, 0) + self.config.projection_write_spacing_seconds
            if state.status in TERMINAL_STATUSES:
                due = 0
            retry_until = old.retry_until if old else None
            if state.status in TERMINAL_STATUSES and retry_until is None:
                retry_until = time.monotonic()+self.config.projection_retry_seconds
            self.slots[state.run_id] = Slot(state, due, old.attempts if old else 0, retry_until)
            self.health.add('accepted')
        with self.lock:
            schedule = self.loop and not self.loop.is_closed() and not self._wake_pending and not self.wake.is_set()
            if schedule:
                self._wake_pending = True
        if schedule:
            self.loop.call_soon_threadsafe(self._signal, context=Context())
        return True

    async def write_one(self, identity, slot):
        self.health.add('attempts')
        try:
            remaining = max(0,slot.retry_until-time.monotonic()) if slot.retry_until is not None else self.config.projection_attempt_seconds
            async with asyncio.timeout(min(self.config.projection_attempt_seconds,remaining)):
                result = await self.repository.persist(slot.state)
            if result in ('persisted','duplicate','stale'):
                self.health.add('persisted')
                with self.lock:
                    self.last_write[identity] = time.monotonic()
                    if self.slots.get(identity) is slot:
                        self.slots.pop(identity)
                    newer = self.slots.get(identity)
                    if newer and newer.state.status not in TERMINAL_STATUSES:
                        newer.due = self.last_write[identity]+self.config.projection_write_spacing_seconds
                    if slot.state.status in TERMINAL_STATUSES and newer is None:
                        self.last_write.pop(identity,None)
                        self.admitted.discard(identity)
                return
            self.health.add('invalid')
        except asyncio.CancelledError:
            raise
        except Exception:
            self.health.add('failed')
        with self.lock:
            self.last_write[identity] = time.monotonic()
            latest = self.slots.get(identity)
            if latest is not slot:
                if latest and latest.state.status not in TERMINAL_STATUSES:
                    latest.due = self.last_write[identity]+self.config.projection_write_spacing_seconds
                return
            slot.attempts += 1
            now = time.monotonic()
            slot.retry_until = slot.retry_until or now+self.config.projection_retry_seconds
            if slot.state.status in TERMINAL_STATUSES and slot.attempts < self.config.projection_terminal_attempts and now < slot.retry_until:
                slot.due = now+min(.1*2**(slot.attempts-1)+random.uniform(0,.05),max(0,slot.retry_until-now))
                self.health.add('retries')
            else:
                self.slots.pop(identity,None)
                if slot.state.status in TERMINAL_STATUSES:
                    self.last_write.pop(identity,None)
                    self.admitted.discard(identity)
                    self.health.add('terminal_unpersisted')

    async def run(self):
        checkpoint = time.monotonic()+self.config.projection_checkpoint_seconds
        while not self.closed:
            now = time.monotonic()
            if now >= checkpoint and not self.stopping:
                with self.lock:
                    publishers = list(self.publishers.values())
                for publisher in publishers:
                    try:
                        publisher.publish(checkpoint=True)
                    except Exception:
                        self.health.add('invalid')
                checkpoint = now+self.config.projection_checkpoint_seconds
                live = set(self.publishers)
                with self.lock:
                    for key in list(self.admitted):
                        if key not in live and key not in self.slots:
                            self.last_write.pop(key,None)
                            self.admitted.discard(key)
            with self.lock:
                self.wake.clear()
                ready = [(key,slot) for key,slot in self.slots.items() if slot.due<=now or self.stopping]
                ready.sort(key=lambda item: item[1].state.status not in TERMINAL_STATUSES)
                next_due = min((s.due for s in self.slots.values()), default=checkpoint)
            if ready:
                key,slot = ready[0]
                self.inflight = slot
                try:
                    await self.write_one(key,slot)
                finally:
                    self.inflight = None
                continue
            try:
                await asyncio.wait_for(self.wake.wait(),max(.001,min(checkpoint,next_due)-time.monotonic()))
            except TimeoutError:
                pass

    async def flush(self, seconds):
        deadline = time.monotonic()+seconds
        if self.wake:
            self.wake.set()
        while self.slots or self.inflight:
            if time.monotonic()>=deadline:
                return False
            await asyncio.sleep(min(.005,max(0,deadline-time.monotonic())))
        return True

    async def close(self, seconds=None):
        budget = self.config.projection_shutdown_seconds if seconds is None else seconds
        self.stopping = True
        deadline = time.monotonic()+budget
        ok = await self.flush(max(0,budget-.05))
        if not ok:
            self.health.add('shutdown_incomplete')
            with self.lock:
                pending = sum(s.state.status in TERMINAL_STATUSES for s in self.slots.values())
            if pending:
                self.health.add('terminal_unpersisted',pending)
        self.closed = True
        if self.task:
            self.task.cancel()
            # Cancellation-cooperative SQLAlchemy/async drivers; do not wait forever.
            await asyncio.wait({self.task},timeout=max(0,deadline-time.monotonic()))
        with self.lock:
            self.slots.clear()
            self.last_write.clear()
            self.admitted.clear()
        self.publishers.clear()
        return ok

_default = None
_default_lock = RLock()


def current_coordinator():
    with _default_lock:
        return _default


def install_coordinator(coordinator):
    global _default
    with _default_lock:
        if _default is not None and _default is not coordinator:
            raise RuntimeError('Projection ownership conflict')
        _default = coordinator


def release_coordinator(coordinator):
    global _default
    with _default_lock:
        if _default is coordinator:
            _default = None
