"""Explicit disposable local DB helpers; never inherit a configured runtime URL."""
from datetime import timedelta
from uuid import uuid4
from backend.observability.config import ObservabilityConfig
from backend.observability.projection import RunState, DiagnosticEvent, utcnow
from backend.observability.stages import RunStatus, Stage, TERMINAL_STATUSES
from backend.observability.database import Database
from backend.observability.repository import Repository
from backend.observability.models import Base


def config(**changes):
    defaults=dict(projection_enabled=True,projection_write_spacing_seconds=.01,
        projection_checkpoint_seconds=.05,projection_attempt_seconds=.2,
        projection_shutdown_seconds=.3,projection_retry_seconds=.5)
    defaults.update(changes)
    return ObservabilityConfig(**defaults)


def state(**changes):
    now=utcnow()
    data=dict(run_id=str(uuid4()),producer_instance_id=str(uuid4()),state_version=1,
        session_id='session-1',environment='development',status=RunStatus.RUNNING,
        current_stage=Stage.REQUEST_RECEIVED,started_at=now,stage_started_at=now,
        last_progress_at=now,heartbeat_at=now,observed_at=now,elapsed_ms=0)
    data.update(changes)
    if data['status'] in TERMINAL_STATUSES:
        data.setdefault('terminal_at',now)
        data['current_stage']={RunStatus.COMPLETED:Stage.TURN_COMPLETED,RunStatus.FAILED:Stage.TURN_FAILED,
            RunStatus.TIMEOUT:Stage.TURN_TIMEOUT,RunStatus.CANCELLED:Stage.TURN_CANCELLED}[data['status']]
    if 'events' not in data:
        data['events']=(DiagnosticEvent(event_seq=1,state_version=data['state_version'],event_type='terminal' if data['status'] in TERMINAL_STATUSES else 'started',
            timestamp=now,elapsed_ms=0,stage=data['current_stage'],status=data['status']),)
    return RunState(**data)


def change(original,**values):
    data=original.model_dump(mode='python')
    data.update(values)
    return RunState(**data)


async def storage(path, cfg=None):
    database=Database('sqlite+aiosqlite:///'+str(path),isolated_test=True)
    async with database.engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    return database,Repository(database.sessions,cfg or config())


async def close_chat_storage(service):
    """Close the known disposable case/attachment stores owned by test ChatService."""
    await service._case_service._db.close()
    await service._attachment_service._repository.close()
