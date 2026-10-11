"""Replaceable local/runtime ports. No client creation or business execution."""
from typing import Protocol
from .contracts import AuditEvent

class AuditCapture(Protocol):
    async def append(self,session,event:AuditEvent) -> bool:
        """Caller-owned authoritative SQL transaction; never commits/publishes."""
        ...

from .transport import EventTransport, DeliveryTransport

class PublisherStore(Protocol):
    config: object
    async def claim(self,now=None) -> list[tuple[AuditEvent,str,str]]: ...
    async def settle(self,event_id,token,*,success,now=None,code='TRANSPORT_FAILED') -> bool: ...

class ProjectorStore(Protocol):
    config: object
    async def apply(self,event:AuditEvent,now=None) -> str:
        """Dedup receipt and projection mutation commit/rollback together."""
        ...
    async def failure(self,code,key='invalid',now=None) -> str: ...
