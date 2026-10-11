"""Separately instantiated bounded consumers. Never execute business operations."""
import asyncio
from .contracts import AuditEvent, decode_event, event_digest
from .capture import RUNTIME_TYPES

class Publisher:
    def __init__(self,store,transport):self.store,self.transport=store,transport;self.running=True;self.task=None
    async def tick(self,now=None,*,after_publish=None):
        claims=await self.store.claim(now);published=0
        # One network call at a time; bounded batch. No SQL lock spans transport.
        for event,token,digest in claims:
            try:
                if event_digest(event)!=digest:raise ValueError('Event integrity')
                async with asyncio.timeout(min(2,self.store.config.publisher_lease_seconds/2)):
                    await self.transport.publish(event.model_dump_json().encode())
            except (ValueError,TypeError):
                await self.store.settle(event.event_id,token,success=False,now=now,code='INVALID');continue
            except Exception:
                await self.store.settle(event.event_id,token,success=False,now=now);continue
            if after_publish is not None: after_publish()  # isolated crash-injection seam
            if await self.store.settle(event.event_id,token,success=True,now=now):published+=1
        return published
    async def run(self):
        while self.running:
            try:
                await self.tick()
                delay=.1
            except Exception:
                # Preserve durable debt and retry the worker, never log DB details.
                delay=self.store.config.retry_initial_seconds
            await asyncio.sleep(delay)
    def start(self):
        if self.task is not None:raise ValueError('Publisher already started')
        self.task=asyncio.create_task(self.run())
    async def close(self):
        self.running=False
        if self.task is not None:
            try:
                async with asyncio.timeout(self.store.config.shutdown_seconds):await self.task
            except TimeoutError:
                self.task.cancel();await asyncio.gather(self.task,return_exceptions=True)

class Projector:
    def __init__(self,store):
        self.store=store
        self.slots=asyncio.Semaphore(store.config.projector_max_inflight)
        self.active=set()
        self.closed=False

    async def record_failure(self,code,key='invalid',now=None):
        try:
            async with asyncio.timeout(self.store.config.projector_deadline_seconds):
                return await self.store.failure(code,key,now)
        except Exception:
            # DB unavailable: cannot invent a durable quarantine/ACK.
            return None

    async def consume(self,data,now=None):
        if self.closed or self.slots.locked():return 'RETRY'
        task=asyncio.current_task()
        async with self.slots:
            self.active.add(task)
            event=None
            try:
                async with asyncio.timeout(self.store.config.projector_deadline_seconds):
                    event=decode_event(data,environment=self.store.config.environment)
                    if not isinstance(event,AuditEvent) or event.event_type not in RUNTIME_TYPES:
                        raise ValueError('No canonical owner')
                    result=await self.store.apply(event,now)
                if result=='CONFLICT':
                    if await self.record_failure('CONFLICT',str(event.event_id),now) is None:return 'RETRY'
                return result
            except (ValueError,TypeError):
                recorded=await self.record_failure('INVALID',now=now)
                return 'INVALID' if recorded is not None else 'RETRY'
            except Exception:
                state=await self.record_failure('PROCESSING_FAILED',str(event.event_id) if event else 'invalid',now)
                return 'DEAD' if state=='DEAD' else 'RETRY'
            finally:self.active.discard(task)

    async def tick(self,transport,now=None):
        count=0
        for _ in range(self.store.config.publisher_batch_size):
            if self.closed:break
            delivery=await transport.receive()
            if delivery is None:break
            result=await self.consume(delivery.data,now)
            if result=='RETRY':await transport.nack(delivery)
            else:await transport.ack(delivery)
            count+=1
        return count

    async def close(self):
        self.closed=True
        active=self.active-{asyncio.current_task()}
        if active:
            _,pending=await asyncio.wait(active,timeout=self.store.config.shutdown_seconds)
            for task in pending:task.cancel()
            await asyncio.gather(*pending,return_exceptions=True)
