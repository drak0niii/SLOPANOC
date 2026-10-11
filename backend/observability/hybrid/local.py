"""Explicit local composition; no schema creation, env discovery or cloud clients.

Callers provide an already-migrated isolated database and opt in to capture.
The normal ANOC and reader startup remain inert until deliberately wired.
"""
from dataclasses import dataclass
from ..repository import Repository as RunRepository
from ..finops.repository import Repository as AccountingRepository
from .capture import Capture
from .store import OutboxStore, ProjectorStore
from .transport import FakeEventTransport
from .workers import Publisher, Projector

@dataclass
class LocalPipeline:
    run_repository: object
    accounting_repository: object
    publisher: Publisher
    projector: Projector
    transport: FakeEventTransport

    async def tick(self, now=None):
        published = await self.publisher.tick(now)
        processed = await self.projector.tick(self.transport, now)
        return published, processed

    async def close(self):
        await self.publisher.close()
        await self.projector.close()


def compose(database, observability_config, hybrid_config, *, isolated_local=False):
    if not isolated_local or database.engine.url.get_backend_name() != 'sqlite' or hybrid_config.environment not in ('local','test','development'):
        raise ValueError('Explicit isolated local database required')
    if not hybrid_config.capture_enabled: raise ValueError('Explicit local capture opt-in required')
    capture = Capture()
    transport = FakeEventTransport(hybrid_config.transport_queue_depth,
        hybrid_config.publisher_max_attempts, retry_initial=hybrid_config.retry_initial_seconds,
        retry_max=hybrid_config.retry_max_seconds, environment=hybrid_config.environment)
    return LocalPipeline(RunRepository(database.sessions,observability_config,audit_capture=capture),
        AccountingRepository(database.sessions,audit_capture=capture),
        Publisher(OutboxStore(database.sessions,hybrid_config),transport),
        Projector(ProjectorStore(database.sessions,hybrid_config)),transport)
