"""Bounded local at-least-once transport; replaceable without cloud SDKs."""
import asyncio
import time
from dataclasses import dataclass
from typing import Protocol
from .contracts import MAX_EVENT_BYTES, AuditEvent, decode_event
from .capture import RUNTIME_TYPES

@dataclass
class Delivery:
    data: bytes
    attempts: int = 0
    available_at: float = 0

class EventTransport(Protocol):
    async def publish(self, data: bytes) -> None: ...

class DeliveryTransport(EventTransport, Protocol):
    async def receive(self) -> Delivery | None: ...
    async def ack(self, delivery: Delivery) -> None: ...
    async def nack(self, delivery: Delivery) -> None: ...

class FakeEventTransport:
    def __init__(self, capacity=128, maximum_attempts=10, *, retry_initial=1,
                 retry_max=60, clock=time.monotonic, environment='local'):
        if not 1 <= capacity <= 4096 or not 1 <= maximum_attempts <= 100:
            raise ValueError('Transport bounds')
        if not 0 < retry_initial <= retry_max <= 300: raise ValueError('Retry bounds')
        if environment not in ('local','development','test'): raise ValueError('Local environment required')
        self.environment = environment
        self.queue = asyncio.Queue(maxsize=capacity)
        self.dead = 0
        self.maximum_attempts = maximum_attempts
        self.retry_initial, self.retry_max, self.clock = retry_initial, retry_max, clock

    async def publish(self, data):
        if type(data) is not bytes or len(data) > MAX_EVENT_BYTES: raise ValueError('Event bound')
        event = decode_event(data, environment=self.environment)
        if not isinstance(event, AuditEvent) or event.event_type not in RUNTIME_TYPES:
            raise ValueError('Canonical audit required')
        self.queue.put_nowait(Delivery(event.model_dump_json().encode()))

    async def receive(self):
        # At most capacity checks; delayed messages do not starve newer deliveries.
        for _ in range(self.queue.qsize()):
            delivery = self.queue.get_nowait()
            if delivery.available_at <= self.clock(): return delivery
            self.queue.task_done()
            self.queue.put_nowait(delivery)
        return None

    async def ack(self, delivery): self.queue.task_done()

    async def nack(self, delivery):
        self.queue.task_done()
        delivery.attempts += 1
        if delivery.attempts >= self.maximum_attempts:
            self.dead += 1
            return
        delivery.available_at = self.clock() + min(self.retry_max,
            self.retry_initial * 2 ** min(delivery.attempts - 1, 16))
        try: self.queue.put_nowait(delivery)
        except asyncio.QueueFull: self.dead += 1
