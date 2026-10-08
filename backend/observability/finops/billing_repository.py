"""Durable CAS publication: no transaction spans source I/O; previous truth survives failure."""
from datetime import datetime,timezone
from sqlalchemy import select,update
from .billing_models import StateRow,PublicationRow,CacheRow
from .billing_contracts import digest,SourceError, instant
from .repository import insert_for

def now():return datetime.now(timezone.utc)

class Repository:
    def __init__(self,sessions):self.sessions=sessions

    async def reserve(self,environment,adapter,window):
        key=digest([environment,adapter.source,adapter.scope,window.partition])
        async with self.sessions() as s,s.begin():
            await s.execute(insert_for(s,StateRow).values(key=key,environment=environment,source=adapter.source,
                scope=adapter.scope,partition=window.partition,mode=adapter.mode,revision=0).on_conflict_do_nothing())
            # Monotonic revision is a durable fencing token, not a held DB lock.
            result=await s.execute(update(StateRow).where(StateRow.key==key).values(revision=StateRow.revision+1,
                last_attempt=now()).returning(StateRow.revision))
            return key,result.scalar_one()

    async def fail(self,key,revision,error):
        if error not in ('SOURCE_UNAVAILABLE','SCHEMA_INCOMPATIBLE','INCOMPLETE_GENERATION','ROW_LIMIT','GROUP_LIMIT',
            'PRICE_CACHE_LIMIT','INVALID_DECIMAL','OUTSIDE_WINDOW','HASH_COLLISION','INVALID_TIER','PUBLICATION_FAILED'):
            error='SCHEMA_INCOMPATIBLE'
        async with self.sessions() as s,s.begin():
            await s.execute(update(StateRow).where(StateRow.key==key,StateRow.revision==revision).values(error=error))

    async def publish(self,key,revision,extraction,projection):
        generation=digest([key,projection.fingerprint,extraction.schema_version])
        async with self.sessions() as s,s.begin():
            # Acquire the write lock through CAS before reading (also avoids SQLite
            # read-to-write transaction upgrade races). Fence and pointer commit together.
            fenced=await s.execute(update(StateRow).where(StateRow.key==key,StateRow.revision==revision).values(last_success=now()))
            if fenced.rowcount!=1:return 'SUPERSEDED'
            state=(await s.execute(select(StateRow).where(StateRow.key==key).with_for_update())).scalar_one()
            # An older extraction cannot rewind publication even if acquired later.
            previous=await s.get(PublicationRow,state.generation) if state.generation else None
            if previous and instant(previous.extracted_at)>instant(extraction.extracted_at):return 'SUPERSEDED'
            existing=await s.get(PublicationRow,generation)
            if existing is not None:
                if existing.controls!=list(projection.controls) or existing.row_count!=projection.rows:
                    raise SourceError('HASH_COLLISION')
            else:
                s.add(PublicationRow(generation=generation,state_key=key,fingerprint=projection.fingerprint,
                    schema_version=extraction.schema_version,mapping_version=projection.mapping_version,
                    window_start=extraction.window.start,window_end=extraction.window.end,extracted_at=extraction.extracted_at,
                    export_time=projection.export_time,price_from=projection.price_from,price_to=projection.price_to,
                    row_count=projection.rows,late_rows=projection.late_rows,correction_rows=projection.correction_rows,
                    unmapped_rows=projection.unmapped_rows,evidence_rows=projection.evidence_rows,
                    controls=list(projection.controls),published_at=now(),status='PUBLISHED'))
                await s.flush()
                s.add(CacheRow(generation=generation,summaries=list(projection.groups),prices=list(projection.prices)))
                await s.flush()
            result=await s.execute(update(StateRow).where(StateRow.key==key,StateRow.revision==revision).values(
                generation=generation,last_success=now(),error=None))
            if result.rowcount!=1:raise SourceError('PUBLICATION_FAILED')
            return 'IDENTICAL' if previous and previous.generation==generation else 'PUBLISHED'

    async def current(self,environment,source,scope,include_cache=True,start=None,end=None):
        async with self.sessions() as s:
            columns=(StateRow,PublicationRow,CacheRow) if include_cache else (StateRow,PublicationRow)
            query=select(*columns).outerjoin(PublicationRow,StateRow.generation==PublicationRow.generation)
            if include_cache:query=query.outerjoin(CacheRow,PublicationRow.generation==CacheRow.generation)
            query=query.where(StateRow.environment==environment,StateRow.source==source,StateRow.scope==scope)
            if start is not None:query=query.where(StateRow.partition>=start.isoformat())
            if end is not None:query=query.where(StateRow.partition<end.isoformat())
            # Old manifests remain retained; operational reads load only the latest
            # bounded24month partition set. No deletion or silent ledger mutation.
            query=query.order_by(StateRow.partition.desc()).limit(731)
            rows=list((await s.execute(query)).all())
            return list(reversed(rows)) if include_cache else [(s,p,None) for s,p in reversed(rows)]

    async def history(self,key):
        async with self.sessions() as s:
            return list((await s.scalars(select(PublicationRow).where(PublicationRow.state_key==key).order_by(PublicationRow.published_at).limit(1000))).all())
