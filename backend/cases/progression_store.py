"""Case-owned, versioned persistence of the authoritative TroubleshootingProgression.

    read  (progression, version N)
    write UPDATE ... SET version = N + 1 WHERE case_id = ? AND version = N
          -> 0 rows: ProgressionConflict (a newer progression exists; nothing is overwritten)
    first write: INSERT version 1 (a concurrent first write loses with ProgressionConflict)

Case scope is resolved ONLY from the authoritative session->case link table
(`slopanoc_case_session_links`), never from the session-state `active_case_id` hint.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from functools import lru_cache
from typing import Optional

from sqlalchemy import update
from sqlalchemy.exc import IntegrityError

from backend.cases.db import CaseDatabase, get_case_database
from backend.cases.models import CaseSessionLinkRecord, CaseTroubleshootingProgressionRecord
from backend.cases.troubleshooting_progression import SCHEMA_VERSION, TroubleshootingProgression


class ProgressionConflict(Exception):
    """The Case progression changed since it was read: the stale update was not applied."""

    def __init__(self, case_id: str, expected_version: Optional[int], current_version: Optional[int]) -> None:
        super().__init__(f"case {case_id} progression is at version {current_version}, not {expected_version}")
        self.case_id = case_id
        self.expected_version = expected_version
        self.current_version = current_version


@dataclass(frozen=True)
class StoredProgression:
    progression: TroubleshootingProgression
    version: int


class CaseProgressionStore:
    def __init__(self, database: Optional[CaseDatabase] = None) -> None:
        self._db = database or get_case_database()

    async def linked_case_id(self, session_id: Optional[str]) -> Optional[str]:
        """The Case this session is linked to, from the authoritative link table (or None)."""
        if not session_id:
            return None
        await self._db.ensure_schema()
        async with self._db.session() as session:
            link = await session.get(CaseSessionLinkRecord, session_id)
            return link.case_id if link is not None else None

    async def load(self, case_id: str) -> Optional[StoredProgression]:
        await self._db.ensure_schema()
        async with self._db.session() as session:
            record = await session.get(CaseTroubleshootingProgressionRecord, case_id)
            if record is None:
                return None
            return StoredProgression(TroubleshootingProgression.model_validate(record.progression), record.version)

    async def save(
        self, case_id: str, progression: TroubleshootingProgression, expected_version: Optional[int], session_id: Optional[str] = None
    ) -> int:
        """Compare-and-swap. `expected_version=None` means "create"; returns the new version."""
        await self._db.ensure_schema()
        now = datetime.now(timezone.utc)
        document = progression.model_dump(mode="json")
        async with self._db.session() as session:
            if expected_version is None:
                session.add(
                    CaseTroubleshootingProgressionRecord(
                        case_id=case_id, version=1, schema_version=SCHEMA_VERSION, progression=document,
                        created_at=now, updated_at=now, updated_by_session_id=session_id,
                    )
                )
                try:
                    await session.commit()
                except IntegrityError:
                    await session.rollback()
                    current = await session.get(CaseTroubleshootingProgressionRecord, case_id)
                    raise ProgressionConflict(case_id, None, current.version if current else None) from None
                return 1
            result = await session.execute(
                update(CaseTroubleshootingProgressionRecord)
                .where(CaseTroubleshootingProgressionRecord.case_id == case_id)
                .where(CaseTroubleshootingProgressionRecord.version == expected_version)
                .values(version=expected_version + 1, schema_version=SCHEMA_VERSION, progression=document,
                        updated_at=now, updated_by_session_id=session_id)
            )
            if result.rowcount != 1:
                await session.rollback()
                current = await session.get(CaseTroubleshootingProgressionRecord, case_id)
                raise ProgressionConflict(case_id, expected_version, current.version if current else None)
            await session.commit()
            return expected_version + 1


@lru_cache(maxsize=1)
def get_case_progression_store() -> CaseProgressionStore:
    return CaseProgressionStore(get_case_database())
