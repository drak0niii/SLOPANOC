"""Count/byte bounded business transport with independent completion signaling."""
import asyncio
import json
from .deadlines import boundary, policy, observed
from .reliability_contract import Category

class Backpressure(RuntimeError):
    def __init__(self):
        super().__init__('delivery_capacity_exhausted')

class DeliveryBuffer:
    def __init__(self, *, capacity=None, byte_limit=None):
        config=policy()
        self.capacity=capacity or config.business_queue_capacity
        self.byte_limit=byte_limit or config.business_queue_bytes
        self.queue=asyncio.Queue(maxsize=self.capacity)
        self.bytes=0
        self.changed=asyncio.Event()
        self.done=asyncio.Event()
        self.error=None
        self.urgent=False
        self.detached=False

    @staticmethod
    def size(item):
        if hasattr(item, 'model_dump'):
            value = item.model_dump(mode='json')
        elif hasattr(item, '__dict__'):
            value = vars(item)
        else:
            value = item
        return len(json.dumps(value,ensure_ascii=False,separators=(',',':'),default=lambda value: vars(value) if hasattr(value,'__dict__') else None).encode('utf-8'))

    async def put(self,item):
        if self.detached:
            return False
        size=self.size(item)
        if size > self.byte_limit:
            self.fail(Backpressure())
            raise self.error
        try:
            async with boundary(Category.QUEUE):
                while self.queue.full() or self.bytes+size > self.byte_limit:
                    self.changed.clear()
                    await self.changed.wait()
                    if self.detached:
                        return False
                self.queue.put_nowait((item,size))
                self.bytes+=size
                self.changed.set()
        except TimeoutError:
            self.fail(Backpressure())
            raise self.error from None
        return True

    def fail(self,error):
        self.urgent=True
        self.error=error
        self.done.set()
        self.changed.set()
        observed('queue_saturation',Category.QUEUE,status='FAILED')

    def finish(self,error=None):
        if self.error is None:
            self.error=error
        self.done.set()
        self.changed.set()

    def detach(self):
        self.detached=True
        while not self.queue.empty():
            self.queue.get_nowait()
        self.bytes=0
        self.changed.set()

    async def get(self, *, heartbeat=None):
        while True:
            if self.error is not None and self.urgent:
                raise self.error
            if not self.queue.empty():
                item,size=self.queue.get_nowait()
                self.bytes-=size
                self.changed.set()
                return item
            if self.error is not None:
                raise self.error
            if self.done.is_set():
                return None
            self.changed.clear()
            if heartbeat is None:
                await self.changed.wait()
            else:
                try:
                    await asyncio.wait_for(self.changed.wait(), heartbeat)
                except TimeoutError:
                    return HEARTBEAT

HEARTBEAT=object()

from starlette.responses import StreamingResponse
from .deadlines import DeadlineExceeded, close_iterator

class ReliableStreamingResponse(StreamingResponse):
    """ASGI delivery deadline does not cancel the independently owned backend."""
    async def stream_response(self, send):
        async def bounded_send(message):
            async with boundary(Category.DELIVERY):
                await send(message)
        try:
            await super().stream_response(bounded_send)
        except DeadlineExceeded:
            raise OSError('sse_delivery_timeout') from None
        finally:
            await close_iterator(self.body_iterator)
