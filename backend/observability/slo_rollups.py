"""Compact SRE operational receipts and minute aggregates; no business content."""
from datetime import datetime, timedelta, timezone
from typing import Literal
from uuid import UUID
from pydantic import AwareDatetime, Field, field_validator, model_validator
from sqlalchemy import String, BigInteger, Boolean, JSON, Index, select, update, delete, case, func
from sqlalchemy.orm import Mapped, mapped_column
from .models import Base, UTCDateTime
from .schemas import Contract, Count, Duration
from .slo_contract import RequestClass, CancelOrigin, LATENCY_IDS, DEFINITIONS, RECEIPT_GRACE_SECONDS, BURN_WINDOWS, WINDOW_SECONDS
from .slo_evaluator import Counts, Bucket, Snapshot, minute

class Receipt(Contract):
    run_id: str = Field(pattern=r'^[0-9a-f-]{36}$')
    environment: Literal['local','development','staging','production']
    subject_digest: str = Field(pattern=r'^[0-9a-f]{64}$')
    accepted_at: AwareDatetime
    deadline_at: AwareDatetime
    version: Count = 0
    terminal_at: AwareDatetime | None = None
    terminal_status: Literal['COMPLETED','FAILED','TIMEOUT','CANCELLED'] | None = None
    terminal_count: int = Field(default=0,ge=0,le=2)
    valid_outcome: bool = False
    cancel_origin: CancelOrigin = CancelOrigin.UNKNOWN
    request_class: RequestClass = RequestClass.UNKNOWN
    duration_ms: Duration | None = None
    trace_complete: bool = False
    sse_expected: bool = False
    emitted_at: AwareDatetime | None = None
    browser_received: bool = False
    transport: Literal['pending','relay_succeeded','client_disconnected','relay_failed','backpressure_failed','unknown'] = 'pending'
    @field_validator('run_id')
    @classmethod
    def canonical(cls,value):
        if str(UUID(value))!=value: raise ValueError('Invalid run correlation')
        return value
    @model_validator(mode='after')
    def times(self):
        if self.deadline_at<self.accepted_at or self.terminal_at is not None and self.terminal_at<self.accepted_at:
            raise ValueError('Invalid receipt chronology')
        return self

class SREReceiptRow(Base):
    __tablename__='observability_sre_receipt'
    run_id: Mapped[str] = mapped_column(String(36),primary_key=True)
    environment: Mapped[str] = mapped_column(String(16))
    due_at: Mapped[object] = mapped_column(UTCDateTime)
    payload: Mapped[dict] = mapped_column(JSON)
    contribution: Mapped[dict] = mapped_column(JSON)
    settled: Mapped[bool] = mapped_column(Boolean,default=False)
    __table_args__=(Index('ix_sre_due','settled','due_at'),)

class SREBucketRow(Base):
    __tablename__='observability_sre_bucket'
    environment: Mapped[str] = mapped_column(String(16),primary_key=True)
    slo_id: Mapped[str] = mapped_column(String(64),primary_key=True)
    at: Mapped[object] = mapped_column(UTCDateTime,primary_key=True)
    good: Mapped[int] = mapped_column(BigInteger,default=0)
    bad: Mapped[int] = mapped_column(BigInteger,default=0)
    unknown: Mapped[int] = mapped_column(BigInteger,default=0)
    excluded: Mapped[int] = mapped_column(BigInteger,default=0)

class SRESourceRow(Base):
    __tablename__='observability_sre_source'
    environment: Mapped[str] = mapped_column(String(16),primary_key=True)
    coverage_start: Mapped[object] = mapped_column(UTCDateTime)
    updated_at: Mapped[object] = mapped_column(UTCDateTime)
    capture_complete: Mapped[bool] = mapped_column(Boolean,default=True)


def contributions(receipt, now):
    """No lifecycle transition: an overdue missing receipt is an SRE bad event only."""
    result={}
    def add(slo_id, at, kind):
        result[slo_id]={'at':minute(at).isoformat(),'good':int(kind=='good'),'bad':int(kind=='bad'),
            'unknown':int(kind=='unknown'),'excluded':int(kind=='excluded')}
    closed=receipt.terminal_at is not None
    if not closed and now<receipt.deadline_at: return result
    timely=closed and receipt.terminal_count==1 and receipt.terminal_at<=receipt.deadline_at
    add('terminal_completion',receipt.accepted_at,'good' if timely else 'bad')
    cancelled=receipt.terminal_status=='CANCELLED'
    if cancelled and receipt.cancel_origin==CancelOrigin.USER: kind='excluded'
    else: kind='good' if closed and receipt.valid_outcome and receipt.terminal_status=='COMPLETED' else 'bad'
    add('availability',receipt.accepted_at,kind)
    if closed:
        add('trace_completeness',receipt.terminal_at,'good' if receipt.trace_complete and receipt.terminal_count==1 else 'bad')
        if receipt.request_class in LATENCY_IDS:
            slo=LATENCY_IDS[receipt.request_class]; threshold=DEFINITIONS[slo].threshold_seconds*1000
            add(slo,receipt.terminal_at,'unknown' if receipt.duration_ms is None else 'good' if receipt.duration_ms<=threshold else 'bad')
        else:
            # Unknown class must remain visible in each class's coverage, never be guessed.
            for slo in LATENCY_IDS.values(): add(slo,receipt.terminal_at,'unknown')
    if receipt.sse_expected:
        if receipt.transport=='client_disconnected' and receipt.emitted_at is None:
            add('sse_delivery',receipt.accepted_at,'excluded')
        elif receipt.transport in ('relay_failed','backpressure_failed'):
            add('sse_delivery',receipt.emitted_at or receipt.accepted_at,'bad')
        elif receipt.emitted_at is not None:
            if receipt.browser_received: add('sse_delivery',receipt.emitted_at,'good')
            elif now>=receipt.emitted_at+timedelta(seconds=RECEIPT_GRACE_SECONDS):
                # Missing ack is delivery-observation bad; reason is not server/business failure.
                add('sse_delivery',receipt.emitted_at,'bad' if receipt.transport=='relay_succeeded' else 'unknown')
    return result

class Rollups:
    def __init__(self,sessions): self.sessions=sessions
    async def _increment(self,session,environment,slo_id,at,counts):
        from sqlalchemy.dialects.postgresql import insert as pg_insert
        from sqlalchemy.dialects.sqlite import insert as sqlite_insert
        insert=pg_insert if session.bind.dialect.name=='postgresql' else sqlite_insert
        values=dict(environment=environment,slo_id=slo_id,at=minute(at),**counts)
        statement=insert(SREBucketRow).values(**values)
        statement=statement.on_conflict_do_update(index_elements=['environment','slo_id','at'],set_={k:getattr(SREBucketRow,k)+v for k,v in counts.items()})
        await session.execute(statement)

    async def _delta(self,session,environment,old,new):
        for sign,items in ((-1,old),(1,new)):
            for slo,item in items.items():
                await self._increment(session,environment,slo,datetime.fromisoformat(item['at']),{k:sign*item[k] for k in ('good','bad','unknown','excluded')})

    async def save(self,receipt,now):
        async with self.sessions() as session, session.begin():
            # Insert shell atomically, then lock. Cross-instance duplicate acceptance is harmless.
            from sqlalchemy.dialects.postgresql import insert as pg_insert
            from sqlalchemy.dialects.sqlite import insert as sqlite_insert
            insert=pg_insert if session.bind.dialect.name=='postgresql' else sqlite_insert
            await session.execute(insert(SREReceiptRow).values(run_id=receipt.run_id,environment=receipt.environment,
                due_at=receipt.deadline_at,payload=receipt.model_dump(mode='json'),contribution={},settled=False).on_conflict_do_nothing(index_elements=['run_id']))
            row=(await session.execute(select(SREReceiptRow).where(SREReceiptRow.run_id==receipt.run_id).with_for_update())).scalar_one()
            previous=Receipt.model_validate(row.payload)
            if (previous.environment,previous.accepted_at,previous.subject_digest)!=(receipt.environment,receipt.accepted_at,receipt.subject_digest):
                raise ValueError('Receipt identity changed')
            if receipt.version<previous.version: return
            # Browser ack is owned by the receipt endpoint and cannot be undone by a relay update.
            receipt=receipt.model_copy(update={'browser_received':previous.browser_received or receipt.browser_received})
            new=contributions(receipt,now)
            await self._delta(session,receipt.environment,row.contribution,new)
            row.payload=receipt.model_dump(mode='json');row.contribution=new
            row.due_at=max(receipt.deadline_at,(receipt.emitted_at or receipt.deadline_at)+timedelta(seconds=RECEIPT_GRACE_SECONDS))
            row.settled=bool(receipt.terminal_at and (not receipt.sse_expected or 'sse_delivery' in new))

    async def settle(self,now,limit=256):
        async with self.sessions() as session,session.begin():
            rows=(await session.execute(select(SREReceiptRow).where(SREReceiptRow.settled==False,SREReceiptRow.due_at<=now).order_by(SREReceiptRow.due_at).limit(limit).with_for_update(skip_locked=True))).scalars().all()
            for row in rows:
                receipt=Receipt.model_validate(row.payload);new=contributions(receipt,now)
                await self._delta(session,row.environment,row.contribution,new)
                row.contribution=new;row.settled=True
            return len(rows)

    async def record(self,environment,slo_id,at,counts):
        if slo_id not in DEFINITIONS: raise ValueError('Unregistered SLI')
        async with self.sessions() as session,session.begin():
            await self._increment(session,environment,slo_id,at,{k:getattr(counts,k) for k in ('good','bad','unknown','excluded')})

    async def heartbeat(self,environment,now,complete=True):
        async with self.sessions() as session,session.begin():
            from sqlalchemy.dialects.postgresql import insert as pg_insert
            from sqlalchemy.dialects.sqlite import insert as sqlite_insert
            insert=pg_insert if session.bind.dialect.name=='postgresql' else sqlite_insert
            await session.execute(insert(SRESourceRow).values(environment=environment,coverage_start=now,updated_at=now,capture_complete=complete).on_conflict_do_nothing(index_elements=['environment']))
            row=(await session.execute(select(SRESourceRow).where(SRESourceRow.environment==environment).with_for_update())).scalar_one()
            if (now-row.updated_at).total_seconds()>300: row.coverage_start=now
            if not complete:
                # A capture gap invalidates history from this point. After a clean
                # producer restart, a full new 28-day window can recover without
                # rewriting historical buckets or requiring a database reset.
                row.coverage_start=now;row.capture_complete=False
            elif not row.capture_complete and now-row.coverage_start>=timedelta(days=28):
                row.capture_complete=True
            row.updated_at=now

    async def snapshot(self,environment,slo_id,now,lag_seconds):
        end=minute(now-timedelta(seconds=lag_seconds));start=end-timedelta(days=28)
        async with self.sessions() as session:
            source=await session.get(SRESourceRow,environment)
            if source is None: return Snapshot(environment,available=False,source='RUNTIME_ROLLUPS',watermark=end)
            # Disjoint canonical ranges preserve exact nested-window counts while
            # bounding the read to six rows, rather than materializing 40,320 minutes.
            ranges=sorted(set((*BURN_WINDOWS, WINDOW_SECONDS)))
            band=case(*[(SREBucketRow.at>=end-timedelta(seconds=seconds),seconds) for seconds in ranges],else_=WINDOW_SECONDS)
            rows=(await session.execute(select(band.label('seconds'),*[func.sum(getattr(SREBucketRow,key)) for key in ('good','bad','unknown','excluded')]).where(SREBucketRow.environment==environment,SREBucketRow.slo_id==slo_id,SREBucketRow.at>=start,SREBucketRow.at<end).group_by(band))).all()
            buckets=tuple(Bucket(end-timedelta(seconds=row[0]),Counts(*row[1:])) for row in rows)
            return Snapshot(environment,buckets,source.updated_at,source.coverage_start,True,source.capture_complete,'RUNTIME_ROLLUPS',end)

    async def receive(self,run_id,subject_digest,environments,now):
        async with self.sessions() as session,session.begin():
            row=(await session.execute(select(SREReceiptRow).where(SREReceiptRow.run_id==run_id,SREReceiptRow.environment.in_(environments)).with_for_update())).scalar_one_or_none()
            if row is None: return False
            receipt=Receipt.model_validate(row.payload)
            if receipt.subject_digest!=subject_digest or not receipt.sse_expected or receipt.emitted_at is None or now<receipt.emitted_at or now>receipt.emitted_at+timedelta(minutes=5): return False
            if receipt.browser_received: return True
            receipt=receipt.model_copy(update={'browser_received':True})
            new=contributions(receipt,now);await self._delta(session,row.environment,row.contribution,new)
            row.payload=receipt.model_dump(mode='json');row.contribution=new
            return True

    async def prune(self,now):
        # Operational correlation shorter than aggregate history; never touches M7 or ADK.
        async with self.sessions() as session,session.begin():
            await session.execute(delete(SREReceiptRow).where(SREReceiptRow.settled==True,SREReceiptRow.due_at<now-timedelta(days=2)))
            await session.execute(delete(SREBucketRow).where(SREBucketRow.at<now-timedelta(days=35)))
