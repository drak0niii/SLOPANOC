"""Bounded read-only DTOs. No transport, publisher or projector construction."""
from uuid import UUID
from typing import Literal
from pydantic import AwareDatetime, Field
from sqlalchemy import select, func
from ..schemas import Contract, Count, TraceId, Identifier, Duration
from ..model_context import ModelAgent
from ..stages import RunStatus
from ..projection import utcnow
from .contracts import Environment
from .models import RunProjectionRow,AgentProjectionRow,OutboxRow,ReceiptRow,FailureRow

class RunActivity(Contract):
    run_id: UUID
    turn_id: Identifier | None=None
    trace_id: TraceId | None=None
    status: RunStatus
    aggregate_version: Count
    latest_event_occurred_at: AwareDatetime
    latest_event_processed_at: AwareDatetime
    projection_lag: Duration

class AgentActivity(Contract):
    agent: ModelAgent
    model_attempts_started: Count
    model_attempts_completed: Count
    usage_records_captured: Count
    latest_event_occurred_at: AwareDatetime
    latest_event_processed_at: AwareDatetime
    projection_lag: Duration

class DebtView(Contract):
    pending: Count=0
    leased: Count=0
    retrying: Count=0
    dead: Count=0
    oldest_unpublished_age: Duration | None=None
    bounded_snapshot_truncated: bool=False

class ProcessingView(Contract):
    processed: Count
    duplicate: Count
    conflict: Count
    invalid: Count
    retry: Count
    dead: Count
    bounded_snapshot_truncated: bool=False

class ActivityView(Contract):
    schema_version: Literal[1]=1
    environment: Environment
    as_of: AwareDatetime
    runs: tuple[RunActivity,...]
    agents: tuple[AgentActivity,...]
    debt: DebtView
    processing: ProcessingView
    runs_truncated: bool
    counts_scope: Literal['returned_page_and_bounded_processing_snapshot']='returned_page_and_bounded_processing_snapshot'
    # These are observed commits, not complete historical rates or finance values.
    completed_in_page: Count
    failed_in_page: Count

class ReadRepository:
    def __init__(self,sessions):self.sessions=sessions
    async def activity(self,environment,limit=50):
        if environment not in ('local','test','development','staging','production') or type(limit) is not int or not 1<=limit<=100:
            raise ValueError('Read bounds')
        now=utcnow()
        async with self.sessions() as s:
            runs=(await s.execute(select(RunProjectionRow).where(RunProjectionRow.environment==environment)
                .order_by(RunProjectionRow.latest_event_occurred_at.desc(),RunProjectionRow.run_id).limit(limit+1))).scalars().all()
            agents=(await s.execute(select(AgentProjectionRow).where(AgentProjectionRow.environment==environment)
                .order_by(AgentProjectionRow.agent).limit(32))).scalars().all()
            debt_rows=(await s.execute(select(OutboxRow.publication_state,OutboxRow.created_at)
                .where(OutboxRow.environment==environment,OutboxRow.publication_state!='PUBLISHED')
                .order_by(OutboxRow.created_at).limit(1001))).all()
            receipts=(await s.execute(select(ReceiptRow.duplicate_count).where(ReceiptRow.environment==environment)
                .order_by(ReceiptRow.processed_at.desc()).limit(1001))).scalars().all()
            failures=(await s.execute(select(FailureRow.failure_code,FailureRow.processing_state,FailureRow.attempts)
                .where(FailureRow.environment==environment).limit(1001))).all()
        debt=DebtView(**{state.lower():sum(r[0]==state for r in debt_rows[:1000]) for state in ('PENDING','LEASED','RETRYING','DEAD')},
            oldest_unpublished_age=max(0,(now-debt_rows[0][1]).total_seconds()) if debt_rows else None,
            bounded_snapshot_truncated=len(debt_rows)>1000)
        processing=ProcessingView(processed=min(1000,len(receipts)),duplicate=sum(receipts[:1000]),
            conflict=sum(r[2] for r in failures[:1000] if r[0]=='CONFLICT'),invalid=sum(r[2] for r in failures[:1000] if r[0]=='INVALID'),
            retry=sum(r[1]=='RETRY' for r in failures[:1000]),dead=sum(r[1]=='DEAD' for r in failures[:1000]),
            bounded_snapshot_truncated=len(receipts)>1000 or len(failures)>1000)
        def dto(cls,row):return cls(**{k:getattr(row,k) for k in cls.model_fields})
        return ActivityView(environment=environment,as_of=now,runs=tuple(dto(RunActivity,r) for r in runs[:limit]),
            agents=tuple(dto(AgentActivity,r) for r in agents),debt=debt,processing=processing,runs_truncated=len(runs)>limit,
            completed_in_page=sum(r.status=='COMPLETED' for r in runs[:limit]),
            failed_in_page=sum(r.status in ('FAILED','TIMEOUT') for r in runs[:limit]))
