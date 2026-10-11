"""Lazy bounded Google GAPIC adapter; only publish, never resource administration."""
import asyncio
from .contracts import AuditEvent, decode_event
from .capture import RUNTIME_TYPES

class GooglePubSubAuditTransport:
    def __init__(self, config, *, client_factory=None):
        if not config.audit_publication_enabled or not config.audit_topic:
            raise ValueError('Explicit publication binding required')
        self.config=config
        self.client_factory=client_factory
        self.client=None
        self.slots=asyncio.Semaphore(config.publish_concurrency)
        self.closed=False

    async def publish(self, data):
        event=decode_event(data,environment=self.config.environment)
        if not isinstance(event,AuditEvent) or event.event_type not in RUNTIME_TYPES:
            raise ValueError('Canonical audit required')
        if self.closed or self.slots.locked():raise RuntimeError('Transport capacity unavailable')
        async with self.slots:
            if self.client is None:
                if self.client_factory is None:
                    from google.pubsub_v1.services.publisher import PublisherAsyncClient
                    self.client=PublisherAsyncClient()
                else:self.client=self.client_factory()
            from google.api_core.retry import AsyncRetry, if_transient_error
            attributes={'event_id':str(event.event_id),'schema_version':'1',
                'event_type':event.event_type,'environment':event.environment}
            for field in ('run_id','turn_id','trace_id'):
                value=getattr(event,field)
                if value is not None:attributes[field]=str(value)
            # No arbitrary attributes or content; all values come from the strict envelope.
            async with asyncio.timeout(self.config.publish_timeout_seconds):
                await self.client.publish(request={'topic':self.config.audit_topic,
                    'messages':[{'data':event.model_dump_json(warnings='none').encode(),
                        'attributes':attributes}]},timeout=self.config.publish_timeout_seconds,
                    retry=AsyncRetry(predicate=if_transient_error,initial=.1,maximum=.5,
                        timeout=self.config.publish_timeout_seconds))

    async def close(self):
        self.closed=True
        if self.client is not None:
            async with asyncio.timeout(self.config.shutdown_seconds):
                await self.client.transport.close()
