"""Abrupt local-process crash fixture. Explicit SQLite URL; no business replay."""
import asyncio
import os
import sys
from pathlib import Path
from datetime import timedelta
from backend.observability.database import Database
from backend.observability.hybrid.config import HybridConfig
from backend.observability.hybrid.capture import Capture
from backend.observability.hybrid.store import OutboxStore
from backend.observability.hybrid.workers import Publisher
from backend.observability.projection import utcnow
from backend.observability.repository import Repository
from backend.tests._m7_storage import config, state

async def main():
    path,mode,wire=sys.argv[1:]
    if not Path(path).is_absolute() or mode not in ('commit','publish'):raise ValueError('Local fixture input')
    db=Database('sqlite+aiosqlite:///'+path,isolated_test=True)
    run=state();await Repository(db.sessions,config(),audit_capture=Capture()).persist(run)
    if mode=='publish':
        class TestSpool:
            async def publish(self,data):Path(wire).write_bytes(data)
        def crash():os._exit(23)
        await Publisher(OutboxStore(db.sessions,HybridConfig(environment='development')),TestSpool()).tick(
            utcnow(),after_publish=crash)
    os._exit(23)

asyncio.run(main())
